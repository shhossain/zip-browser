"""
Search routes for ZIP file content search.
"""

import math
from flask import (
    Blueprint,
    request,
    render_template,
    abort,
)
from flask_login import login_required

from ..utils import validate_pagination_params


def create_search_routes(zip_manager):
    """Create search routes."""
    bp = Blueprint("search", __name__)

    @bp.route("/search/<zip_id>")
    @login_required
    def search(zip_id):
        """Search for files in a ZIP archive."""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        if zip_info["requires_password"] and not zip_info["zfile"]:
            return render_template("zip_unlock.html", zip_info=zip_info, zip_id=zip_id)

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(500)

        query = request.args.get("q", "").strip()
        search_type = request.args.get("type", "all")
        page = request.args.get("page", 1)
        per_page = request.args.get("per_page", 50)

        allowed_search_types = ["all", "images", "videos", "folders", "files"]
        if search_type not in allowed_search_types:
            search_type = "all"

        page, per_page, _ = validate_pagination_params(page, per_page, 150)

        results = []
        if query:
            results = zip_manager.search_files(zip_id, query, search_type)

        total_results = len(results)
        total_pages = math.ceil(total_results / per_page) if total_results > 0 else 1
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_results = results[start_idx:end_idx]

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


def _create_search_result_item(result, zip_id):
    """Create item dictionary for search result rendering."""
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
