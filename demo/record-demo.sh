#!/usr/bin/env bash
# record-demo.sh — coordinator: finds the persistent demo window by name, records it.
# The demo window ("ai-cli-utils demo") must already be open in iTerm2.
# Your current session focus is never touched.
#
# Requires: ffmpeg, screencapture (macOS), iTerm2, python3 (with pyobjc Quartz),
#           screen recording permission for iTerm2 (System Settings → Privacy → Screen Recording)
#
# Usage: bash demo/record-demo.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_DIR="$SCRIPT_DIR/archive"
PALETTE_FILE="/tmp/ai-cli-demo-palette.png"
SENTINEL="/tmp/ai-cli-demo-done"
DEMO_WINDOW_NAME="ai-cli-utils demo"

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

# --- Find the demo window and get its actual bounds ---

printf "${DIM}Locating demo window \"${DEMO_WINDOW_NAME}\"…${RESET}\n"

WINDOW_BOUNDS=$(osascript - "$DEMO_WINDOW_NAME" <<'APPLESCRIPT'
on run argv
  set winName to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      if name of w contains winName then
        set wb to bounds of w
        set wx to item 1 of wb
        set wy to item 2 of wb
        set wr to item 3 of wb
        set wb2 to item 4 of wb
        return (wx as string) & "," & (wy as string) & "," & (wr as string) & "," & (wb2 as string)
      end if
    end repeat
  end tell
  return ""
end run
APPLESCRIPT
)

if [[ -z "$WINDOW_BOUNDS" ]]; then
  echo "Error: could not find iTerm2 window named \"${DEMO_WINDOW_NAME}\" — open it first" >&2
  exit 1
fi

ACTUAL_X=$(echo "$WINDOW_BOUNDS" | cut -d',' -f1 | tr -d ' ')
ACTUAL_Y=$(echo "$WINDOW_BOUNDS" | cut -d',' -f2 | tr -d ' ')
ACTUAL_R=$(echo "$WINDOW_BOUNDS" | cut -d',' -f3 | tr -d ' ')
ACTUAL_B=$(echo "$WINDOW_BOUNDS" | cut -d',' -f4 | tr -d ' ')
DEMO_W=$(( ACTUAL_R - ACTUAL_X ))
DEMO_H=$(( ACTUAL_B - ACTUAL_Y ))

printf "${DIM}Demo window at (${ACTUAL_X}, ${ACTUAL_Y}) size ${DEMO_W}x${DEMO_H}${RESET}\n"

# --- Reset demo window to a fresh single tab (no focus change) ---

osascript - "$DEMO_WINDOW_NAME" <<'APPLESCRIPT'
on run argv
  set winName to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      if name of w contains winName then
        tell w
          repeat while (count of tabs) > 1
            tell last tab to close
          end repeat
          tell current tab
            tell current session
              write text "clear"
            end tell
          end tell
        end tell
        exit repeat
      end if
    end repeat
  end tell
end run
APPLESCRIPT

sleep 1
printf "${DIM}Demo window reset to fresh tab${RESET}\n"

# --- Find the demo window's CGWindowID via Quartz ---

printf "${DIM}Identifying demo window CGWindowID…${RESET}\n"
WINDOW_ID=$(python3 - "$ACTUAL_X" "$ACTUAL_Y" <<'PYEOF'
import Quartz, sys

target_x = int(sys.argv[1])
target_y = int(sys.argv[2])

wl = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements,
    Quartz.kCGNullWindowID,
)
for w in wl:
    if w.get("kCGWindowOwnerName") != "iTerm2":
        continue
    b = w.get("kCGWindowBounds", {})
    if abs(int(b.get("X", -9999)) - target_x) <= 2 and abs(int(b.get("Y", -9999)) - target_y) <= 2:
        print(w["kCGWindowNumber"])
        sys.exit(0)
print("", end="")
PYEOF
)

if [[ -z "$WINDOW_ID" ]]; then
  echo "Error: could not find demo window in Quartz at (${ACTUAL_X}, ${ACTUAL_Y}) — check screen recording permission for iTerm2" >&2
  exit 1
fi
printf "${DIM}Demo window CGWindowID: ${WINDOW_ID}${RESET}\n"

# --- Start recording (silent, no UI overlay) ---

printf "${DIM}Starting recording…${RESET}\n"
screencapture -v -l "$WINDOW_ID" "$OUTPUT_MOV" &
SCAP_PID=$!
sleep 1.5

# --- Launch the demo runner inside the demo window (no focus change) ---

rm -f "$SENTINEL"
osascript - "$DEMO_WINDOW_NAME" "$SCRIPT_DIR" "$SENTINEL" "$ACTUAL_X" "$ACTUAL_Y" "$ACTUAL_R" "$ACTUAL_B" <<'APPLESCRIPT'
on run argv
  set winName to item 1 of argv
  set scriptDir to item 2 of argv
  set sentinel to item 3 of argv
  set wx to item 4 of argv
  set wy to item 5 of argv
  set wr to item 6 of argv
  set wb to item 7 of argv
  tell application "iTerm2"
    repeat with w in windows
      if name of w contains winName then
        tell w
          tell current tab
            tell current session
              write text "bash '" & scriptDir & "/demo-runner.sh' '" & sentinel & "' '" & wx & "' '" & wy & "' '" & wr & "' '" & wb & "'"
            end tell
          end tell
        end tell
        exit repeat
      end if
    end repeat
  end tell
end run
APPLESCRIPT

# --- Wait for runner to signal completion ---

printf "${DIM}Recording demo sequence (waiting for completion)…${RESET}\n"
TIMEOUT=180
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
  -vf "fps=15,scale=${DEMO_W}:-2:flags=lanczos,palettegen=stats_mode=full" \
  "$PALETTE_FILE" 2>/dev/null

ffmpeg -y -i "$OUTPUT_MOV" -i "$PALETTE_FILE" \
  -filter_complex "fps=15,scale=${DEMO_W}:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" \
  -loop 0 \
  "$OUTPUT_GIF" 2>/dev/null

ffmpeg -y -i "$OUTPUT_MOV" \
  -vf "fps=30,scale=${DEMO_W}:-2:flags=lanczos" \
  -c:v libx264 -crf 22 -preset slow -pix_fmt yuv420p \
  "$OUTPUT_MP4" 2>/dev/null

rm -f "$PALETTE_FILE" "$SENTINEL" "$OUTPUT_MOV"

GIF_SIZE=$(du -sh "$OUTPUT_GIF" | cut -f1)
MP4_SIZE=$(du -sh "$OUTPUT_MP4" | cut -f1)
printf "${GREEN}Done!${RESET}\n"
printf "  GIF: $OUTPUT_GIF ($GIF_SIZE)\n"
printf "  MP4: $OUTPUT_MP4 ($MP4_SIZE)\n"
