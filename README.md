# ZIP File Viewer

A modern, modular Flask web application for browsing and viewing files inside offline and online ZIP archives with multi-user authentication and CSRF protection.

## Quick Start

### Simple Steps to Run

1. **One-line install:**

   **Windows (PowerShell):**

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/shhossain/zip_file_viewer/main/setup.ps1 | iex"
   ```

   **Linux/macOS (Bash):**

   ```bash
   curl -fsSL https://raw.githubusercontent.com/shhossain/zip_file_viewer/main/install.sh | sh
   ```

2. **Create an admin user:**

   ```bash
   zip-viewer user create admin --admin
   ```

3. **Start the server:**

   ```bash
   zip-viewer server path/to/your/file.zip
   ```

4. **Access the application:**
   Open your browser to http://localhost:5000

That's it! You're ready to browse your ZIP files.

## Features

- 🔒 **Password-protected ZIP** View password protected zip files.
- 🌐 **Remote ZIP files** - View remote zip files.
- 📁 **Browse ZIP contents** in a clean UI.
- 👥 **Multi-user authentication** with secure password hashing
- 🔐 **CSRF protection** and role-based access control
- 🖼️ **Image thumbnails** and preview gallery
- 🔍 **Search functionality** across ZIP contents
- 🎨 **Modern, responsive UI** with dark theme support
- 📱 **Mobile-friendly** design
- 📦 **Multiple ZIP files** or directories support
- 🏗️ **Modular architecture** following DRY principles

## Advanced Installation Options

### Manual Installation (Alternative)

#### Prerequisites

- Python 3.8+
- Git

#### Install from GitHub

```bash
# Using uv (recommended)
uv pip install git+https://github.com/shhossain/zip_file_viewer.git

# Or using pip
pip install git+https://github.com/shhossain/zip_file_viewer.git
```

#### Install from Source

```bash
git clone https://github.com/shhossain/zip_file_viewer.git
cd zip_file_viewer
uv pip install -e .
```

## Command Reference

### Basic Usage

```
usage: zip-viewer [-h] {server,user} ...

ZIP File Viewer with Multi-User Authentication

positional arguments:
  {server,user}  Available commands
    server       Start the web server (default)
    user         User management commands

options:
  -h, --help     show this help message and exit
```

### Server Command

```
usage: zip-viewer server [-h] [-H HOST] [-P PORT] [-D] [-u USERNAME] [-p PASSWORD] zip_paths [zip_paths ...]

positional arguments:
  zip_paths             Path(s) to ZIP file(s) - can be single files, directories with ZIP files, URLs to remote ZIP files, or txt file containing list of zip URLs

options:
  -h, --help            show this help message and exit
  -H HOST, --host HOST  Host to run the server on (default: 0.0.0.0)
  -P PORT, --port PORT  Port to run the server on (default: 5000)
  -D, --debug           Enable debug mode
  -u USERNAME, --username USERNAME
                        Username for single-user mode (legacy)
  -p PASSWORD, --password PASSWORD
                        Password for single-user mode (legacy)
```

### User Management Commands

```
usage: zip-viewer user [-h] {create,list,show,update,passwd,delete,info} ...

positional arguments:
  {create,list,show,update,passwd,delete,info}
                        User actions
    create              Create a new user
    list                List all users
    show                Show user details
    update              Update user information
    passwd              Change user password
    delete              Delete a user
    info                Show user database information

options:
  -h, --help            show this help message and exit
```

## Usage Examples

```bash
# Basic usage
zip-viewer server /path/to/archive.zip

# Multiple ZIP files
zip-viewer server /path/to/zip-folder/

# Remote ZIP files from URLs
zip-viewer server https://example.com/archive.zip
zip-viewer server https://github.com/user/repo/archive/main.zip

# Password-protected ZIP files (you'll be prompted for password)
zip-viewer server /path/to/protected.zip

# Custom configuration
zip-viewer server /path/to/files --host 127.0.0.1 --port 8080 --debug

# User management
zip-viewer user create john --email john@example.com
zip-viewer user list --detailed
zip-viewer user update john --admin
zip-viewer user passwd john
zip-viewer user delete john

# Legacy single-user mode (backward compatibility)
zip-viewer server /path/to/files --username admin --password secret
```

## Optional: Building Executable

Create a standalone executable with PyInstaller:

```bash
# Install PyInstaller
uv pip install pyinstaller

# Build executable
python build_exe.py
```

The executable will be created in `dist/zip-viewer.exe` (~35 MB, completely self-contained).

## Additional Information

### User Data Storage

User accounts are stored in:

- **Windows:** `%USERPROFILE%\.zip_file_viewer\users.json`
- **Linux/macOS:** `~/.zip_file_viewer/users.json`

### Security Features

- **Secure password hashing** (PBKDF2 with SHA256, 100,000 iterations)
- **Flask-Login** session management
- **CSRF protection** on all forms
- **Role-based access control** (admin/regular users)
- **Input validation** and sanitization
- **Safe file serving** from ZIP archives

### Architecture

- `app.py` - Main application factory and configuration
- `auth.py` - Authentication management with Flask-Login
- `user_manager.py` - Multi-user management with secure password hashing
- `zip_manager.py` - ZIP file operations and caching
- `routes.py` - Route handlers and request processing
- `utils.py` - Utility functions and helpers
- `config.py` - Configuration management
