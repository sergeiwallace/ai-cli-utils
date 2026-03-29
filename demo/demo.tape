# VHS tape file — generates demo.gif
# Install: https://github.com/charmbracelet/vhs
# Run: vhs demo/demo.tape

Output demo/demo.gif

Set FontSize 14
Set Width 1200
Set Height 600
Set Theme "Catppuccin Mocha"

Type "# Launch a Claude Code session"
Enter
Sleep 500ms

Type "ai c 1"
Enter
Sleep 2s

Type "# Session runs in tmux with its own git worktree"
Enter
Sleep 1s

Type "# Launch a second parallel session"
Enter
Sleep 500ms

Type "ai c 2"
Enter
Sleep 2s

Type "# Connect to your remote dev server"
Enter
Sleep 500ms

Type "ai c -R 1"
Enter
Sleep 2s

Type "# Sync Claude Code memory between machines"
Enter
Sleep 500ms

Type "ai sync push"
Enter
Sleep 1.5s

Type "ai sync pull"
Enter
Sleep 1.5s
