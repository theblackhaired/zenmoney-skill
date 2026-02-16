#!/usr/bin/env pwsh
# ZenMoney Skill Installation Script for Claude Code (Windows PowerShell)
# Installs the executor-based skill (token-efficient, ~100 tokens vs ~10k)
# Run with: .\install-skill-executor.ps1

$ErrorActionPreference = "Stop"

Write-Host "ZenMoney Skill Installer (Executor Mode)" -ForegroundColor Cyan
Write-Host "Token-efficient: ~100 tokens idle vs ~10k for direct MCP" -ForegroundColor Green
Write-Host ""

$CLAUDE_SKILLS_DIR = "$env:USERPROFILE\.claude\skills"
$SKILL_NAME = "zenmoney"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$SKILL_SOURCE_DIR = "$SCRIPT_DIR\skill"
$TARGET_DIR = "$CLAUDE_SKILLS_DIR\$SKILL_NAME"
$DIST_PATH = "$SCRIPT_DIR\dist\index.js"

# Check if skill directory exists
if (-not (Test-Path $SKILL_SOURCE_DIR)) {
    Write-Host "Error: skill\ directory not found. Run from the project root." -ForegroundColor Red
    exit 1
}

# Check if MCP server is built
if (-not (Test-Path $DIST_PATH)) {
    Write-Host "MCP server not built. Building..." -ForegroundColor Yellow
    Push-Location $SCRIPT_DIR
    npm install
    npm run build
    Pop-Location
}

# Check Python mcp package
Write-Host "Checking Python mcp package..."
try {
    python -c "import mcp" 2>$null
    Write-Host "✓ Python mcp package is installed" -ForegroundColor Green
} catch {
    Write-Host "Installing Python mcp package..." -ForegroundColor Yellow
    pip install mcp
}

# Create skills directory
New-Item -ItemType Directory -Force -Path $CLAUDE_SKILLS_DIR | Out-Null

# Handle existing installation
if (Test-Path $TARGET_DIR) {
    Write-Host "Skill already installed at: $TARGET_DIR" -ForegroundColor Yellow
    $response = Read-Host "Overwrite? (y/N)"
    if ($response -ne "y" -and $response -ne "Y") {
        Write-Host "Installation cancelled" -ForegroundColor Yellow
        exit 0
    }
    Remove-Item -Recurse -Force $TARGET_DIR
}

# Remove old single-file skill if present
$OLD_SKILL = "$CLAUDE_SKILLS_DIR\zenmoney.skill.md"
if (Test-Path $OLD_SKILL) {
    Write-Host "Removing old single-file skill: $OLD_SKILL" -ForegroundColor Yellow
    Remove-Item -Force $OLD_SKILL
}

# Create skill directory
Write-Host "Creating skill directory..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "$TARGET_DIR\references" | Out-Null

# Copy skill files
Write-Host "Copying skill files..." -ForegroundColor Cyan
Copy-Item "$SKILL_SOURCE_DIR\SKILL.md" "$TARGET_DIR\"
Copy-Item "$SKILL_SOURCE_DIR\executor.py" "$TARGET_DIR\"
Copy-Item "$SKILL_SOURCE_DIR\references\*.md" "$TARGET_DIR\references\"

# Create mcp-config.json with absolute path to dist/index.js
Write-Host "Configuring MCP connection..." -ForegroundColor Cyan
$DIST_PATH_JSON = $DIST_PATH -replace '\\', '/'
@"
{
  "name": "zenmoney",
  "command": "node",
  "args": [
    "$DIST_PATH_JSON"
  ],
  "env": {
    "ZENMONEY_TOKEN": "YOUR_TOKEN_HERE"
  }
}
"@ | Out-File -Encoding UTF8 "$TARGET_DIR\mcp-config.json"

Write-Host ""
Write-Host "✓ Skill installed at: $TARGET_DIR" -ForegroundColor Green
Write-Host ""
Write-Host "Files installed:"
Write-Host "  - SKILL.md (main skill documentation)"
Write-Host "  - executor.py (Python MCP executor)"
Write-Host "  - references/ (detailed documentation)"
Write-Host "  - mcp-config.json (configuration)"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Get your ZenMoney token:"
Write-Host "     Option A (easiest): Copy from https://budgera.com/settings/export" -ForegroundColor Cyan
Write-Host "     Option B: Run 'npm run auth' in project directory"
Write-Host ""
Write-Host "  2. Set your token:"
Write-Host "     Edit: $TARGET_DIR\mcp-config.json"
Write-Host "     Replace: YOUR_TOKEN_HERE with your actual token"
Write-Host ""
Write-Host "  3. Restart Claude Code"
Write-Host ""
Write-Host "  4. Test with: 'Покажи мои счета в ZenMoney'"
Write-Host ""
Write-Host "📚 Full documentation: $TARGET_DIR\SKILL.md" -ForegroundColor Cyan
Write-Host "🔧 Troubleshooting: $TARGET_DIR\references\troubleshooting.md" -ForegroundColor Cyan
Write-Host ""
Write-Host "Installation complete! 🎉" -ForegroundColor Green
