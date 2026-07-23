"""Workspace bootstrap and health checks for the AI Tech Lead repo."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ai_tech_lead.app_settings import AppSettings, load_settings
from ai_tech_lead.backlog_repository import MarkdownBacklogRepository
from ai_tech_lead.backlog_store import SqliteBacklogRepository
from ai_tech_lead.checkpointer_store import get_checkpointer
from ai_tech_lead.config import DATA_DIR, LOGS_DIR, PROJECT_ROOT, SETTINGS_PATH, ensure_project_dirs
from ai_tech_lead.knowledge_store import (
    get_knowledge_store,
    get_knowledge_store_statistics,
)
from ai_tech_lead.storage import initialize_database


@dataclass(frozen=True)
class WorkspaceHealthReport:
    ok: bool
    lines: list[str]


def bootstrap_workspace(settings_path: Path = SETTINGS_PATH) -> list[str]:
    """Create local runtime files and directories needed for a fresh clone."""

    ensure_project_dirs()
    archive_dir = DATA_DIR / "backlog" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"Created/checked directories under {DATA_DIR} and {LOGS_DIR}."]

    if not settings_path.exists():
        example_path = settings_path.with_name("coding_agent_settings.example.json")
        if not example_path.exists():
            raise FileNotFoundError(f"Example settings file not found: {example_path}")
        shutil.copy2(example_path, settings_path)
        lines.append(f"Created settings file from example: {settings_path}")
    else:
        lines.append(f"Settings file already exists: {settings_path}")

    settings = load_settings(settings_path)
    initialize_database()

    backlog_path = _resolve_project_path(settings.project_root, settings.backlog_path)
    if backlog_path.suffix.lower() == ".sqlite3":
        SqliteBacklogRepository(backlog_path)
        lines.append(f"Initialised backlog store: {backlog_path}")
    else:
        MarkdownBacklogRepository(backlog_path)
        lines.append(f"Validated markdown backlog file: {backlog_path}")

    knowledge_store_path = _resolve_project_path(
        settings.project_root,
        settings.knowledge_store_path,
    )
    get_knowledge_store(knowledge_store_path).batch([])
    lines.append(f"Initialised knowledge store: {knowledge_store_path}")

    get_checkpointer()
    lines.append("Initialised app database and checkpointer store.")
    lines.append(
        "Next step: run `uv run pytest` and then start the app with "
        "`uv run python -m ai_tech_lead --debug`."
    )
    return lines


def doctor_workspace(settings_path: Path = SETTINGS_PATH) -> WorkspaceHealthReport:
    """Report the most important local workspace health checks."""

    messages: list[str] = []
    ok = True

    def note(prefix: str, text: str) -> None:
        messages.append(f"{prefix}: {text}")

    if settings_path.exists():
        note("OK", f"Settings file present: {settings_path}")
    else:
        note("ERROR", f"Settings file missing: {settings_path}")
        ok = False

    example_path = settings_path.with_name("coding_agent_settings.example.json")
    if example_path.exists():
        note("OK", f"Settings example present: {example_path}")
    else:
        note("ERROR", f"Settings example missing: {example_path}")
        ok = False

    if not (DATA_DIR / "prompts.json").exists():
        note("ERROR", f"Prompt registry missing: {DATA_DIR / 'prompts.json'}")
        ok = False
    else:
        note("OK", f"Prompt registry present: {DATA_DIR / 'prompts.json'}")

    if not (PROJECT_ROOT / "docs" / "INDEX.md").exists():
        note("ERROR", "docs/INDEX.md missing")
        ok = False
    else:
        note("OK", "docs/INDEX.md present")

    settings = _load_settings_if_available(settings_path)
    if settings is not None:
        backlog_path = _resolve_project_path(settings.project_root, settings.backlog_path)
        knowledge_store_path = _resolve_project_path(
            settings.project_root,
            settings.knowledge_store_path,
        )

        note(
            "OK" if backlog_path.exists() else "WARN",
            f"Backlog store: {backlog_path}",
        )
        note(
            "OK" if knowledge_store_path.exists() else "WARN",
            f"Knowledge store: {knowledge_store_path}",
        )
        note(
            "OK" if (DATA_DIR / "ai_tech_lead.sqlite3").exists() else "WARN",
            f"Runtime DB: {DATA_DIR / 'ai_tech_lead.sqlite3'}",
        )
        note(
            "OK" if (DATA_DIR / "checkpoints.sqlite3").exists() else "WARN",
            f"Checkpoint DB: {DATA_DIR / 'checkpoints.sqlite3'}",
        )

        try:
            knowledge_stats = get_knowledge_store_statistics(knowledge_store_path)
        except Exception as error:
            note("ERROR", f"Knowledge store stats unavailable: {error}")
            ok = False
            knowledge_stats = None
        if knowledge_stats is not None:
            note(
                "OK",
                "Knowledge stats: "
                f"items={knowledge_stats['item_count']} "
                f"namespaces={knowledge_stats['namespace_count']} "
                f"size_bytes={knowledge_stats['size_bytes']}",
            )

        for unsafe_path in _unsafe_runtime_paths(knowledge_store_path, backlog_path):
            if _is_git_tracked(unsafe_path):
                note("ERROR", f"Tracked runtime file should be ignored: {unsafe_path}")
                ok = False
            else:
                note("OK", f"Runtime file is untracked as expected: {unsafe_path}")
    else:
        note("WARN", "Settings could not be loaded, so runtime-path checks were skipped.")
        ok = False

    return WorkspaceHealthReport(ok=ok, lines=messages)


def _load_settings_if_available(settings_path: Path) -> AppSettings | None:
    try:
        return load_settings(settings_path)
    except (FileNotFoundError, ValueError):
        return None


def _resolve_project_path(project_root: str, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return Path(project_root) / path


def _unsafe_runtime_paths(
    knowledge_store_path: Path,
    backlog_path: Path,
) -> list[Path]:
    runtime_paths = [
        SETTINGS_PATH,
        DATA_DIR / "ai_tech_lead.sqlite3",
        DATA_DIR / "checkpoints.sqlite3",
        DATA_DIR / "backlog.sqlite3",
        knowledge_store_path,
    ]
    if backlog_path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}:
        runtime_paths.append(backlog_path)
    return [path for path in runtime_paths if path.exists()]


def _is_git_tracked(path: Path) -> bool:
    relative_path = path.relative_to(PROJECT_ROOT)
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "--error-unmatch", str(relative_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0
