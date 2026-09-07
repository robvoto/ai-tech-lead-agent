"""Discover a target project's canonical validation command (ATL-039).

The AI Tech Lead must decide completion from its own validation run, not from the
coding agent's prose. To do that it needs the project's canonical validation
command *before* the coding-agent handoff. This module finds that command without
hardcoding any project's tooling and without a per-project settings key:

1. Documented — an explicit line in the project's own authoritative entry points
   (``AGENTS.md``, ``docs/INDEX.md`` and its one-hop references, ``.skills``
   index and its best-matching skill). This is the preferred source; a project
   states its own command.
2. Inferred — exactly one unambiguous repository signal (a pytest project, an
   ``npm test`` script, a ``make test`` target, ``tox``/``nox``). Deterministic
   file inspection only, never an LLM guess. Zero signals or more than one
   conflicting signal returns nothing.
3. Nothing — the caller escalates to a human at the completion gate rather than
   guessing.

Every returned command is shell-safe and ``shlex``-splittable so the caller can
run it with ``shell=False``.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .project_guidance_discovery import (
    _best_matching_skill,
    _referenced_docs_from_index,
)
from .research_sources import _request_terms

_MAX_COMMAND_CHARS = 160
_UNSAFE_COMMAND_CHARS = frozenset(";|&`$><\n\r*?")
# A documented command is only trusted when its first token is a recognised
# runner. This is a safety allowlist for "what ATL will execute unattended", not
# a statement about which tools a project may use — anything outside it falls
# through to the human-escalation path.
_ALLOWED_COMMAND_HEADS = frozenset(
    {"uv", "uvx", "python", "python3", "pytest", "poetry", "hatch", "tox", "nox",
     "make", "just", "npm", "pnpm", "yarn", "bun", "go", "cargo", "bundle",
     "rake", "gradle", "mvn", "dotnet", "pdm", "rye"}
)

# Lines that declare a validation/test command in project docs. The command is
# taken from a backtick span on the same line; the keyword just marks intent.
_DECLARATION_KEYWORDS = (
    "validation command",
    "core validation",
    "validate with",
    "run validation",
    "test command",
    "run the test",
    "run tests with",
    "how to run the test",
)
_BACKTICK_SPAN_RE = re.compile(r"`([^`\n]{2,200})`")


@dataclass(frozen=True)
class ValidationCommand:
    """A canonical validation command plus where it was found."""

    command: str
    source: str  # "documented: <relpath>" | "inferred: <signal>"


def discover_validation_command(
    request: str, project_root: str
) -> ValidationCommand | None:
    """Return the project's canonical validation command, or ``None``.

    ``request`` is used only to rank which one-hop documentation pages and which
    skill file to read (same bounded, index-driven selection as project-guidance
    discovery); it never changes which command is chosen.
    """

    if not project_root:
        return None
    root = Path(project_root)
    if not root.is_dir():
        return None

    documented = _documented_command(request, root)
    if documented is not None:
        return documented
    return _inferred_command(root)


# --- documented -------------------------------------------------------------


def _documented_command(request: str, root: Path) -> ValidationCommand | None:
    request_terms = _request_terms(request)
    for relative_path, text in _candidate_documents(request, root, request_terms):
        command = _command_from_declaration(text)
        if command is not None:
            return ValidationCommand(command=command, source=f"documented: {relative_path}")
    return None


def _candidate_documents(
    request: str, root: Path, request_terms: set[str]
) -> list[tuple[str, str]]:
    """The same small, index-driven file set project-guidance discovery reads.

    Read in full here (not truncated) because a validation-command line can sit
    anywhere in the file, but the file *set* stays bounded to the project's
    declared entry points — no repository scan.
    """

    documents: list[tuple[str, str]] = []

    agents_path = root / "AGENTS.md"
    if agents_path.is_file():
        documents.append(("AGENTS.md", _read_text(agents_path)))

    docs_index_path = root / "docs" / "INDEX.md"
    if docs_index_path.is_file():
        docs_index_text = _read_text(docs_index_path)
        documents.append(("docs/INDEX.md", docs_index_text))
        for relative_path, document_text in _referenced_docs_from_index(
            root, docs_index_path, docs_index_text, request_terms
        ):
            documents.append((relative_path, document_text))

    skills_index_path = root / ".skills" / "INDEX.md"
    if skills_index_path.is_file():
        index_text = _read_text(skills_index_path)
        documents.append((".skills/INDEX.md", index_text))
        best_skill = _best_matching_skill(index_text, skills_index_path, request_terms)
        if best_skill is not None:
            relative_path, _summary, skill_text = best_skill
            documents.append((f".skills/{relative_path}", skill_text))

    return documents


def _command_from_declaration(text: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not any(keyword in lowered for keyword in _DECLARATION_KEYWORDS):
            continue
        for span in _BACKTICK_SPAN_RE.findall(line):
            command = _sanitise_command(span)
            if command is not None:
                return command
    return None


# --- inferred --------------------------------------------------------------


def _inferred_command(root: Path) -> ValidationCommand | None:
    """Return a command only when exactly one unambiguous signal is present."""

    signals: list[tuple[str, str]] = []  # (signal label, command)

    pytest_signal = _pytest_signal(root)
    if pytest_signal is not None:
        signals.append(pytest_signal)
    npm_signal = _npm_test_signal(root)
    if npm_signal is not None:
        signals.append(npm_signal)
    if _makefile_has_test_target(root):
        signals.append(("Makefile test target", "make test"))
    if (root / "tox.ini").is_file():
        signals.append(("tox.ini", "tox"))
    if (root / "noxfile.py").is_file():
        signals.append(("noxfile.py", "nox"))

    if len(signals) != 1:
        return None
    label, command = signals[0]
    return ValidationCommand(command=command, source=f"inferred: {label}")


def _pytest_signal(root: Path) -> tuple[str, str] | None:
    pyproject = root / "pyproject.toml"
    has_pytest_marker = False
    if pyproject.is_file():
        pyproject_text = _read_text(pyproject)
        has_pytest_marker = "[tool.pytest" in pyproject_text or "pytest" in pyproject_text
    tests_dir = root / "tests"
    has_test_files = tests_dir.is_dir() and any(tests_dir.glob("test_*.py"))
    if not (has_pytest_marker or has_test_files):
        return None
    if (root / "uv.lock").is_file():
        return ("uv + pytest project", "uv run pytest")
    return ("pytest project", "pytest")


def _npm_test_signal(root: Path) -> tuple[str, str] | None:
    package_json = root / "package.json"
    if not package_json.is_file():
        return None
    try:
        data = json.loads(_read_text(package_json))
    except (ValueError, TypeError):
        return None
    script = str((data.get("scripts") or {}).get("test", "")).strip()
    if not script or "no test specified" in script.lower():
        return None
    return ("package.json test script", "npm test")


def _makefile_has_test_target(root: Path) -> bool:
    makefile = root / "Makefile"
    if not makefile.is_file():
        return False
    return re.search(r"(?m)^test\s*:", _read_text(makefile)) is not None


# --- shared --------------------------------------------------------------


def _sanitise_command(raw: str) -> str | None:
    command = " ".join(raw.split())
    if not command or len(command) > _MAX_COMMAND_CHARS:
        return None
    if any(char in _UNSAFE_COMMAND_CHARS for char in command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] not in _ALLOWED_COMMAND_HEADS:
        return None
    return command


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
