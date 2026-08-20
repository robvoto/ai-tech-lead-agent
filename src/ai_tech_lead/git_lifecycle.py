"""Authoritative Git lifecycle operations for coding-task execution.

The workflow owns the approval boundary; this module owns the Git mechanics
behind that boundary.  It never force-pushes, silently resolves conflicts, or
deletes task worktrees.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .logging_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

TARGET_BRANCH = "main"
REMOTE = "origin"
_GIT_TIMEOUT_SECONDS = 30
# The default is resolved beside the canonical checkout, never inside it, so
# task allocations cannot make the canonical repository appear dirty.
_WORKTREE_ROOT: Path | None = None


class GitLifecycleError(RuntimeError):
    """A Git lifecycle operation failed or was not safe to continue."""


class GitLifecycleBlocked(GitLifecycleError):
    """Human review is required before the lifecycle can continue."""


@dataclass(frozen=True)
class TaskGitState:
    """Bounded, checkpoint-friendly Git state for one coding request."""

    canonical_root: str
    worktree: str
    branch: str
    base_sha: str
    tip_sha: str = ""
    task_commit_sha: str = ""
    branch_pushed: bool = False
    main_status: str = ""
    main_sha: str = ""
    integration_status: str = ""
    integration_reason: str = ""


@dataclass(frozen=True)
class GitCommandResult:
    """Small internal wrapper that preserves bounded Git diagnostics."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def not_in_main_status(branch: str) -> str:
    return f"MAIN STATUS: NOT IN MAIN — pushed branch {branch}"


def in_main_status(main_sha: str) -> str:
    return f"MAIN STATUS: IN MAIN — verified on origin/main at {main_sha}"


def parse_integration_approval(text: str) -> bool:
    """Recognise only the explicitly documented natural-language approvals.

    This is an explicit syntax parser, not a fuzzy semantic classifier.  Any
    other text remains non-authorising and must use the structured approval
    option exposed by the caller contract.
    """

    normalized = " ".join(text.split()).casefold()
    return normalized in {"approved", "merge it", "put it in main", "ship it"}


def prepare_task_worktree(
    canonical_root: Path,
    request_id: str,
    *,
    existing: TaskGitState | None = None,
) -> TaskGitState:
    """Create or validate one request-owned worktree from ``origin/main``."""

    canonical_root = _repo_root(canonical_root)
    if existing is not None:
        if Path(existing.canonical_root).resolve() != canonical_root:
            raise GitLifecycleBlocked("The recorded canonical repository does not match this task.")
        _validate_existing_allocation(existing)
        return existing

    if not request_id.strip():
        raise GitLifecycleBlocked("A request_id is required to allocate a task worktree.")

    _git(canonical_root, "fetch", REMOTE)
    _require_ref(canonical_root, f"{REMOTE}/{TARGET_BRANCH}")
    base_sha = _rev_parse(canonical_root, f"{REMOTE}/{TARGET_BRANCH}")

    request_key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
    branch = f"atl/task-{request_key}"
    worktree_root = _WORKTREE_ROOT or (
        canonical_root.parent / f".{canonical_root.name}-atl-worktrees"
    )
    worktree = worktree_root / request_key
    if worktree.exists():
        raise GitLifecycleBlocked(
            f"A task worktree already exists at {worktree}; ownership is ambiguous."
        )

    worktree_root.mkdir(parents=True, exist_ok=True)
    _git(
        canonical_root,
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        f"{REMOTE}/{TARGET_BRANCH}",
    )
    try:
        _git(worktree, "worktree", "lock", str(worktree))
    except GitLifecycleError:
        # The worktree is owned by this request, so remove only the allocation
        # we just created if locking failed.  No existing allocation is touched.
        _git(canonical_root, "worktree", "remove", "--force", str(worktree))
        raise

    logger.info(
        "Allocated task worktree request_id=%s branch=%s base=%s path=%s",
        request_id,
        branch,
        base_sha,
        worktree,
    )
    return TaskGitState(
        canonical_root=str(canonical_root),
        worktree=str(worktree),
        branch=branch,
        base_sha=base_sha,
    )


def commit_and_push_task_branch(task: TaskGitState) -> TaskGitState:
    """Commit validated task changes and push the task branch without force."""

    worktree = Path(task.worktree).resolve()
    _validate_existing_allocation(task)
    status = _git(worktree, "status", "--porcelain=v1").stdout.strip()
    if status:
        _git(worktree, "add", "--all")
        _git(
            worktree,
            "commit",
            "-m",
            f"atl: complete task {task.branch.removeprefix('atl/task-')}",
        )

    tip_sha = _rev_parse(worktree, "HEAD")
    if tip_sha == task.base_sha:
        raise GitLifecycleError("Validated coding work produced no commit beyond origin/main.")

    _git(worktree, "push", "--set-upstream", REMOTE, task.branch)
    logger.info("Pushed validated task branch=%s commit=%s", task.branch, tip_sha)
    return TaskGitState(
        **{
            **task.__dict__,
            "tip_sha": tip_sha,
            "task_commit_sha": tip_sha,
            "branch_pushed": True,
            "main_status": not_in_main_status(task.branch),
            "integration_status": "awaiting_approval",
            "integration_reason": (
                "Validated task branch is pushed and awaits explicit integration approval."
            ),
        }
    )


def integrate_task_branch(
    task: TaskGitState,
    *,
    validate_integrated: Callable[[Path], None],
) -> TaskGitState:
    """Reconcile, validate, push, and verify one task branch into ``main``."""

    if not task.branch_pushed or not task.task_commit_sha:
        raise GitLifecycleError("Cannot integrate before the validated task branch is pushed.")
    canonical_root = Path(task.canonical_root).resolve()
    worktree = Path(task.worktree).resolve()
    _validate_existing_allocation(task)

    _git(canonical_root, "fetch", REMOTE)
    _reconcile_task_branch(task, worktree)
    _git(canonical_root, "fetch", REMOTE)
    _sync_canonical_main(canonical_root)

    try:
        _git(canonical_root, "merge", "--no-ff", "--no-edit", task.branch)
    except GitLifecycleError as error:
        _abort_merge(canonical_root)
        raise GitLifecycleBlocked(
            f"Integration conflict while merging {task.branch} into {TARGET_BRANCH}; "
            "the merge was aborted and needs human reconciliation."
        ) from error

    try:
        validate_integrated(canonical_root)
    except Exception:
        _abort_merge(canonical_root)
        raise

    _git(canonical_root, "push", REMOTE, TARGET_BRANCH)
    _git(canonical_root, "fetch", REMOTE)
    if not _is_ancestor(canonical_root, task.task_commit_sha, f"{REMOTE}/{TARGET_BRANCH}"):
        raise GitLifecycleError(
            f"Push completed but task commit {task.task_commit_sha} is not contained in "
            f"{REMOTE}/{TARGET_BRANCH}."
        )
    main_sha = _rev_parse(canonical_root, f"{REMOTE}/{TARGET_BRANCH}")
    logger.info("Verified task commit=%s in origin/main=%s", task.task_commit_sha, main_sha)
    return TaskGitState(
        **{
            **task.__dict__,
            "tip_sha": _rev_parse(worktree, "HEAD"),
            "main_status": in_main_status(main_sha),
            "main_sha": main_sha,
            "integration_status": "integrated",
            "integration_reason": "Task commit is an ancestor of origin/main.",
        }
    )


def validate_integrated_git_result(canonical_root: Path) -> None:
    """Run the universal required validation available at the Git boundary."""

    _git(canonical_root, "diff", "--check", f"{REMOTE}/{TARGET_BRANCH}...HEAD")


def _reconcile_task_branch(task: TaskGitState, worktree: Path) -> None:
    current_main = _rev_parse(worktree, f"{REMOTE}/{TARGET_BRANCH}")
    if current_main == task.base_sha or _is_ancestor(worktree, current_main, task.task_commit_sha):
        return
    try:
        _git(worktree, "merge", "--no-edit", f"{REMOTE}/{TARGET_BRANCH}")
    except GitLifecycleError as error:
        _abort_merge(worktree)
        raise GitLifecycleBlocked(
            f"{REMOTE}/{TARGET_BRANCH} advanced since the task branch was created and "
            "the task cannot be reconciled without human conflict resolution."
        ) from error
    _git(worktree, "push", REMOTE, task.branch)


def _sync_canonical_main(canonical_root: Path) -> None:
    branch = _rev_parse(canonical_root, "--abbrev-ref", "HEAD")
    if branch != TARGET_BRANCH:
        raise GitLifecycleBlocked(
            f"Canonical repository is on '{branch}', not '{TARGET_BRANCH}'; ownership is ambiguous."
        )
    if _git(canonical_root, "status", "--porcelain=v1").stdout.strip():
        raise GitLifecycleBlocked(
            "Canonical main has local changes; integration stopped to protect unrelated work."
        )
    _git(canonical_root, "merge", "--ff-only", f"{REMOTE}/{TARGET_BRANCH}")


def _validate_existing_allocation(task: TaskGitState) -> None:
    worktree = Path(task.worktree).resolve()
    if not worktree.is_dir():
        raise GitLifecycleBlocked(f"Recorded task worktree is unavailable: {worktree}")
    branch = _rev_parse(worktree, "--abbrev-ref", "HEAD")
    if branch != task.branch:
        raise GitLifecycleBlocked(
            f"Recorded task worktree is on '{branch}', expected owned branch '{task.branch}'."
        )
    status = _git(worktree, "status", "--porcelain=v1").stdout.splitlines()
    if any(line[:2] in {"UU", "AA", "DD", "AU", "UA", "DU", "UD"} for line in status):
        raise GitLifecycleBlocked(
            "The task worktree contains unresolved merge conflicts; human reconciliation "
            "is required."
        )


def _repo_root(path: Path) -> Path:
    root = _rev_parse(path, "--show-toplevel")
    return Path(root).resolve()


def _require_ref(cwd: Path, ref: str) -> None:
    _git(cwd, "show-ref", "--verify", "--quiet", f"refs/remotes/{ref}")


def _rev_parse(cwd: Path, *args: str) -> str:
    return _git(cwd, "rev-parse", *args).stdout.strip()


def _is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    result = _run_git(cwd, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode not in (0, 1):
        raise GitLifecycleError(result.stderr or "git merge-base failed")
    return result.returncode == 0


def _abort_merge(cwd: Path) -> None:
    result = _run_git(cwd, "merge", "--abort")
    if result.returncode != 0:
        logger.warning("Could not abort Git merge in %s: %s", cwd, result.stderr.strip())


def _git(cwd: Path, *args: str) -> GitCommandResult:
    result = _run_git(cwd, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise GitLifecycleError(f"git {' '.join(result.args)} failed: {detail[:800]}")
    return result


def _run_git(cwd: Path, *args: str) -> GitCommandResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitLifecycleError(f"git {' '.join(args)} could not run: {error}") from error
    return GitCommandResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
