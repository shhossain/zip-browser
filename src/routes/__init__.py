"""
Routes package for ZIP Browser application.
Modular blueprints: auth, browse, video, search.
"""

from .auth import create_auth_routes
from .browse import create_browse_routes
from .video import create_video_routes
from .search import create_search_routes

__all__ = [
    'create_auth_routes',
    'create_browse_routes', 
    'create_video_routes',
    'create_search_routes',
]
