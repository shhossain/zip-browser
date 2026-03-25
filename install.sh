#!/bin/sh
# ZIP File Viewer Installer for Linux/macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/shhossain/zip-browser/main/install.sh | sh

set -e

REPO="shhossain/zip-browser"
REPO_URL="https://github.com/${REPO}.git"
ARCHIVE_URL="https://github.com/${REPO}/archive/refs/heads/main.tar.gz"

# Colors (only when outputting to a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

info()  { printf "${BLUE}%s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}%s${NC}\n" "$*"; }
error() { printf "${RED}%s${NC}\n" "$*" >&2; }
ok()    { printf "${GREEN}%s${NC}\n" "$*"; }

has() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
    if [ -n "${TEMP_DIR:-}" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}
trap cleanup EXIT

main() {
    info "Installing ZIP File Viewer..."

    # Need curl or wget for downloading
    if ! has curl && ! has wget; then
        error "Error: curl or wget is required"
        exit 1
    fi

    # Install uv if not present
    if ! has uv; then
        warn "Installing uv..."
        if has curl; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
        else
            wget -qO- https://astral.sh/uv/install.sh | sh
        fi
        export PATH="$HOME/.local/bin:$PATH"

        if ! has uv; then
            error "Error: uv installation failed"
            exit 1
        fi
    fi

    TEMP_DIR=$(mktemp -d)
    cd "$TEMP_DIR"

    warn "Downloading ZIP File Viewer..."

    if has git; then
        git clone --depth 1 "$REPO_URL" .
    elif has curl; then
        curl -L "$ARCHIVE_URL" | tar xz --strip-components=1
    elif has wget; then
        wget -qO- "$ARCHIVE_URL" | tar xz --strip-components=1
    fi

    warn "Installing package..."
    uv tool install .

    ok "Installation complete!"
    echo
    info "Next steps:"
    warn "  1. Restart your terminal (or run: source ~/.$(basename "$SHELL")rc)"
    warn "  2. zip-browser user create admin -p admin"
    warn "  3. zip-browser server /path/to/your/zip/files"
    warn "  4. Open http://localhost:5000"
}

main