#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Setup script for ZIP File Viewer on Windows
.DESCRIPTION
    This script installs uv, clones the zip-browser repository, installs the package, 
    and ensures it's available in the PATH. Can run in quick mode or interactive mode.
.PARAMETER Quick
    Run in quick/silent mode (minimal output, no interaction)
.PARAMETER Force
    Force reinstallation even if already installed
.EXAMPLE
    .\setup.ps1
    Run in interactive mode with detailed output
.EXAMPLE
    .\setup.ps1 -Quick
    Run in quick/silent mode (good for automation)
.EXAMPLE
    powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/shhossain/zip-browser/main/setup.ps1 | iex"
    One-liner installation from web
#>

[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$Force
)

# Set error action preference
$ErrorActionPreference = "Stop"

# Colors for output
$Green = "`e[32m"
$Red = "`e[31m"
$Yellow = "`e[33m"
$Blue = "`e[34m"
$Reset = "`e[0m"

function Write-ColorOutput {
    param(
        [string]$Message,
        [string]$Color = $Reset,
        [switch]$NoNewline
    )
    if ($Quick) {
        # In quick mode, only show essential messages
        if ($Color -eq $Red -or $Color -eq $Green) {
            if ($NoNewline) {
                Write-Host "${Color}${Message}${Reset}" -NoNewline
            } else {
                Write-Host "${Color}${Message}${Reset}"
            }
        }
    } else {
        if ($NoNewline) {
            Write-Host "${Color}${Message}${Reset}" -NoNewline
        } else {
            Write-Host "${Color}${Message}${Reset}"
        }
    }
}

function Test-CommandExists {
    param([string]$Command)
    try {
        Get-Command $Command -ErrorAction Stop | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Add-ToPath {
    param([string]$PathToAdd)
    
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -split ";" -notcontains $PathToAdd) {
        if (-not $Quick) {
            Write-ColorOutput "Adding $PathToAdd to user PATH..." $Yellow
        }
        $newPath = "$currentPath;$PathToAdd"
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        
        # Update current session PATH
        $env:PATH = "$env:PATH;$PathToAdd"
        if (-not $Quick) {
            Write-ColorOutput "Added to PATH successfully!" $Green
        }
        return $true
    }
    else {
        if (-not $Quick) {
            Write-ColorOutput "Path already exists in PATH" $Blue
        }
        return $false
    }
}

function Test-Installation {
    try {
        if (Test-CommandExists "zip-browser") {
            $null = zip-browser --help 2>$null
            return $LASTEXITCODE -eq 0
        }
        return $false
    }
    catch {
        return $false
    }
}

try {
    if (-not $Quick) {
        Write-ColorOutput "=== ZIP File Viewer Setup for Windows ===" $Blue
        Write-ColorOutput ""
    } else {
        Write-ColorOutput "🚀 Installing ZIP File Viewer..." $Blue
    }

    # Check if already installed and not forcing
    if (-not $Force -and (Test-Installation)) {
        Write-ColorOutput "✅ ZIP File Viewer is already installed!" $Green
        if (-not $Quick) {
            Write-ColorOutput ""
            Write-ColorOutput "Use -Force to reinstall or run:" $Yellow
            Write-ColorOutput "  zip-browser --help" $Reset
        }
        exit 0
    }

    # Step 1: Install uv
    if (-not $Quick) {
        Write-ColorOutput "Step 1: Installing uv..." $Blue
    }
    
    if (Test-CommandExists "uv") {
        if (-not $Quick) {
            Write-ColorOutput "uv is already installed!" $Green
        }
    }
    else {
        if ($Quick) {
            Write-ColorOutput "📦 Installing uv..." $Yellow
        } else {
            Write-ColorOutput "Installing uv..." $Yellow
        }
        
        try {
            powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
            if (-not $Quick) {
                Write-ColorOutput "uv installed successfully!" $Green
            }
            
            # Check if uv is in PATH, if not add it
            if (-not (Test-CommandExists "uv")) {
                $uvPath = "$env:USERPROFILE\.cargo\bin"
                if (Test-Path $uvPath) {
                    Add-ToPath $uvPath
                    # Update current session
                    $env:PATH = "$env:PATH;$uvPath"
                }
            }
        }
        catch {
            Write-ColorOutput "❌ Failed to install uv: $_" $Red
            exit 1
        }
    }

    # Verify uv installation
    if (-not (Test-CommandExists "uv")) {
        Write-ColorOutput "❌ uv is not available in PATH. Please restart your terminal and try again." $Red
        exit 1
    }

    if (-not $Quick) {
        Write-ColorOutput ""
    }

    # Step 2: Setup repository
    if ($Quick) {
        # Quick mode: use temp directory
        $repoPath = Join-Path $env:TEMP "zip-browser-install-$(Get-Random)"
        Write-ColorOutput "📥 Downloading ZIP File Viewer..." $Yellow
        
        if (Test-Path $repoPath) { 
            Remove-Item $repoPath -Recurse -Force 
        }
        New-Item $repoPath -ItemType Directory | Out-Null
        Set-Location $repoPath
        
        git clone https://github.com/shhossain/zip-browser.git . 2>$null
    } else {
        # Interactive mode: use persistent directory
        Write-ColorOutput "Step 2: Setting up zip-browser..." $Blue
        $repoPath = "zip-browser"
        
        if (Test-Path $repoPath) {
            Write-ColorOutput "Repository already exists. Updating..." $Yellow
            Set-Location $repoPath
            git pull origin main 2>$null
        }
        else {
            Write-ColorOutput "Cloning repository..." $Yellow
            git clone https://github.com/shhossain/zip-browser.git $repoPath 2>$null
            Set-Location $repoPath
        }
        Write-ColorOutput ""
    }

    # Step 3: Install the package
    if ($Quick) {
        Write-ColorOutput "⚙️ Installing package..." $Yellow
    } else {
        Write-ColorOutput "Step 3: Installing zip-browser..." $Blue
        Write-ColorOutput "Installing package with uv..." $Yellow
    }
    
    try {
        uv pip install . 2>$null
        if (-not $Quick) {
            Write-ColorOutput "Package installed successfully!" $Green
        }
    }
    catch {
        Write-ColorOutput "❌ Failed to install package: $_" $Red
        exit 1
    }

    # Cleanup for quick mode
    if ($Quick) {
        Set-Location $env:USERPROFILE
        Remove-Item $repoPath -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-ColorOutput ""
    }

    # Step 4: Verify installation
    if (-not $Quick) {
        Write-ColorOutput "Step 4: Verifying installation..." $Blue
    }
    
    if (Test-Installation) {
        Write-ColorOutput "✅ Installation complete!" $Green
    } else {
        if (-not $Quick) {
            Write-ColorOutput "Verification failed. Trying to add uv bin to PATH..." $Yellow
        }
        
        # Find uv installation and add to PATH
        $uvBinPaths = @(
            "$env:USERPROFILE\.local\bin",
            "$env:USERPROFILE\.cargo\bin",
            "$env:LOCALAPPDATA\uv\bin"
        )
        
        foreach ($binPath in $uvBinPaths) {
            if (Test-Path $binPath) {
                Add-ToPath $binPath
                break
            }
        }
    }

    if (-not $Quick) {
        Write-ColorOutput ""
        Write-ColorOutput "=== Setup Complete! ===" $Green
        Write-ColorOutput ""
        Write-ColorOutput "Next steps:" $Blue
        Write-ColorOutput "1. Create an admin user:" $Yellow
        Write-ColorOutput "   zip-browser user create admin -p admin" $Reset
        Write-ColorOutput ""
        Write-ColorOutput "2. Start the server:" $Yellow
        Write-ColorOutput "   zip-browser server path/to/your/zip/files" $Reset
        Write-ColorOutput ""
        Write-ColorOutput "3. Open your browser to http://localhost:5000" $Yellow
        Write-ColorOutput ""
        Write-ColorOutput "If zip-browser command is not found, restart your terminal and try again." $Blue
    } else {
        Write-ColorOutput ""
        Write-ColorOutput "Next steps:" $Blue
        Write-ColorOutput "1. Restart your terminal" $Yellow
        Write-ColorOutput "2. Run: zip-browser user create admin -p admin" $Yellow
        Write-ColorOutput "3. Run: zip-browser server path/to/your/zip/files" $Yellow
        Write-ColorOutput "4. Open: http://localhost:5000" $Yellow
    }

}
catch {
    Write-ColorOutput "❌ Setup failed: $_" $Red
    exit 1
}
finally {
    # Return to original directory if not in quick mode
    if (-not $Quick -and $PSScriptRoot) {
        Set-Location $PSScriptRoot
    }
}