#!/bin/bash

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')


if echo "$COMMAND" | grep -q 'git commit'; then

  exit 0

fi

exit 0
