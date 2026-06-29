#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/nightly-checkpoint.log"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "$LOG_FILE"
}

fail() {
  log "ERROR: $*"
  exit 1
}

log "Starting nightly checkpoint for ai-tech-lead"
log "Project root: $PROJECT_ROOT"

if ! command -v git >/dev/null 2>&1; then
  fail "git is not installed or not on PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  fail "uv is not installed or not on PATH"
fi

if git diff --quiet && git diff --cached --quiet; then
  log "No tracked changes to commit. Stopping cleanly."
  exit 0
fi

CURRENT_BRANCH="$(git branch --show-current)"
log "Current branch: ${CURRENT_BRANCH:-unknown}"
log "Running tests before checkpoint commit"

uv run pytest 2>&1 | tee -a "$LOG_FILE"

CHECKPOINT_BRANCH="nightly-checkpoints"
log "Switching to checkpoint branch: $CHECKPOINT_BRANCH"
git checkout -B "$CHECKPOINT_BRANCH"

git add -A

if git diff --cached --quiet; then
  log "No staged changes after git add. Stopping cleanly."
  exit 0
fi

COMMIT_MESSAGE="nightly checkpoint: $(date '+%Y-%m-%d')"
log "Creating commit: $COMMIT_MESSAGE"
git commit -m "$COMMIT_MESSAGE" 2>&1 | tee -a "$LOG_FILE"

log "Pushing checkpoint branch to origin"
git push origin "$CHECKPOINT_BRANCH" --force-with-lease 2>&1 | tee -a "$LOG_FILE"

log "Nightly checkpoint complete"
