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
    
    # Download and install
    print_color $YELLOW "📥 Downloading ZIP File Viewer..."
    if command_exists git; then
        git clone https://github.com/shhossain/zip-browser.git .
    else
        print_color $YELLOW "Git not found, downloading ZIP archive..."
        ZIP_URL="https://github.com/shhossain/zip-browser/archive/refs/heads/main.zip"
        ZIP_FILE="main.zip"
        
        if command_exists curl; then
            curl -L "$ZIP_URL" -o "$ZIP_FILE"
        elif command_exists wget; then
            wget "$ZIP_URL" -O "$ZIP_FILE"
        else
            print_color $RED "❌ Error: Neither git, curl, nor wget found"
            exit 1
        fi
        
        # Extract ZIP file
        if command_exists unzip; then
            unzip -q "$ZIP_FILE"
            mv zip-browser-main/* .
            rm -rf zip-browser-main "$ZIP_FILE"
        else
            print_color $YELLOW "unzip not found, installing git and cloning instead..."
            rm -f "$ZIP_FILE"
            
            # Try to install git
            if command_exists apt-get; then
                print_color $YELLOW "Installing git with apt-get..."
                sudo apt-get update && sudo apt-get install -y git
            elif command_exists yum; then
                print_color $YELLOW "Installing git with yum..."
                sudo yum install -y git
            elif command_exists dnf; then
                print_color $YELLOW "Installing git with dnf..."
                sudo dnf install -y git
            elif command_exists brew; then
                print_color $YELLOW "Installing git with brew..."
                brew install git
            elif command_exists pacman; then
                print_color $YELLOW "Installing git with pacman..."
                sudo pacman -S --noconfirm git
            else
                print_color $RED "❌ Error: Cannot install git automatically. Please install git or unzip manually."
                exit 1
            fi
            
            # Verify git installation
            if command_exists git; then
                print_color $GREEN "Git installed successfully! Cloning repository..."
                git clone https://github.com/shhossain/zip-browser.git .
            else
                print_color $RED "❌ Error: Git installation failed. Please install git or unzip manually."
                exit 1
            fi
        fi
    fi
    
    print_color $YELLOW "⚙️ Installing package..."
    uv pip install .
    
    # Cleanup
    cd "$HOME"
    rm -rf "$TEMP_DIR"
    
    print_color $GREEN "✅ Installation complete!"
    echo
    print_color $BLUE "Next steps:"
    print_color $YELLOW "1. Restart your terminal or run: source ~/.$(basename $SHELL)rc"
    print_color $YELLOW "2. Run: zip-browser user create admin -p admin"
    print_color $YELLOW "3. Run: zip-browser server path/to/your/zip/files"
    print_color $YELLOW "4. Open: http://localhost:5000"
}

# Check requirements
if ! command_exists curl && ! command_exists wget && ! command_exists git; then
    print_color $RED "❌ Error: At least one of git, curl, or wget is required"
    exit 1
fi

main