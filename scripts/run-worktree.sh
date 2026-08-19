#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMON_GIT_DIR="$(git -C "$WORKTREE_ROOT" rev-parse --path-format=absolute --git-common-dir)"
PRIMARY_ROOT="$(dirname "$COMMON_GIT_DIR")"

if [[ -n "${AI_TECH_LEAD_PYTHON:-}" ]]; then
  PYTHON_BIN="$AI_TECH_LEAD_PYTHON"
elif [[ -x "$WORKTREE_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$WORKTREE_ROOT/.venv/bin/python"
elif [[ -x "$PRIMARY_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$PRIMARY_ROOT/.venv/bin/python"
else
  echo "Error: no AI Tech Lead Python environment found." >&2
  echo "Expected .venv in either $WORKTREE_ROOT or $PRIMARY_ROOT." >&2
  echo "Alternatively set AI_TECH_LEAD_PYTHON to the Python executable to use." >&2
  exit 1
fi

SETTINGS_PATH="$WORKTREE_ROOT/data/coding_agent_settings.json"
PRIMARY_SETTINGS_PATH="$PRIMARY_ROOT/data/coding_agent_settings.json"

if [[ ! -f "$SETTINGS_PATH" ]]; then
  if [[ ! -f "$PRIMARY_SETTINGS_PATH" ]]; then
    echo "Error: worktree settings are missing and no primary settings file exists at:" >&2
    echo "  $PRIMARY_SETTINGS_PATH" >&2
    exit 1
  fi

  "$PYTHON_BIN" - "$PRIMARY_SETTINGS_PATH" "$SETTINGS_PATH" "$PRIMARY_ROOT" "$WORKTREE_ROOT" <<'PY'
import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
primary_root = str(Path(sys.argv[3]).resolve())
worktree_root = str(Path(sys.argv[4]).resolve())

settings = json.loads(source_path.read_text(encoding="utf-8"))
settings["project_root"] = worktree_root

allowed_roots = settings.get("allowed_project_roots")
if isinstance(allowed_roots, list):
    settings["allowed_project_roots"] = [
        worktree_root if str(Path(root).resolve()) == primary_root else root
        for root in allowed_roots
    ]

legacy_allowed_roots = settings.get("army_allowed_project_roots")
if isinstance(legacy_allowed_roots, list):
    settings["army_allowed_project_roots"] = [
        worktree_root if str(Path(root).resolve()) == primary_root else root
        for root in legacy_allowed_roots
    ]

project_registry = settings.get("project_registry")
if isinstance(project_registry, list):
    for entry in project_registry:
        if not isinstance(entry, dict):
            continue
        root = entry.get("root")
        if isinstance(root, str) and str(Path(root).resolve()) == primary_root:
            entry["root"] = worktree_root

target_path.parent.mkdir(parents=True, exist_ok=True)
target_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
print(f"Created worktree-local settings: {target_path}")
PY
fi

export PYTHONPATH="$WORKTREE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$WORKTREE_ROOT"

ACTUAL_PACKAGE_ROOT="$($PYTHON_BIN - <<'PY'
from pathlib import Path
import ai_tech_lead

print(Path(ai_tech_lead.__file__).resolve().parent)
PY
)"
EXPECTED_PACKAGE_ROOT="$WORKTREE_ROOT/src/ai_tech_lead"

if [[ "$ACTUAL_PACKAGE_ROOT" != "$EXPECTED_PACKAGE_ROOT" ]]; then
  echo "Error: worktree source isolation failed." >&2
  echo "Expected package: $EXPECTED_PACKAGE_ROOT" >&2
  echo "Imported package: $ACTUAL_PACKAGE_ROOT" >&2
  exit 1
fi

echo "Worktree source verified: $ACTUAL_PACKAGE_ROOT"
echo "Runtime Python: $PYTHON_BIN"
exec "$PYTHON_BIN" -m ai_tech_lead "$@"
