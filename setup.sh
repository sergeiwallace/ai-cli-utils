#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[setup]${NC} $1"; }
ok()    { echo -e "${GREEN}[setup]${NC} $1"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $1"; }
err()   { echo -e "${RED}[setup]${NC} $1"; }

step=0
total=9
next_step() { step=$((step + 1)); echo ""; info "[$step/$total] $1"; }

PROJECT_DIR="$(pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Project Setup: ${GREEN}${PROJECT_NAME}${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

next_step "Git initialization"
if [ -d .git ]; then
    ok "Git repo already initialized."
else
    git init -b main
    ok "Git repo initialized (branch: main)."
fi

next_step "Git hooks"
if [ -f .githooks/install.sh ]; then
    if bash .githooks/install.sh; then
        ok "Git hooks installed."
    else
        warn "Git hooks not installed — see the message above, then re-run bash .githooks/install.sh."
    fi
else
    warn "No .githooks/install.sh found — skipping."
fi

next_step "Dependencies"

if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; then
    if command -v uv &>/dev/null; then
        info "Python project detected. Setting up with uv..."
        uv sync --inexact 2>/dev/null && ok "Python deps installed (uv)." || warn "Python install failed; run manually."
    else
        warn "Python project detected but uv is required. Install: https://docs.astral.sh/uv/"
    fi
fi

if [ -f package.json ]; then
    if command -v npm &>/dev/null; then
        info "Node.js project detected. Installing..."
        npm install --silent 2>/dev/null && ok "Node.js deps installed." || warn "npm install failed — run manually."
    else
        warn "package.json found but npm not available."
    fi
fi

if [ -f Cargo.toml ]; then
    if command -v cargo &>/dev/null; then
        info "Rust project detected. Building..."
        cargo build --workspace 2>/dev/null && ok "Rust built." || warn "cargo build failed — run manually."
    else
        warn "Cargo.toml found but cargo not available."
    fi
fi

if [ -f go.mod ]; then
    if command -v go &>/dev/null; then
        info "Go project detected. Downloading modules..."
        go mod download 2>/dev/null && ok "Go modules downloaded." || warn "go mod download failed — run manually."
    else
        warn "go.mod found but go not available."
    fi
fi

next_step "Environment"
if [ -f .env.example ] && [ ! -f .env ]; then
    cp .env.example .env
    ok "Created .env from .env.example."
else
    ok ".env already exists or no .env.example found."
fi



if command -v doppler &>/dev/null; then
    ok "Doppler found — secrets load from Doppler (project: your Doppler project, config: dev)."
    info "To add secrets: doppler secrets set KEY_NAME --project <your-project> --config dev"
else
    warn "Doppler not found. Install: brew install dopplerhq/cli/doppler"
    warn "Then run: doppler login && doppler secrets set KEY_NAME --project <your-project> --config dev"
fi




if command -v direnv &>/dev/null; then
    if [ -f .envrc ]; then
        direnv allow . 2>/dev/null && ok "direnv allowed." || warn "direnv allow failed — run 'direnv allow' manually."
    fi
else
    warn "direnv not installed — recommended for env management."
fi

next_step "GitHub repository (private)"
if ! command -v gh &>/dev/null; then
    err "gh CLI not found. Install: https://cli.github.com/"
else
    if gh repo create "$PROJECT_NAME" --private --source=. --remote=origin 2>/dev/null; then
        ok "GitHub repo created: $PROJECT_NAME (private)"
    else
        warn "Repo creation failed — it may already exist. Add remote manually."
    fi
fi

next_step "Initial commit"
if git log --oneline -1 &>/dev/null; then
    ok "Commits already exist — skipping initial commit."
else
    git add -A
    git commit -m "$(cat <<'COMMITEOF'
chore: Initial scaffold from project-template
COMMITEOF
)"
    ok "Initial commit created."
fi

next_step "Project vision"
if grep -q "Original Vision" README.md 2>/dev/null && command -v claude &>/dev/null; then
    info "Expanding project vision with Claude Code..."
    VISION_LINE=$(grep -A1 "## Original Vision" README.md | tail -1 | sed 's/^> //')
    if [ -n "$VISION_LINE" ]; then
        VISION_PROMPT=""
        IFS= read -r -d '' VISION_PROMPT <<VISIONEOF || true
You are writing the About section of a README.md for a new software project called $PROJECT_NAME.

The project creator described their vision as:
$VISION_LINE

Write a clear, compelling About section (2-4 paragraphs) that expands this vision into a proper project statement. Cover:
- What the project does and why it exists
- Who it's for and what problem it solves
- Key goals or differentiators

Keep the tone professional but approachable. Do NOT include a heading — just the body paragraphs.
Do NOT wrap output in markdown code fences. Output ONLY the paragraphs.
VISIONEOF
        vision_tmp=$(mktemp "${TMPDIR:-/tmp}/project-vision.XXXXXX") || {
            warn "Could not create a secure temporary file for the project vision."
            vision_tmp=""
        }
        if [ -n "$vision_tmp" ]; then
            chmod 600 "$vision_tmp"
            trap 'rm -f -- "$vision_tmp"' EXIT HUP INT TERM
            claude --print --output-format text --max-turns 1 --prompt "$VISION_PROMPT" \
                > "$vision_tmp" 2>/dev/null || true
        fi

        if [ -n "$vision_tmp" ] && [ -s "$vision_tmp" ]; then
            python3 -c '
import sys
readme = open("README.md").read()
expanded = open(sys.argv[1]).read().strip()
readme = readme.replace("<!-- AI-expanded project statement will be inserted here by setup.sh -->", expanded)
open("README.md", "w").write(readme)
' "$vision_tmp" 2>/dev/null
            if [ $? -eq 0 ]; then
                git add README.md
                if git commit -m "docs: Expand project vision in README" 2>/dev/null; then
                    ok "Project vision expanded in README.md."
                elif git add README.md && git commit -m "docs: Expand project vision in README" 2>/dev/null; then
                    ok "Project vision expanded in README.md (hooks auto-fixed formatting)."
                else
                    warn "Pre-commit hooks blocked the README commit — inspect and commit it manually."
                fi
            else
                warn "Failed to update README — you can expand it manually later."
            fi
        elif [ -n "$vision_tmp" ]; then
            warn "Claude didn't return output — skipping vision expansion."
        fi
        if [ -n "$vision_tmp" ]; then
            rm -f -- "$vision_tmp"
        fi
    fi
else
    if ! grep -q "Original Vision" README.md 2>/dev/null; then
        ok "No project vision provided — skipping."
    elif ! command -v claude &>/dev/null; then
        warn "Claude Code not found — skipping vision expansion. You can expand it manually."
    fi
fi

next_step "Claude Code verification"
if command -v claude &>/dev/null; then
    ok "Claude Code CLI found."
    if [ -f .mcp.json ]; then
        ok "MCP config present (.mcp.json)."
    else
        warn "No .mcp.json — MCP servers not configured."
    fi
    if [ -f CLAUDE.md ]; then
        ok "CLAUDE.md present."
    fi
    if [ -d .claude/agents ]; then
        AGENT_COUNT=$(ls .claude/agents/*.md 2>/dev/null | wc -l | tr -d ' ')
        ok "$AGENT_COUNT agent definitions found in .claude/agents/."
    fi
else
    warn "Claude Code CLI not found. Install: https://claude.com/claude-code"
fi




next_step "Setup complete"
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ ${PROJECT_NAME} is ready${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo "  What would you like to do next? (comma-separated, e.g. 1,4)"
echo ""

declare -a MENU_LABELS=()
declare -a MENU_ACTIONS=()

MENU_LABELS+=("Plan project architecture     — outline components, data models, key decisions")
MENU_ACTIONS+=("plan")





MENU_LABELS+=("Customize session config      — tailor CLAUDE.md, agents, skills")
MENU_ACTIONS+=("config")

MENU_LABELS+=("Explore the scaffold          — walk through what was generated")
MENU_ACTIONS+=("explore")

MENU_LABELS+=("Nothing — I'll take it from here")
MENU_ACTIONS+=("nothing")

for i in "${!MENU_LABELS[@]}"; do
    echo "    $((i + 1))) ${MENU_LABELS[$i]}"
done





echo ""
read -p "  Pick: " -r choices
echo ""

IFS=',' read -ra SELECTED <<< "$choices"

for choice in "${SELECTED[@]}"; do
    choice=$(echo "$choice" | tr -d ' ')
    idx=$((choice - 1))

    MENU_SIZE=${#MENU_ACTIONS[@]}
    if [ "$idx" -lt 0 ] || [ "$idx" -ge "$MENU_SIZE" ]; then
        warn "Invalid choice: $choice — skipping."
        continue
    fi

    action="${MENU_ACTIONS[$idx]}"

    case "$action" in
        plan)
            info "Launching Claude Code to plan project architecture..."
            claude --prompt "Help me plan the architecture for this project. Review CLAUDE.md and the scaffold, then walk me through: 1) key components and their responsibilities, 2) data models, 3) external integrations, 4) open design decisions. Output a design doc to docs/designs/architecture.md"
            ;;



        config)
            info "Launching Claude Code to customize session config..."
            claude --prompt "Help me customize the session configuration for this project. Walk me through each file and ask what I want to change: 1) CLAUDE.md — instructions, workflow rules, test conventions, 2) .claude/agents/ — agent definitions, 3) .claude/skills/ — skill definitions, 4) .mcp.json — MCP server connections. Show me current contents and suggest project-specific adjustments."
            ;;
        explore)
            info "Launching Claude Code to explore..."
            claude --prompt "Walk me through what was generated in this scaffold. Explain the project structure, key config files, available skills, and how the dev workflow works."
            ;;
        nothing)
            info "You're all set. Run 'claude' when you're ready."
            ;;
    esac
done
