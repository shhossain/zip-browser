"""
Browse and file viewing routes.
"""

import os
import io
import math
import mimetypes
from flask import (
    Blueprint,
    request,
    jsonify,
    render_template,
    send_file,
    abort,
    redirect,
    url_for,
    flash,
)
from flask_login import login_required
from PIL import Image

from ..utils import is_image, is_video, validate_pagination_params, is_system_file
from ..archive_handlers import is_nested_archive
from ..cache_manager import cache_manager


def create_browse_routes(zip_manager, user_manager=None):
    """Create browsing and file viewing routes."""
    bp = Blueprint("browse", __name__)

    # Valid "open with" handler names and the MIME types they map to
    OPEN_WITH_HANDLERS = {
        "text": "text/plain",
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "pdf": "application/pdf",
        "html": "text/html",
        "archive": None,   # special: redirect to nested-archive flow
        "download": "application/octet-stream",
        "default": None,   # use the normal guessed MIME type
    }

    @bp.route("/zips")
    @login_required
    def zip_list():
        """List all available ZIP files."""
        return render_template(
            "zip_list.html", zip_files=zip_manager.get_all_zip_files()
        )

    @bp.route("/add_source", methods=["POST"])
    @login_required
    def add_source():
        """Add a new archive source (URL, magnet, local path, or .torrent upload)."""
        source = request.form.get("source", "").strip()
        uploaded = request.files.get("torrent_file")

        if uploaded and uploaded.filename:
            # Handle uploaded .torrent file
            import tempfile
            fname = os.path.basename(uploaded.filename)
            if not fname.lower().endswith(".torrent"):
                flash("Only .torrent files can be uploaded.")
                return redirect(url_for("browse.zip_list"))
            tmp_dir = tempfile.mkdtemp(prefix="zipbrowser_upload_")
            save_path = os.path.join(tmp_dir, fname)
            uploaded.save(save_path)
            source = save_path

        if not source:
            flash("Please provide a URL, magnet link, path, or upload a .torrent file.")
            return redirect(url_for("browse.zip_list"))

        # Expand ~ to home directory for local paths
        if not zip_manager.is_url(source) and not zip_manager.is_magnet(source):
            source = os.path.expanduser(source)

        # Validate: must be a URL, magnet link, or an existing local path
        is_url = zip_manager.is_url(source)
        is_magnet = zip_manager.is_magnet(source)
        is_local = not is_url and not is_magnet and os.path.exists(source)

        if not is_url and not is_magnet and not is_local:
            flash(f"Invalid source: not a URL, magnet link, or existing path.")
            return redirect(url_for("browse.zip_list"))

        before = set(zip_manager.get_all_zip_files().keys())
        zip_manager.initialize_zip_files(source)
        after = set(zip_manager.get_all_zip_files().keys())
        added = after - before

        if added:
            flash(f"Added {len(added)} new source(s).")
        else:
            flash("Source already loaded or contains no browsable content.")

        return redirect(url_for("browse.zip_list"))

    @bp.route("/remove_source/<zip_id>", methods=["POST"])
    @login_required
    def remove_source(zip_id):
        """Remove a loaded archive source."""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            flash("Source not found.")
            return redirect(url_for("browse.zip_list"))

        name = zip_info.get("name", zip_id)
        zfile = zip_info.get("zfile")
        if zfile:
            try:
                zfile.close()
            except Exception:
                pass

        del zip_manager.zip_files[zip_id]
        flash(f"Removed: {name}")
        return redirect(url_for("browse.zip_list"))

    @bp.route("/unlock/<zip_id>", methods=["POST"])
    @login_required
    def unlock_zip(zip_id):
        """Unlock a password-protected ZIP file."""
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
        print(f"Browsing ZIP: {zip_id}, info: {zip_info}, path: {path}")
        if not zip_info:
            abort(404)

        # Check if zip file requires password and is not unlocked
        if zip_info["requires_password"] and not zip_info["zfile"]:
            return render_template("zip_unlock.html", zip_info=zip_info, zip_id=zip_id)

        # Load zip file if not already loaded
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(500)

        # Check if the path points to a nested archive
        if path and zip_manager.is_item_archive(zip_id, path):
            nested_id, needs_password = zip_manager.open_nested_archive(zip_id, path)
            if nested_id is None:
                abort(500)
            from flask import redirect, url_for
            if needs_password:
                return render_template(
                    "zip_unlock.html",
                    zip_info=zip_manager.get_zip_info(nested_id),
                    zip_id=nested_id,
                )
            return redirect(url_for("browse.browse", zip_id=nested_id))

        cur_dir = zip_manager.get_dir_tree(zip_id, path)
        if cur_dir is None:
            return abort(404)

        # Register session for cache tracking
        session_id = request.cookies.get('session', 'default')
        cache_manager.register_session(session_id)

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

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        size = int(request.args.get("size", 100))
        allowed_sizes = [80, 100, 150, 200, 250]
        if size not in allowed_sizes:
            size = 100

        if is_system_file(path):
            abort(404)

        try:
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            data = zfile.read(path)
            img = Image.open(io.BytesIO(data))
            img.thumbnail((size, size))

            if img.mode in ("RGBA", "LA", "P"):
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

            if hasattr(zfile, "close"):
                zfile.close()

            return send_file(buf, mimetype="image/jpeg")
        except Exception as e:
            print(f"Thumbnail error for {zip_id}/{path}: {e}")
            abort(404)
        finally:
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

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # For URL-backed handlers, redirect to the direct URL
        direct_url = zip_manager.get_file_url(zip_id, path)
        if direct_url:
            from flask import redirect
            return redirect(direct_url)

        try:
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
            if "zfile" in locals() and hasattr(zfile, "close"):
                try:
                    zfile.close()
                except Exception:
                    pass

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

    @bp.route("/release-folder/<zip_id>/<path:path>", methods=["POST"])
    @login_required
    def release_folder(zip_id, path):
        """Release folder cache when user navigates away."""
        session_id = request.cookies.get('session', 'default')
        cache_manager.release_folder_cache(session_id, zip_id, path)
        return jsonify({"success": True})

    # ------------------------------------------------------------------
    # Open With — serve a file forced to a specific handler / MIME type
    # ------------------------------------------------------------------

    @bp.route("/open-with/<handler>/<zip_id>/<path:path>")
    @login_required
    def open_with(handler, zip_id, path):
        """Serve a file using the requested *handler* type.

        ``handler`` must be one of the keys in ``OPEN_WITH_HANDLERS``.
        """
        if handler not in OPEN_WITH_HANDLERS:
            abort(400)

        # "archive" handler → redirect to the browse route (nested archive flow)
        if handler == "archive":
            from flask import redirect, url_for
            return redirect(url_for("browse.browse", zip_id=zip_id, path=path))

        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)
        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        try:
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            data = zfile.read(path)

            if handler == "default":
                mime, _ = mimetypes.guess_type(path)
                mime = mime or "application/octet-stream"
            else:
                mime = OPEN_WITH_HANDLERS[handler]

            return send_file(io.BytesIO(data), mimetype=mime)
        except Exception:
            abort(404)
        finally:
            if "zfile" in locals() and hasattr(zfile, "close"):
                try:
                    zfile.close()
                except Exception:
                    pass

    @bp.route("/open-with-options/<zip_id>/<path:path>")
    @login_required
    def open_with_options(zip_id, path):
        """Return the list of available open-with handlers for a file,
        together with the user's saved preference (if any)."""
        from flask_login import current_user

        ext = os.path.splitext(path.lower())[1]

        saved_handler = None
        if user_manager and hasattr(current_user, 'username'):
            ow_prefs = user_manager.get_open_with_prefs(current_user.username)
            saved_handler = ow_prefs.get(ext)

        handlers = [
            {"id": "default", "label": "Default"},
            {"id": "text", "label": "Text"},
            {"id": "image", "label": "Image"},
            {"id": "video", "label": "Video"},
            {"id": "audio", "label": "Audio"},
            {"id": "pdf", "label": "PDF"},
            {"id": "html", "label": "HTML"},
            {"id": "download", "label": "Download"},
        ]

        if is_nested_archive(path):
            handlers.append({"id": "archive", "label": "Browse as Archive"})

        return jsonify({
            "handlers": handlers,
            "saved": saved_handler,
            "extension": ext,
        })

    @bp.route("/save-open-with", methods=["POST"])
    @login_required
    def save_open_with():
        """Save the user's preferred open-with handler for an extension."""
        from flask_login import current_user

        if not user_manager:
            return jsonify({"success": False, "error": "Preferences not available"}), 400

        data = request.get_json(silent=True) or {}
        extension = data.get("extension", "").lower().strip()
        handler = data.get("handler", "").strip()

        if not extension or handler not in OPEN_WITH_HANDLERS:
            return jsonify({"success": False, "error": "Invalid parameters"}), 400

        ok = user_manager.set_open_with_pref(current_user.username, extension, handler)
        return jsonify({"success": ok})

    return bp


def _create_item_dict(name, dir_content, zip_id, zip_manager, path):
    """Create item dictionary for template rendering."""
    is_archive = dir_content == "__archive__"
    item = {
        "name": name,
        "is_folder": isinstance(dir_content, dict),
        "is_archive": is_archive,
        "is_image": False,
        "is_video": False,
        "preview_image": None,
        "size": None,
        "extension": "",
    }

    if item["is_folder"]:
        item["type"] = "folder"
        folder_path = path + "/" + name if path else name
        first_image = zip_manager.get_first_image_in_folder(zip_id, folder_path)
        if first_image:
            item["preview_image"] = first_image
    elif is_archive:
        item["type"] = "archive"
        item["extension"] = os.path.splitext(name.lower())[1]
    else:
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


def _sort_items(items, sort_by, sort_order):
    """Sort items based on the specified criteria."""
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
        return sorted(
            items,
            key=lambda x: (not x["is_folder"], x["name"].lower()),
            reverse=reverse,
        )

    return items
