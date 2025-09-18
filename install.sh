#!/bin/bash
# ZIP File Viewer Installer for Linux/macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/shhossain/zip-browser/main/install.sh | sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_color() {
    echo -e "${1}${2}${NC}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

main() {
    print_color $BLUE "🚀 Installing ZIP File Viewer..."
    
    # Install uv if not present
    if ! command_exists uv; then
        print_color $YELLOW "📦 Installing uv..."
        if command_exists curl; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
        elif command_exists wget; then
            wget -qO- https://astral.sh/uv/install.sh | sh
        else
            print_color $RED "❌ Error: Neither curl nor wget found"
            exit 1
        fi
        
        # Add to PATH for current session
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    
    # Create temp directory
    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"
    
    # Clone and install
    print_color $YELLOW "📥 Downloading ZIP File Viewer..."
    git clone https://github.com/shhossain/zip-browser.git .
    
    print_color $YELLOW "⚙️ Installing package..."
    uv pip install .
    
    # Cleanup
    cd "$HOME"
    rm -rf "$TEMP_DIR"
    
    print_color $GREEN "✅ Installation complete!"
    echo
    print_color $BLUE "Next steps:"
    print_color $YELLOW "1. Restart your terminal or run: source ~/.$(basename $SHELL)rc"
    print_color $YELLOW "2. Run: zip-browser user create admin --admin"
    print_color $YELLOW "3. Run: zip-browser server path/to/your/zip/files"
    print_color $YELLOW "4. Open: http://localhost:5000"
}

# Check requirements
if ! command_exists git; then
    print_color $RED "❌ Error: git is required but not installed"
    exit 1
fi

main