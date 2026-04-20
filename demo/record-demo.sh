#!/usr/bin/env bash
# record-demo.sh — coordinator: opens a dedicated demo window, records it, converts to GIF/MP4.
# Your current iTerm2 session is completely untouched.
#
# Requires: ffmpeg, screencapture (macOS), iTerm2, python3 (stdlib only),
#           screen recording permission for iTerm2 (System Settings → Privacy → Screen Recording)
#
# Usage: bash demo/record-demo.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_DIR="$SCRIPT_DIR/archive"
PALETTE_FILE="/tmp/ai-cli-demo-palette.png"
SENTINEL="/tmp/ai-cli-demo-done"

# Demo window position and size — must match DEMO_BOUNDS in demo-runner.sh
DEMO_X=200; DEMO_Y=80; DEMO_W=1200; DEMO_H=780

BOLD="\033[1m"
GREEN="\033[1;32m"
DIM="\033[2m"
RESET="\033[0m"

# Timestamped output filenames
TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
SHORT_COMMIT=$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo "nogit")
BASENAME="demo-${TIMESTAMP}-${SHORT_COMMIT}"
OUTPUT_MOV="/tmp/${BASENAME}.mov"
OUTPUT_MP4="$SCRIPT_DIR/${BASENAME}.mp4"
OUTPUT_GIF="$SCRIPT_DIR/${BASENAME}.gif"

# --- Preflight ---

for dep in ffmpeg screencapture osascript python3 ai; do
  command -v "$dep" &>/dev/null || { echo "Error: $dep not found" >&2; exit 1; }
done

python3 -c "import Quartz" 2>/dev/null || {
  echo "Error: python3 Quartz module not available (install pyobjc-framework-Quartz)" >&2
  exit 1
}

# --- Archive old demo files ---

mkdir -p "$ARCHIVE_DIR"
for f in "$SCRIPT_DIR"/demo-*.gif "$SCRIPT_DIR"/demo-*.mp4 "$SCRIPT_DIR"/demo.gif; do
  [[ -f "$f" ]] && mv "$f" "$ARCHIVE_DIR/" 2>/dev/null || true
done

# --- Cleanup stale processes ---

pkill -f "screencapture.*demo" 2>/dev/null || true
rm -f "$OUTPUT_MOV" "$OUTPUT_MP4" "$OUTPUT_GIF" "$PALETTE_FILE" "$SENTINEL"

# --- Create demo window ---

printf "${DIM}Creating demo window…${RESET}\n"

# Create a new iTerm2 window, exit full-screen if it opened that way (checked by
# comparing its width to a threshold — no System Events accessibility needed),
# position it at the known demo bounds, then return focus to the main session.
osascript <<'APPLESCRIPT'
tell application "iTerm2"
  set mainWin to window 1
  set newWin to (create window with default profile)
  delay 1.5
  select newWin
  delay 0.5

  -- Check if new window opened full-screen by its width.
  -- Full-screen windows span the display (typically >1400pt); the demo window is 1200pt.
  set wb to bounds of newWin
  set winW to (item 3 of wb) - (item 1 of wb)
end tell

if winW > 1400 then
  tell application "System Events"
    tell process "iTerm2"
      keystroke "f" using {control down, command down}
    end tell
  end tell
  delay 1.5
end if

tell application "iTerm2"
  set bounds of newWin to {200, 80, 1400, 860}
  delay 0.3
  select mainWin
end tell
APPLESCRIPT

sleep 0.5
printf "${DIM}Demo window created at {200, 80, 1400, 860}${RESET}\n"

# --- Find the demo window's CGWindowID by matching on known position ---
# screencapture -l <CGWindowID> records silently in the background with no UI overlay.
# We match by owner "iTerm2" and bounds (X=DEMO_X, Y=DEMO_Y) after setting them above.

printf "${DIM}Identifying demo window…${RESET}\n"
WINDOW_ID=$(python3 - <<PYEOF
import Quartz, sys

wl = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
    Quartz.kCGNullWindowID,
)
for w in wl:
    if w.get("kCGWindowOwnerName") != "iTerm2":
        continue
    b = w.get("kCGWindowBounds", {})
    if int(b.get("X", -1)) == ${DEMO_X} and int(b.get("Y", -1)) == ${DEMO_Y}:
        print(w["kCGWindowNumber"])
        sys.exit(0)
print("", end="")
PYEOF
)

if [[ -z "$WINDOW_ID" ]]; then
  echo "Error: could not find demo window at (${DEMO_X}, ${DEMO_Y}) — ensure iTerm2 screen recording permission is granted" >&2
  exit 1
fi
printf "${DIM}Demo window CGWindowID: ${WINDOW_ID}${RESET}\n"

# --- Start recording the demo window by ID (silent, no UI overlay) ---
# -l records a specific window; runs fully in the background.

printf "${DIM}Starting recording…${RESET}\n"
screencapture -v -l "$WINDOW_ID" "$OUTPUT_MOV" &
SCAP_PID=$!
sleep 1.5

# --- Launch the demo runner inside the demo window ---
# Target by known bounds — never by frontmost or window 1, since focus stays
# on the user's main session throughout.

rm -f "$SENTINEL"
osascript <<APPLESCRIPT
tell application "iTerm2"
  repeat with w in windows
    if bounds of w = {200, 80, 1400, 860} then
      tell w
        tell current tab
          tell current session
            write text "bash '$SCRIPT_DIR/demo-runner.sh' '$SENTINEL'"
          end tell
        end tell
      end tell
      exit repeat
    end if
  end repeat
end tell
APPLESCRIPT

# --- Wait for runner to signal completion ---

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

[[ -f "$OUTPUT_MOV" ]] || { echo "Error: recording not created — check screen recording permission for iTerm2" >&2; exit 1; }

# --- Convert to GIF and MP4 ---

printf "${DIM}Converting to GIF and MP4…${RESET}\n"

ffmpeg -y -i "$OUTPUT_MOV" \
  -vf "fps=15,scale=1200:-2:flags=lanczos,palettegen=stats_mode=full" \
  "$PALETTE_FILE" 2>/dev/null

ffmpeg -y -i "$OUTPUT_MOV" -i "$PALETTE_FILE" \
  -filter_complex "fps=15,scale=1200:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" \
  -loop 0 \
  "$OUTPUT_GIF" 2>/dev/null

ffmpeg -y -i "$OUTPUT_MOV" \
  -vf "fps=30,scale=1200:-2:flags=lanczos" \
  -c:v libx264 -crf 22 -preset slow -pix_fmt yuv420p \
  "$OUTPUT_MP4" 2>/dev/null

rm -f "$PALETTE_FILE" "$SENTINEL" "$OUTPUT_MOV"

GIF_SIZE=$(du -sh "$OUTPUT_GIF" | cut -f1)
MP4_SIZE=$(du -sh "$OUTPUT_MP4" | cut -f1)
printf "${GREEN}Done!${RESET}\n"
printf "  GIF: $OUTPUT_GIF ($GIF_SIZE)\n"
printf "  MP4: $OUTPUT_MP4 ($MP4_SIZE)\n"
