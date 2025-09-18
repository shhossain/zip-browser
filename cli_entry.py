#!/usr/bin/env python3
"""
Command line entry point for zip-file-viewer package.
"""

import sys
import os

def main():
    """Entry point for the zip-browser command line tool."""
    # Add the package directory to the Python path
    package_dir = os.path.dirname(os.path.abspath(__file__))
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)

    # Import and run the main application
    try:
        from src.app import main as app_main
        app_main()
    except ImportError:
        # Fallback: try to run the main.py file directly
        main_py = os.path.join(package_dir, 'main.py')
        if os.path.exists(main_py):
            import subprocess
            subprocess.run([sys.executable, main_py] + sys.argv[1:])
        else:
            print("Error: Could not find the application entry point.")
            sys.exit(1)

if __name__ == "__main__":
    main()
