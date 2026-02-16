#!/bin/bash
# ZenMoney Skill Installation Script for Claude Code (macOS/Linux)
# Installs the executor-based skill (token-efficient, ~100 tokens vs ~10k)
# Run with: bash install-skill-executor.sh

set -e

echo "ZenMoney Skill Installer (Executor Mode)"
echo "Token-efficient: ~100 tokens idle vs ~10k for direct MCP"
echo ""

CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
SKILL_NAME="zenmoney"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SOURCE_DIR="$SCRIPT_DIR/skill"
TARGET_DIR="$CLAUDE_SKILLS_DIR/$SKILL_NAME"
DIST_PATH="$SCRIPT_DIR/dist/index.js"

# Check if skill directory exists
if [ ! -d "$SKILL_SOURCE_DIR" ]; then
    echo "Error: skill/ directory not found. Run from the project root."
    exit 1
fi

# Check if MCP server is built
if [ ! -f "$DIST_PATH" ]; then
    echo "MCP server not built. Building..."
    cd "$SCRIPT_DIR" && npm install && npm run build
fi

# Check Python mcp package
echo "Checking Python mcp package..."
if ! python3 -c "import mcp" 2>/dev/null; then
    echo "Installing Python mcp package..."
    pip3 install mcp
else
    echo "✓ Python mcp package is installed"
fi

# Create skills directory
mkdir -p "$CLAUDE_SKILLS_DIR"

# Handle existing installation
if [ -e "$TARGET_DIR" ] || [ -L "$TARGET_DIR" ]; then
    echo "Skill already installed at: $TARGET_DIR"
    read -p "Overwrite? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled"
        exit 0
    fi
    rm -rf "$TARGET_DIR"
fi

# Remove old single-file skill if present
OLD_SKILL="$CLAUDE_SKILLS_DIR/zenmoney.skill.md"
if [ -e "$OLD_SKILL" ] || [ -L "$OLD_SKILL" ]; then
    echo "Removing old single-file skill: $OLD_SKILL"
    rm -f "$OLD_SKILL"
fi

# Create skill directory
echo "Creating skill directory..."
mkdir -p "$TARGET_DIR/references"

# Copy skill files
echo "Copying skill files..."
cp "$SKILL_SOURCE_DIR/SKILL.md" "$TARGET_DIR/"
cp "$SKILL_SOURCE_DIR/executor.py" "$TARGET_DIR/"
cp -r "$SKILL_SOURCE_DIR/references/"*.md "$TARGET_DIR/references/"
chmod +x "$TARGET_DIR/executor.py"

# Create mcp-config.json with absolute path to dist/index.js
echo "Configuring MCP connection..."
cat > "$TARGET_DIR/mcp-config.json" << EOF
{
  "name": "zenmoney",
  "command": "node",
  "args": [
    "$DIST_PATH"
  ],
  "env": {
    "ZENMONEY_TOKEN": "YOUR_TOKEN_HERE"
  }
}
EOF

echo ""
echo "✓ Skill installed at: $TARGET_DIR"
echo ""
echo "Files installed:"
echo "  - SKILL.md (main skill documentation)"
echo "  - executor.py (Python MCP executor)"
echo "  - references/ (detailed documentation)"
echo "  - mcp-config.json (configuration)"
echo ""
echo "Next steps:"
echo "  1. Get your ZenMoney token:"
echo "     Option A (easiest): Copy from https://budgera.com/settings/export"
echo "     Option B: Run 'npm run auth' in project directory"
echo ""
echo "  2. Set your token:"
echo "     Edit: $TARGET_DIR/mcp-config.json"
echo "     Replace: YOUR_TOKEN_HERE with your actual token"
echo ""
echo "  3. Restart Claude Code"
echo ""
echo "  4. Test with: 'Покажи мои счета в ZenMoney'"
echo ""
echo "📚 Full documentation: $TARGET_DIR/SKILL.md"
echo "🔧 Troubleshooting: $TARGET_DIR/references/troubleshooting.md"
echo ""
echo "Installation complete! 🎉"
