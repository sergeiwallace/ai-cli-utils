#!/bin/bash
INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ "$FILE" == *.md ]]; then
  if command -v npx &>/dev/null; then
    npx markdownlint-cli2 "$FILE" 2>&1
  fi
fi

exit 0
