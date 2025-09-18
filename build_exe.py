#!/usr/bin/env python3
"""
Build script for creating a self-contained ZIP File Viewer executable using PyInstaller.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

def ensure_icon_exists():
    """Ensure the icon file exists, create it if needed."""
    icon_path = Path('zip-viewer-icon.ico')
    if not icon_path.exists():
        print("🎨 Creating application icon...")
        try:
            subprocess.run([sys.executable, 'create_icon.py'], check=True)
            print("✅ Icon created successfully!")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to create icon: {e}")
            return False
        except FileNotFoundError:
            print("❌ create_icon.py not found")
            return False
    else:
        print("✅ Icon file already exists")
    return True

def clean_build():
    """Clean previous build artifacts."""
    print("🧹 Cleaning previous build artifacts...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            try:
                shutil.rmtree(dir_name)
                print(f"   Removed {dir_name}")
            except PermissionError:
                print(f"   Warning: Could not remove {dir_name} (file in use)")
                # Try to continue anyway
                pass
    
    # Clean __pycache__ in src directory
    src_pycache = Path('src/__pycache__')
    if src_pycache.exists():
        try:
            shutil.rmtree(src_pycache)
            print("   Removed src/__pycache__")
        except PermissionError:
            print("   Warning: Could not remove src/__pycache__ (file in use)")
    
    return True

def build_executable():
    """Build the executable using PyInstaller."""
    print("🔨 Building executable with PyInstaller...")
    
    try:
        # Run PyInstaller with the spec file
        cmd = ['pyinstaller', 'zip-viewer.spec']
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✅ Build completed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Build failed with error code {e.returncode}")
        print(f"Error output: {e.stderr}")
        return False
    except FileNotFoundError:
        print("❌ PyInstaller not found. Install it with: pip install pyinstaller")
        return False

def test_executable():
    """Test if the built executable works."""
    print("🧪 Testing the built executable...")
    
    exe_path = Path('dist/zip-viewer.exe')
    if not exe_path.exists():
        print("❌ Executable not found in dist/zip-viewer.exe")
        return False
    
    try:
        # Test help command
        result = subprocess.run([str(exe_path), '--help'], 
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("✅ Executable test passed!")
            print(f"   Executable size: {exe_path.stat().st_size / 1024 / 1024:.1f} MB")
            return True
        else:
            print(f"❌ Executable test failed with code {result.returncode}")
            print(f"Error: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ Executable test timed out")
        return False
    except Exception as e:
        print(f"❌ Error testing executable: {e}")
        return False

def main():
    """Main build process."""
    print("🚀 ZIP File Viewer - Executable Build Script")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not os.path.exists('main.py'):
        print("❌ Error: main.py not found. Please run this script from the project root.")
        sys.exit(1)
    
    if not os.path.exists('zip-viewer.spec'):
        print("❌ Error: zip-viewer.spec not found. Please run PyInstaller first to generate it.")
        sys.exit(1)
    
    # Build process
    steps = [
        ("Ensuring icon exists", ensure_icon_exists),
        ("Cleaning build artifacts", clean_build),
        ("Building executable", build_executable),
        ("Testing executable", test_executable),
    ]
    
    for step_name, step_func in steps:
        print(f"\n📋 {step_name}...")
        if not step_func():
            print(f"\n❌ Build process failed at: {step_name}")
            sys.exit(1)
    
    print("\n🎉 Build process completed successfully!")
    print("\n📦 Your executable is ready:")
    print(f"   Location: {os.path.abspath('dist/zip-viewer.exe')}")
    print("\n🔧 Usage examples:")
    print("   .\\dist\\zip-viewer.exe --help")
    print("   .\\dist\\zip-viewer.exe user create admin --admin")
    print("   .\\dist\\zip-viewer.exe server path/to/your/file.zip")

if __name__ == "__main__":
    main()