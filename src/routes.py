"""
Route handlers for the ZIP file viewer application.
"""

import os
import io
import math
import mimetypes
import subprocess
import tempfile
import threading
import hashlib
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    send_file,
    abort,
)
from flask_login import login_user, logout_user, login_required, current_user
from PIL import Image

from .auth import LoginForm
from .utils import is_image, is_video, validate_pagination_params, is_system_file, needs_transcoding


def check_ffmpeg_available():
    """Check if FFmpeg is available on the system"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


FFMPEG_AVAILABLE = check_ffmpeg_available()

# Limit concurrent FFmpeg processes to prevent server overload
FFMPEG_SEMAPHORE = threading.Semaphore(2)

# Cache for transcoded videos and thumbnails
VIDEO_CACHE_DIR = tempfile.mkdtemp(prefix="zip_browser_video_cache_")
THUMB_CACHE_DIR = tempfile.mkdtemp(prefix="zip_browser_thumb_cache_")


def get_video_cache_path(zip_id, path):
    """Get the cache path for a transcoded video"""
    cache_key = hashlib.md5(f"{zip_id}_{path}".encode()).hexdigest()
    return os.path.join(VIDEO_CACHE_DIR, f"{cache_key}.mp4")


def get_thumb_cache_path(zip_id, path, thumb_type="static"):
    """Get the cache path for a video thumbnail"""
    cache_key = hashlib.md5(f"{zip_id}_{path}".encode()).hexdigest()
    ext = "gif" if thumb_type == "gif" else "jpg"
    return os.path.join(THUMB_CACHE_DIR, f"{cache_key}_{thumb_type}.{ext}")


def create_routes(auth_manager, zip_manager):
    """Create and configure all routes"""
    bp = Blueprint("main", __name__)

    @bp.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("main.zip_list"))
        return redirect(url_for("main.login"))

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        form = LoginForm()

        if form.validate_on_submit():
            username = form.username.data
            password = form.password.data

            if auth_manager.validate_credentials(username, password):
                user = auth_manager.load_user(username)
                login_user(user)
                next_page = request.args.get("next")
                return redirect(next_page or url_for("main.zip_list"))
            else:
                flash("Invalid username or password")

        return render_template("login.html", form=form)

    @bp.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out")
        return redirect(url_for("main.login"))

    @bp.route("/zips")
    @login_required
    def zip_list():
        """List all available ZIP files"""
        return render_template(
            "zip_list.html", zip_files=zip_manager.get_all_zip_files()
        )

    @bp.route("/unlock/<zip_id>", methods=["POST"])
    @login_required
    def unlock_zip(zip_id):
        """Unlock a password-protected ZIP file"""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            return jsonify({"success": False, "error": "ZIP file not found"})

        password = request.form.get("password", "")

        if zip_manager.validate_zip_password(zip_info["path"], password):
            zip_manager.load_zip_file(zip_id, password)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Invalid password"})

    @bp.route("/browse/<zip_id>/")
    @bp.route("/browse/<zip_id>/<path:path>")
    @login_required
    def browse(zip_id, path=""):
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        # Check if zip file requires password and is not unlocked
        if zip_info["requires_password"] and not zip_info["zfile"]:
            return render_template("zip_unlock.html", zip_info=zip_info, zip_id=zip_id)

        # Load zip file if not already loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(500)

        cur_dir = zip_manager.get_dir_tree(zip_id, path)
        if cur_dir is None:
            return abort(404)

        # View mode, sorting and pagination logic
        view_mode = request.args.get("view", "thumbnail")
        sort_by = request.args.get("sort", "name")
        sort_order = request.args.get("order", "asc")
        page = request.args.get("page", 1)
        per_page = request.args.get("per_page", 30)
        thumb_size = request.args.get("thumb_size", 100)

        # Validate parameters
        allowed_view_modes = ["thumbnail", "details"]
        allowed_sort_options = ["name", "type", "date"]
        allowed_sort_orders = ["asc", "desc"]

        if view_mode not in allowed_view_modes:
            view_mode = "thumbnail"
        if sort_by not in allowed_sort_options:
            sort_by = "name"
        if sort_order not in allowed_sort_orders:
            sort_order = "asc"

        page, per_page, thumb_size = validate_pagination_params(
            page, per_page, thumb_size
        )

        # Create unified items list
        all_items = []
        all_images = []

        for name in cur_dir:
            item = _create_item_dict(name, cur_dir[name], zip_id, zip_manager, path)
            all_items.append(item)

            if item["is_image"]:
                all_images.append(name)

        # Sort items
        all_items = _sort_items(all_items, sort_by, sort_order)

        # Pagination for images in the viewer
        total_pages = math.ceil(len(all_images) / per_page)

        return render_template(
            "index.html",
            items=all_items,
            all_images=all_images,
            path=path,
            zip_id=zip_id,
            zip_name=zip_info["name"],
            view_mode=view_mode,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            total_pages=total_pages,
            per_page=per_page,
            thumb_size=thumb_size,
        )

    @bp.route("/thumb/<zip_id>/<path:path>")
    @login_required
    def thumb(zip_id, path):
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        # Ensure the ZIP file is loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # Get thumbnail size from query parameter (default: 100)
        size = int(request.args.get("size", 100))
        # Limit size to predefined options for security
        allowed_sizes = [80, 100, 150, 200, 250]
        if size not in allowed_sizes:
            size = 100

        # Check if it's a system file that should be filtered out
        if is_system_file(path):
            abort(404)

        try:
            # Get a fresh zip file object for reading
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            data = zfile.read(path)
            img = Image.open(io.BytesIO(data))
            img.thumbnail((size, size))

            # Convert RGBA to RGB if necessary (JPEG doesn't support transparency)
            if img.mode in ("RGBA", "LA", "P"):
                # Create a white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(
                    img, mask=img.split()[-1] if img.mode == "RGBA" else None
                )
                img = background
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            buf.seek(0)

            # Close the zip file since we got a fresh object
            if hasattr(zfile, "close"):
                zfile.close()

            return send_file(buf, mimetype="image/jpeg")
        except Exception as e:
            # Log the error for debugging
            print(f"Thumbnail error for {zip_id}/{path}: {e}")
            abort(404)
        finally:
            # Always close the zip file object since we got a fresh one
            if "zfile" in locals() and hasattr(zfile, "close"):
                try:
                    zfile.close()
                except Exception:
                    pass

    @bp.route("/view/<zip_id>/<path:path>")
    @login_required
    def view_file(zip_id, path):
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        # Ensure the ZIP file is loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        try:
            # Get a fresh zip file object for reading
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            data = zfile.read(path)
            mime, _ = mimetypes.guess_type(path)

            return send_file(
                io.BytesIO(data), mimetype=mime or "application/octet-stream"
            )
        except Exception as e:
            return f"<p>Error reading file: {e}</p>"
        finally:
            # Always close the zip file object since we got a fresh one
            if "zfile" in locals() and hasattr(zfile, "close"):
                try:
                    zfile.close()
                except Exception:
                    pass

    @bp.route("/stream/<zip_id>/<path:path>")
    @login_required
    def stream_video(zip_id, path):
        """Stream video with FFmpeg transcoding for unsupported formats - with caching and seeking support"""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        # Ensure the ZIP file is loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # Check if transcoding is needed
        if not needs_transcoding(path):
            # For browser-native formats, just serve the file directly
            return redirect(url_for('main.view_file', zip_id=zip_id, path=path))

        if not FFMPEG_AVAILABLE:
            # FFmpeg not available, try to serve directly anyway
            return redirect(url_for('main.view_file', zip_id=zip_id, path=path))

        # Check if we have a cached version
        cache_path = get_video_cache_path(zip_id, path)
        
        if os.path.exists(cache_path):
            # Serve the cached file with range request support for seeking
            return send_file(
                cache_path,
                mimetype="video/mp4",
                conditional=True  # Enables range request support
            )

        # Need to transcode - use semaphore to limit concurrent processes
        acquired = FFMPEG_SEMAPHORE.acquire(timeout=30)
        if not acquired:
            # Too many concurrent transcoding requests
            return jsonify({"error": "Server busy, please try again later"}), 503

        try:
            # Get a fresh zip file object for reading
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            # Extract video to a temporary file
            video_data = zfile.read(path)
            
            # Close the zip file
            if hasattr(zfile, "close"):
                zfile.close()

            # Create temp file with original extension
            ext = os.path.splitext(path)[1]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_input:
                tmp_input.write(video_data)
                tmp_input_path = tmp_input.name

            try:
                # Transcode to cache file using FFmpeg
                process = subprocess.run(
                    [
                        "ffmpeg",
                        "-i", tmp_input_path,
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "23",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-movflags", "+faststart",
                        "-y",
                        cache_path
                    ],
                    capture_output=True,
                    timeout=300  # 5 minute timeout
                )

                if process.returncode != 0:
                    print(f"FFmpeg error: {process.stderr.decode()}")
                    # Fall back to direct file serving
                    return redirect(url_for('main.view_file', zip_id=zip_id, path=path))

            finally:
                # Clean up input temp file
                try:
                    os.unlink(tmp_input_path)
                except Exception:
                    pass

            # Serve the cached file with range request support
            return send_file(
                cache_path,
                mimetype="video/mp4",
                conditional=True
            )

        except subprocess.TimeoutExpired:
            print(f"FFmpeg timeout for {zip_id}/{path}")
            return jsonify({"error": "Video transcoding timed out"}), 504
        except Exception as e:
            print(f"Video streaming error for {zip_id}/{path}: {e}")
            # Fall back to direct file serving
            return redirect(url_for('main.view_file', zip_id=zip_id, path=path))
        finally:
            FFMPEG_SEMAPHORE.release()

    @bp.route("/video-info/<zip_id>/<path:path>")
    @login_required
    def video_info(zip_id, path):
        """Get video information including whether transcoding is needed"""
        return jsonify({
            "needs_transcoding": needs_transcoding(path),
            "ffmpeg_available": FFMPEG_AVAILABLE,
            "stream_url": url_for('main.stream_video', zip_id=zip_id, path=path) if needs_transcoding(path) and FFMPEG_AVAILABLE else url_for('main.view_file', zip_id=zip_id, path=path),
            "direct_url": url_for('main.view_file', zip_id=zip_id, path=path)
        })

    @bp.route("/video-thumb/<zip_id>/<path:path>")
    @login_required
    def video_thumbnail(zip_id, path):
        """Generate a static thumbnail for a video"""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        if not FFMPEG_AVAILABLE:
            abort(404)

        # Ensure the ZIP file is loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # Check cache first
        cache_path = get_thumb_cache_path(zip_id, path, "static")
        if os.path.exists(cache_path):
            return send_file(cache_path, mimetype="image/jpeg")

        # Use semaphore to limit concurrent FFmpeg processes
        acquired = FFMPEG_SEMAPHORE.acquire(timeout=10)
        if not acquired:
            abort(503)

        try:
            # Get video from zip
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            video_data = zfile.read(path)
            if hasattr(zfile, "close"):
                zfile.close()

            # Create temp input file
            ext = os.path.splitext(path)[1]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_input:
                tmp_input.write(video_data)
                tmp_input_path = tmp_input.name

            try:
                # First get video duration to find a good frame
                probe_result = subprocess.run(
                    [
                        "ffprobe",
                        "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        tmp_input_path
                    ],
                    capture_output=True,
                    timeout=10
                )
                
                duration = 0
                try:
                    duration = float(probe_result.stdout.decode().strip())
                except Exception:
                    duration = 0
                
                # Get frame at 10% of video or 1 second, whichever is smaller
                seek_time = min(duration * 0.1, 1.0) if duration > 0 else 0

                # Extract thumbnail using FFmpeg
                subprocess.run(
                    [
                        "ffmpeg",
                        "-ss", str(seek_time),
                        "-i", tmp_input_path,
                        "-vframes", "1",
                        "-vf", "scale=320:-1",
                        "-q:v", "3",
                        "-y",
                        cache_path
                    ],
                    capture_output=True,
                    timeout=30
                )

                if os.path.exists(cache_path):
                    return send_file(cache_path, mimetype="image/jpeg")
                else:
                    abort(500)

            finally:
                try:
                    os.unlink(tmp_input_path)
                except Exception:
                    pass

        except Exception as e:
            print(f"Video thumbnail error: {e}")
            abort(500)
        finally:
            FFMPEG_SEMAPHORE.release()

    @bp.route("/video-thumb-gif/<zip_id>/<path:path>")
    @login_required
    def video_thumbnail_gif(zip_id, path):
        """Generate an animated GIF preview for a video"""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        if not FFMPEG_AVAILABLE:
            abort(404)

        # Ensure the ZIP file is loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # Check cache first
        cache_path = get_thumb_cache_path(zip_id, path, "gif")
        if os.path.exists(cache_path):
            return send_file(cache_path, mimetype="image/gif")

        # Use semaphore
        acquired = FFMPEG_SEMAPHORE.acquire(timeout=10)
        if not acquired:
            abort(503)

        try:
            # Get video from zip
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            video_data = zfile.read(path)
            if hasattr(zfile, "close"):
                zfile.close()

            # Create temp input file
            ext = os.path.splitext(path)[1]
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_input:
                tmp_input.write(video_data)
                tmp_input_path = tmp_input.name

            try:
                # Get video duration
                probe_result = subprocess.run(
                    [
                        "ffprobe",
                        "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        tmp_input_path
                    ],
                    capture_output=True,
                    timeout=10
                )
                
                duration = 0
                try:
                    duration = float(probe_result.stdout.decode().strip())
                except Exception:
                    duration = 10  # Default

                # Create GIF with 8-10 frames from different parts of video
                # Use fps=0.5 to get 1 frame every 2 seconds, or fewer frames for short videos
                if duration < 5:
                    fps_filter = "fps=2"  # 2 fps for very short videos
                elif duration < 30:
                    fps_filter = "fps=0.5"  # 1 frame every 2 seconds
                else:
                    fps_filter = "fps=0.2"  # 1 frame every 5 seconds

                # Create GIF with palette for better quality
                palette_path = tmp_input_path + "_palette.png"
                
                # Generate palette
                subprocess.run(
                    [
                        "ffmpeg",
                        "-i", tmp_input_path,
                        "-vf", f"{fps_filter},scale=200:-1:flags=lanczos,palettegen=max_colors=64",
                        "-y",
                        palette_path
                    ],
                    capture_output=True,
                    timeout=60
                )

                # Generate GIF using palette
                subprocess.run(
                    [
                        "ffmpeg",
                        "-i", tmp_input_path,
                        "-i", palette_path,
                        "-lavfi", f"{fps_filter},scale=200:-1:flags=lanczos[x];[x][1:v]paletteuse",
                        "-t", "10",  # Max 10 seconds of video
                        "-y",
                        cache_path
                    ],
                    capture_output=True,
                    timeout=60
                )

                # Clean up palette
                try:
                    os.unlink(palette_path)
                except Exception:
                    pass

                if os.path.exists(cache_path):
                    return send_file(cache_path, mimetype="image/gif")
                else:
                    abort(500)

            finally:
                try:
                    os.unlink(tmp_input_path)
                except Exception:
                    pass

        except Exception as e:
            print(f"Video GIF thumbnail error: {e}")
            abort(500)
        finally:
            FFMPEG_SEMAPHORE.release()

    @bp.route("/images/<zip_id>/")
    @bp.route("/images/<zip_id>/<path:dir_path>")
    @login_required
    def list_images(zip_id, dir_path=""):
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            return jsonify([])

        cur_dir = zip_manager.get_dir_tree(zip_id, dir_path)
        if cur_dir is None:
            return jsonify([])

        images = [
            name for name in sorted(cur_dir) if cur_dir[name] is None and is_image(name)
        ]
        return jsonify(images)

    @bp.route("/search/<zip_id>")
    @login_required
    def search(zip_id):
        """Search for files in a ZIP archive"""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        # Check if zip file requires password and is not unlocked
        if zip_info["requires_password"] and not zip_info["zfile"]:
            return render_template("zip_unlock.html", zip_info=zip_info, zip_id=zip_id)

        # Load zip file if not already loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(500)

        # Get search parameters
        query = request.args.get("q", "").strip()
        search_type = request.args.get("type", "all")
        page = request.args.get("page", 1)
        per_page = request.args.get("per_page", 50)

        # Validate parameters
        allowed_search_types = ["all", "images", "videos", "folders", "files"]
        if search_type not in allowed_search_types:
            search_type = "all"

        page, per_page, _ = validate_pagination_params(page, per_page, 150)

        # Perform search
        results = []
        if query:
            results = zip_manager.search_files(zip_id, query, search_type)

        # Pagination
        total_results = len(results)
        total_pages = math.ceil(total_results / per_page) if total_results > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_results = results[start_idx:end_idx]

        # Convert search results to items format for template
        items = []
        for result in paginated_results:
            item = _create_search_result_item(result, zip_id)
            items.append(item)

        return render_template(
            "search_results.html",
            items=items,
            query=query,
            search_type=search_type,
            zip_id=zip_id,
            zip_name=zip_info["name"],
            page=page,
            total_pages=total_pages,
            per_page=per_page,
            total_results=total_results,
        )

    return bp


def _create_item_dict(name, dir_content, zip_id, zip_manager, path):
    """Create item dictionary for template rendering"""
    item = {
        "name": name,
        "is_folder": dir_content is not None,
        "is_image": False,
        "is_video": False,
        "preview_image": None,
        "size": None,
        "extension": "",
    }

    if item["is_folder"]:
        item["type"] = "folder"
        # Get first image in this folder for preview
        folder_path = path + "/" + name if path else name
        first_image = zip_manager.get_first_image_in_folder(zip_id, folder_path)
        if first_image:
            item["preview_image"] = first_image
    else:
        # It's a file
        item["extension"] = os.path.splitext(name.lower())[1]
        item["is_image"] = is_image(name)
        item["is_video"] = is_video(name)
        if item["is_image"]:
            item["type"] = "image"
            item["preview_image"] = path + "/" + name if path else name
        elif item["is_video"]:
            item["type"] = "video"
            item["preview_image"] = path + "/" + name if path else name
        else:
            item["type"] = "file"

    return item


def _create_search_result_item(result, zip_id):
    """Create item dictionary for search result rendering"""
    item = {
        "name": result["name"],
        "path": result["path"],
        "directory": result["directory"],
        "is_folder": result["is_folder"],
        "is_image": result["is_image"],
        "is_video": result.get("is_video", False),
        "extension": result["extension"],
        "preview_image": None,
    }

    if item["is_folder"]:
        item["type"] = "folder"
    elif item["is_image"]:
        item["type"] = "image"
        item["preview_image"] = result["path"]
    elif item["is_video"]:
        item["type"] = "video"
        item["preview_image"] = result["path"]
    else:
        item["type"] = "file"

    return item


def _sort_items(items, sort_by, sort_order):
    """Sort items based on the specified criteria"""
    reverse = sort_order == "desc"

    if sort_by == "name":
        return sorted(
            items,
            key=lambda x: (not x["is_folder"], x["name"].lower()),
            reverse=reverse,
        )
    elif sort_by == "type":
        return sorted(
            items,
            key=lambda x: (
                not x["is_folder"],
                x.get("extension", "").lower(),
                x["name"].lower(),
            ),
            reverse=reverse,
        )
    elif sort_by == "date":
        # For now, sort by name as we don't have date info from ZIP
        return sorted(
            items,
            key=lambda x: (not x["is_folder"], x["name"].lower()),
            reverse=reverse,
        )

    return items
