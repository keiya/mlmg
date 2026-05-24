#!/usr/bin/env bash
# PostToolUse hook: ruff (check-only + format) and pyright on edited .py files.
# Feedback is shown to Claude but does not block.
#
# stdin JSON: tool_name, tool_input.file_path, cwd
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

if [[ -z "$FILE_PATH" ]] || [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
if [[ -z "$CWD" ]]; then
  exit 0
fi

if [[ -f "$CWD/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$CWD/.venv/bin/activate"
fi

cd "$CWD"

# --- Block `# pyright: basic` in production code ---
REL_PATH="${FILE_PATH#"$CWD/"}"
if [[ "$REL_PATH" != tests/* ]] && grep -qE '^\s*#\s*pyright:\s*basic\b' "$FILE_PATH"; then
  echo "BLOCKED: # pyright: basic is not allowed in production code ($REL_PATH). Use line-level # type: ignore[rule] instead."
  exit 2
fi

ISSUES=""

if command -v ruff >/dev/null 2>&1; then
  RUFF_ISSUES=$(ruff check --quiet "$FILE_PATH" 2>/dev/null || true)
  if [[ -n "$RUFF_ISSUES" ]]; then
    ISSUES+="ruff:"$'\n'"$RUFF_ISSUES"$'\n'
  fi
  ruff format --quiet "$FILE_PATH" 2>/dev/null || true
fi

if command -v pyright >/dev/null 2>&1; then
  PYRIGHT_OUTPUT=$(pyright --outputjson "$FILE_PATH" 2>/dev/null || true)
  if [[ -n "$PYRIGHT_OUTPUT" ]]; then
    ERROR_COUNT=$(echo "$PYRIGHT_OUTPUT" | jq -r '.summary.errorCount // 0' 2>/dev/null || echo "0")
    WARNING_COUNT=$(echo "$PYRIGHT_OUTPUT" | jq -r '.summary.warningCount // 0' 2>/dev/null || echo "0")

    if [[ "$ERROR_COUNT" -gt 0 ]] || [[ "$WARNING_COUNT" -gt 0 ]]; then
      DIAGNOSTICS=$(echo "$PYRIGHT_OUTPUT" | jq -r '
        .generalDiagnostics[]
        | select(.severity == "error" or .severity == "warning")
        | "\(.severity): \(.file):\(.range.start.line + 1): \(.message) [\(.rule // "unknown")]"
      ' 2>/dev/null || true)

      ISSUES+="pyright: ${ERROR_COUNT} error(s), ${WARNING_COUNT} warning(s) in ${FILE_PATH}"$'\n'
      if [[ -n "$DIAGNOSTICS" ]]; then
        ISSUES+="$DIAGNOSTICS"$'\n'
      fi
    fi
  fi
fi

if [[ -n "$ISSUES" ]]; then
  echo "$ISSUES"
fi

exit 0
