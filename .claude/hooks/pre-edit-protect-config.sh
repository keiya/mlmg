#!/usr/bin/env bash
# PreToolUse hook: block agent edits to linter/tooling/hook config files.
# Ask the user before modifying any of these.
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")
case "$BASENAME" in
  pyproject.toml|pyrightconfig.json|uv.lock)
    echo "BLOCKED: $BASENAME is a protected config file. Ask the user before modifying."
    exit 2
    ;;
esac

# Normalize to handle both absolute and relative paths fed by the tool.
case "$FILE_PATH" in
  */.claude/settings.json|.claude/settings.json|*/.claude/hooks/*.sh|.claude/hooks/*.sh)
    echo "BLOCKED: $FILE_PATH is a protected harness file. Ask the user before modifying."
    exit 2
    ;;
esac

exit 0
