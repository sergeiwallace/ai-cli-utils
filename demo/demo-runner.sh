#!/usr/bin/env bash
# demo-runner.sh — demo sequence, runs inside the dedicated demo window.
# Invoked by record-demo.sh; do not run directly.
#
# Usage: bash demo/demo-runner.sh <sentinel-file>

SENTINEL="${1:-/tmp/ai-cli-demo-done}"

BOLD="\033[1m"
GREEN="\033[1;32m"
CYAN="\033[1;36m"
DIM="\033[2m"
RESET="\033[0m"

type_text() {
  local text="$1"
  for ((i = 0; i < ${#text}; i++)); do
    printf "%s" "${text:$i:1}"
    sleep 0.045
  done
}

# Show command typed in demo tab, open it in a new tab of the same window,
# wait briefly so viewer sees the new tab loading, then switch back.
open_in_new_tab() {
  local cmd="$1"
  local wait_secs="${2:-3}"

  printf "${GREEN}\$${RESET} "
  type_text "$cmd"
  echo
  sleep 0.3

  osascript <<APPLESCRIPT
tell application "iTerm2"
  tell current window
    set demoTabIdx to count of tabs
    set t to (create tab with default profile)
    tell t
      tell current session
        write text "$cmd"
      end tell
    end tell
    delay $wait_secs
    select tab demoTabIdx
  end tell
end tell
APPLESCRIPT

  sleep 0.5
}

run() {
  local cmd="$1"
  printf "${GREEN}\$${RESET} "
  type_text "$cmd"
  echo
  sleep 0.3
  eval "$cmd" || true
}

pause() { sleep "${1:-1}"; }

# --- Demo sequence ---

clear
printf "${CYAN}${BOLD}  ai-cli-utils — AI session management for power users${RESET}\n\n"
pause 1.5

# Session launches — each opens a new tab in this demo window
open_in_new_tab "ai c 1" 3        # Claude Code session
open_in_new_tab "ai g 1" 2.5      # Gemini CLI session
open_in_new_tab "ai c -R 1" 4     # Remote CC session on dev server

# Show all active sessions
run "ai ls"
pause 1.5

# Claude quota monitoring
run "ai quota status"
pause 1.5

# AI passthrough query (-s 3: paid key, avoids browser OAuth in scripted context)
run "ai gemini 'in one sentence, what does a session manager do?' -m flash -s 3"
pause 2.5

# End card
clear
printf "\n\n"
printf "${CYAN}${BOLD}  github.com/sergeiwallace/ai-cli-utils${RESET}\n"
printf "${DIM}  pip install ai-cli-utils${RESET}\n\n"
pause 2

# Signal coordinator that demo is complete
touch "$SENTINEL"
