#!/usr/bin/env bash
# record-demo.sh — coordinator: opens a dedicated demo window, records it, converts to GIF.
# Your current iTerm2 session is completely untouched.
#
# Requires: ffmpeg, screencapture (macOS), iTerm2, Python Quartz framework,
#           screen recording permission for iTerm2 (System Settings → Privacy → Screen Recording)
#
# Usage: bash demo/record-demo.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_MOV="$SCRIPT_DIR/demo.mov"
OUTPUT_GIF="$SCRIPT_DIR/demo.gif"
PALETTE_FILE="/tmp/ai-cli-demo-palette.png"
SENTINEL="/tmp/ai-cli-demo-done"

BOLD="\033[1m"
GREEN="\033[1;32m"
DIM="\033[2m"
RESET="\033[0m"

# --- Preflight ---

for dep in ffmpeg screencapture osascript python3 ai; do
  command -v "$dep" &>/dev/null || { echo "Error: $dep not found" >&2; exit 1; }
done
python3 -c "import Quartz" 2>/dev/null || { echo "Error: Python Quartz framework not available" >&2; exit 1; }

# --- Cleanup ---

pkill -f "screencapture.*demo" 2>/dev/null || true
rm -f "$OUTPUT_MOV" "$OUTPUT_GIF" "$PALETTE_FILE" "$SENTINEL"

# --- Create demo window ---

printf "${DIM}Creating demo window…${RESET}\n"

# Snapshot existing iTerm2 layer-0 window IDs before creating the demo window
BEFORE_IDS=$(python3 - <<'PYEOF'
import Quartz
wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
ids = [str(w['kCGWindowNumber']) for w in wins
       if w.get('kCGWindowOwnerName') == 'iTerm2' and w.get('kCGWindowLayer', 99) == 0]
print(','.join(ids))
PYEOF
)

# Create new window, explicitly bring it to front, exit full-screen on it only,
# size it, then switch focus back to the main full-screen session so the user's
# workflow is uninterrupted. All subsequent recording runs in the background.
osascript <<'APPLESCRIPT'
-- Step 1: remember the main session window before we create the new one
tell application "iTerm2"
  set mainWin to window 1
  set newWin to (create window with default profile)
  delay 1.5
  select newWin
  delay 0.3
end tell

-- Step 2: exit full-screen on the new window only (it is now frontmost)
tell application "System Events"
  tell process "iTerm2"
    keystroke "f" using {control down, command down}
  end tell
end tell
delay 1.2

-- Step 3: set the demo window to a fixed 1200x780 size
tell application "iTerm2"
  set bounds of window 1 to {200, 80, 1400, 860}
  delay 0.3

  -- Step 4: switch focus back to the main session
  select mainWin
end tell
APPLESCRIPT

sleep 0.5

# Identify the new window's CGWindowID by diffing against BEFORE_IDS
WINDOW_ID=$(python3 - <<PYEOF
import Quartz
before = set(int(x) for x in "$BEFORE_IDS".split(',') if x)
wins = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
for w in wins:
    if w.get('kCGWindowOwnerName') == 'iTerm2' and w.get('kCGWindowLayer', 99) == 0:
        wid = w['kCGWindowNumber']
        if wid not in before:
            print(wid)
            break
PYEOF
)

[[ -n "$WINDOW_ID" ]] || { echo "Error: could not identify demo window" >&2; exit 1; }
printf "${DIM}Demo window CGWindowID: $WINDOW_ID${RESET}\n"

# --- Start recording the demo window (does not need focus) ---

printf "${DIM}Starting recording…${RESET}\n"
screencapture -v -l "$WINDOW_ID" -C "$OUTPUT_MOV" &
SCAP_PID=$!
sleep 1.5

# --- Launch the demo runner inside the new window ---

rm -f "$SENTINEL"
osascript <<APPLESCRIPT
tell application "iTerm2"
  -- The demo window is the most recently created; it is the frontmost iTerm2 window
  -- on the non-fullscreen desktop Space.
  tell (first window whose frontmost is true)
    tell current tab
      tell current session
        write text "bash '$SCRIPT_DIR/demo-runner.sh' '$SENTINEL'"
      end tell
    end tell
  end tell
end tell
APPLESCRIPT

# --- Wait for runner to signal completion via sentinel file ---

printf "${DIM}Recording demo sequence (waiting for completion)…${RESET}\n"
TIMEOUT=120
ELAPSED=0
until [[ -f "$SENTINEL" ]]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
  if [[ $ELAPSED -ge $TIMEOUT ]]; then
    echo "Error: demo runner timed out after ${TIMEOUT}s" >&2
    kill -INT "$SCAP_PID" 2>/dev/null || true
    exit 1
  fi
done

sleep 2  # let end card linger in the recording

# --- Stop recording ---

kill -INT "$SCAP_PID" 2>/dev/null || true
wait "$SCAP_PID" 2>/dev/null || true
sleep 1

# Close the demo window (identified by its known bounds, never touches other windows)
osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "iTerm2"
  repeat with w in windows
    if bounds of w = {200, 80, 1400, 860} then
      close w
      exit repeat
    end if
  end repeat
end tell
APPLESCRIPT

[[ -f "$OUTPUT_MOV" ]] || { echo "Error: recording file not created — check screen recording permission for iTerm2" >&2; exit 1; }

# --- Convert to optimised GIF (two-pass palette) ---

printf "${DIM}Converting to GIF…${RESET}\n"

ffmpeg -y -i "$OUTPUT_MOV" \
  -vf "fps=15,scale=1200:-1:flags=lanczos,palettegen=stats_mode=full" \
  "$PALETTE_FILE" 2>/dev/null

ffmpeg -y -i "$OUTPUT_MOV" -i "$PALETTE_FILE" \
  -filter_complex "fps=15,scale=1200:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" \
  -loop 0 \
  "$OUTPUT_GIF" 2>/dev/null

rm -f "$PALETTE_FILE" "$SENTINEL"

GIF_SIZE=$(du -sh "$OUTPUT_GIF" | cut -f1)
printf "${GREEN}Done!${RESET} demo.gif ($GIF_SIZE) → $OUTPUT_GIF\n"
