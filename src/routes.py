"""
Route handlers for the ZIP file viewer application.
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
    redirect,
    url_for,
    flash,
    send_file,
    abort,
)
from flask_login import login_user, logout_user, login_required, current_user
from PIL import Image

from .auth import LoginForm
from .utils import is_image, validate_pagination_params, is_system_file


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
                except:
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
                except:
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
        allowed_search_types = ["all", "images", "folders", "files"]
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
        if item["is_image"]:
            item["type"] = "image"
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
        "extension": result["extension"],
        "preview_image": None,
    }

    if item["is_folder"]:
        item["type"] = "folder"
    elif item["is_image"]:
        item["type"] = "image"
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
