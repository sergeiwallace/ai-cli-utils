#!/usr/bin/env bash
# demo-runner.sh — demo sequence, runs inside the dedicated demo window.
# Invoked by record-demo.sh; do not run directly.
#
# Usage: bash demo/demo-runner.sh <sentinel-file> <win-x> <win-y> <win-r> <win-b>

SENTINEL="${1:-/tmp/ai-cli-demo-done}"
DEMO_WIN_X="${2:-200}"
DEMO_WIN_Y="${3:-80}"
DEMO_WIN_R="${4:-1400}"
DEMO_WIN_B="${5:-860}"

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

# Target demo window by its actual bounds passed in from coordinator.
DEMO_BOUNDS="{$DEMO_WIN_X, $DEMO_WIN_Y, $DEMO_WIN_R, $DEMO_WIN_B}"

open_in_new_tab() {
  local cmd="$1"
  local wait_secs="${2:-3}"

  printf "${GREEN}\$${RESET} "
  type_text "$cmd"
  echo
  sleep 0.3

  osascript <<APPLESCRIPT
tell application "iTerm2"
  repeat with w in windows
    if bounds of w = $DEMO_BOUNDS then
      tell w
        set demoTabIdx to count of tabs
        set t to (create tab with default profile)
        tell t
          tell current session
            -- Minimum wait for shell + direnv to initialize before polling.
            delay 4
            -- Poll until output stops changing for 4 consecutive 0.5s intervals.
            set prevContents to ""
            set stableCount to 0
            repeat 60 times
              delay 0.5
              set curContents to contents
              if curContents is equal to prevContents and length of curContents > 0 then
                set stableCount to stableCount + 1
                if stableCount >= 4 then exit repeat
              else
                set stableCount to 0
              end if
              set prevContents to curContents
            end repeat
            write text "$cmd"
          end tell
        end tell
        delay $wait_secs
        select tab demoTabIdx
      end tell
      exit repeat
    end if
  end repeat
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

# Session launches — each opens a new tab in the demo window
open_in_new_tab "ai c 1" 20       # Claude Code session — wait for CC UI to fully render
open_in_new_tab "ai g 1" 12       # Gemini CLI session
open_in_new_tab "ai c -R 1" 25    # Remote CC session — SSH + remote tmux + CC init

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
