#!/usr/bin/env bash
# Stop hook: run smoke tests before accepting task completion.
# Only fires when there are uncommitted .py changes.
set -euo pipefail

CWD=$(echo "$(cat)" | jq -r '.cwd // empty' 2>/dev/null || true)
if [[ -z "$CWD" ]]; then
  CWD="$(pwd)"
fi

cd "$CWD"

# Only run if there are Python source changes (skip docs-only edits).
# If we're not in a git repo, run anyway — better to over-run than miss regressions.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if ! git diff --name-only HEAD 2>/dev/null | grep -q '\.py$'; then
    if ! git ls-files --others --exclude-standard 2>/dev/null | grep -q '\.py$'; then
      exit 0
    fi
  fi
fi

# Pick a runner. `uv run` handles the venv automatically; fall back to a venv pytest
# only if uv is missing. If neither exists, exit 0 — we'd rather not run than run in
# the wrong environment and report spurious failures.
if command -v uv >/dev/null 2>&1; then
  RUNNER=(uv run pytest)
elif [[ -x ".venv/bin/pytest" ]]; then
  RUNNER=(".venv/bin/pytest")
elif [[ -x "venv/bin/pytest" ]]; then
  RUNNER=("venv/bin/pytest")
else
  exit 0
fi

OUTPUT=$("${RUNNER[@]}" -m smoke -q 2>&1) || {
  echo "Smoke tests failed — fix before finishing:" >&2
  echo "$OUTPUT" >&2
  exit 2
}

exit 0
