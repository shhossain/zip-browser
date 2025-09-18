#!/bin/bash
#
# Setup script for zip-browser on Linux and macOS
# This script installs uv, clones the zip-browser repository, installs the package,
# and ensures it's available in the PATH.
#
# Usage: ./setup-unix.sh

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to add directory to PATH in shell profile
add_to_path() {
    local path_to_add=$1
    local shell_profile=""
    
    # Determine which shell profile to use
    if [[ "$SHELL" == *"zsh"* ]]; then
        shell_profile="$HOME/.zshrc"
    elif [[ "$SHELL" == *"bash"* ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            shell_profile="$HOME/.bash_profile"
        else
            shell_profile="$HOME/.bashrc"
        fi
    elif [[ "$SHELL" == *"fish"* ]]; then
        shell_profile="$HOME/.config/fish/config.fish"
    else
        shell_profile="$HOME/.profile"
    fi
    
    # Check if path is already in the profile
    if [[ -f "$shell_profile" ]] && grep -q "$path_to_add" "$shell_profile"; then
        print_color $BLUE "Path already exists in $shell_profile"
        return 0
    fi
    
    print_color $YELLOW "Adding $path_to_add to $shell_profile..."
    
    # Add to PATH in shell profile
    if [[ "$SHELL" == *"fish"* ]]; then
        echo "set -gx PATH $path_to_add \$PATH" >> "$shell_profile"
    else
        echo "export PATH=\"$path_to_add:\$PATH\"" >> "$shell_profile"
    fi
    
    # Update current session PATH
    export PATH="$path_to_add:$PATH"
    print_color $GREEN "Added to PATH successfully!"
    return 1
}

# Function to detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    else
        echo "unknown"
    fi
}

main() {
    print_color $BLUE "=== ZIP File Viewer Setup for $(detect_os | tr '[:lower:]' '[:upper:]') ==="
    echo

    # Step 1: Install uv
    print_color $BLUE "Step 1: Installing uv..."
    if command_exists uv; then
        print_color $GREEN "uv is already installed!"
    else
        print_color $YELLOW "Installing uv..."
        
        # Try curl first, fallback to wget
        if command_exists curl; then
            print_color $YELLOW "Using curl to download uv installer..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
        elif command_exists wget; then
            print_color $YELLOW "Using wget to download uv installer..."
            wget -qO- https://astral.sh/uv/install.sh | sh
        else
            print_color $RED "Error: Neither curl nor wget is available. Please install one of them first."
            exit 1
        fi
        
        print_color $GREEN "uv installed successfully!"
        
        # Add uv to PATH if not already there
        UV_BIN_PATH="$HOME/.cargo/bin"
        if [[ -d "$UV_BIN_PATH" ]] && ! command_exists uv; then
            path_added=$(add_to_path "$UV_BIN_PATH")
        fi
    fi

    # Verify uv installation
    if ! command_exists uv; then
        print_color $RED "uv is not available in PATH. Please restart your terminal and try again."
        print_color $YELLOW "Or manually add ~/.cargo/bin to your PATH"
        exit 1
    fi

    echo

    # Step 2: Clone repository
    print_color $BLUE "Step 2: Setting up zip-browser..."
    REPO_PATH="zip-browser"
    
    if [[ -d "$REPO_PATH" ]]; then
        print_color $YELLOW "Repository already exists. Updating..."
        cd "$REPO_PATH"
        git pull origin main || print_color $YELLOW "Warning: Could not update repository"
    else
        print_color $YELLOW "Cloning repository..."
        git clone https://github.com/shhossain/zip-browser.git "$REPO_PATH"
        cd "$REPO_PATH"
    fi

    echo

    # Step 3: Install the package
    print_color $BLUE "Step 3: Installing zip-browser..."
    print_color $YELLOW "Installing package with uv..."
    
    if uv pip install -e .; then
        print_color $GREEN "Package installed successfully!"
    else
        print_color $RED "Failed to install package"
        exit 1
    fi

    echo

    # Step 4: Verify installation
    print_color $BLUE "Step 4: Verifying installation..."
    
    if uv run zip-browser --help >/dev/null 2>&1; then
        print_color $GREEN "zip-browser is working correctly!"
    else
        print_color $YELLOW "Verification failed. Trying to add uv bin to PATH..."
        
        # Try to find and add uv installation paths
        UV_PATHS=(
            "$HOME/.local/bin"
            "$HOME/.cargo/bin"
            "$HOME/.uv/bin"
        )
        
        for UV_PATH in "${UV_PATHS[@]}"; do
            if [[ -d "$UV_PATH" ]]; then
                add_to_path "$UV_PATH"
                break
            fi
        done
    fi

    echo
    print_color $GREEN "=== Setup Complete! ==="
    echo
    print_color $BLUE "Next steps:"
    print_color $YELLOW "1. Restart your terminal or run: source ~/.$(basename $SHELL)rc"
    echo
    print_color $YELLOW "2. Create an admin user:"
    echo "   zip-browser user create admin --admin"
    echo
    print_color $YELLOW "3. Start the server:"
    echo "   zip-browser server path/to/your/zip/files"
    echo
    print_color $YELLOW "4. Open your browser to http://localhost:5000"
    echo
    print_color $BLUE "If zip-browser command is not found, restart your terminal and try again."
}

# Check for required tools
check_requirements() {
    local missing_tools=()
    
    if ! command_exists git; then
        missing_tools+=("git")
    fi
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        print_color $RED "Error: Missing required tools: ${missing_tools[*]}"
        print_color $YELLOW "Please install them first:"
        
        OS=$(detect_os)
        case $OS in
            "linux")
                print_color $YELLOW "  Ubuntu/Debian: sudo apt update && sudo apt install ${missing_tools[*]}"
                print_color $YELLOW "  CentOS/RHEL: sudo yum install ${missing_tools[*]}"
                print_color $YELLOW "  Fedora: sudo dnf install ${missing_tools[*]}"
                ;;
            "macos")
                print_color $YELLOW "  macOS: brew install ${missing_tools[*]}"
                print_color $YELLOW "  Or install Xcode Command Line Tools: xcode-select --install"
                ;;
        esac
        exit 1
    fi
}

# Run the setup
check_requirements
main