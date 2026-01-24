"""
Video processing routes for streaming, transcoding, and thumbnails.
Uses non-blocking FFmpeg with improved timeout handling.
"""

import os
import subprocess
import threading
from flask import (
    Blueprint,
    request,
    jsonify,
    redirect,
    url_for,
    send_file,
    abort,
    Response,
)
from flask_login import login_required
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from ..utils import needs_transcoding
from ..cache_manager import cache_manager


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

# Use a thread pool for FFmpeg operations (max 2 concurrent)
FFMPEG_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ffmpeg")

# Lock for FFmpeg operations
FFMPEG_LOCK = threading.Lock()


def _run_ffmpeg_with_timeout(args, timeout=60):
    """Run FFmpeg with proper timeout handling."""
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return -1, b"", b"Timeout"
    except Exception as e:
        return -1, b"", str(e).encode()


def _transcode_video_sync(input_path, output_path):
    """Transcode video synchronously with timeout."""
    args = [
        "ffmpeg",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",  # Fastest preset for quick start
        "-tune", "zerolatency",  # Optimize for streaming
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart+frag_keyframe+empty_moov",  # Enable streaming
        "-threads", "2",
        "-y",
        output_path
    ]
    return _run_ffmpeg_with_timeout(args, timeout=120)


def _extract_thumbnail_sync(input_path, output_path, seek_time=0):
    """Extract a single thumbnail from video."""
    args = [
        "ffmpeg",
        "-ss", str(seek_time),
        "-i", input_path,
        "-vframes", "1",
        "-vf", "scale=320:-1",
        "-q:v", "5",  # Lower quality for speed
        "-y",
        output_path
    ]
    return _run_ffmpeg_with_timeout(args, timeout=15)


def _create_gif_preview_sync(input_path, output_path, duration=10):
    """Create a simple GIF preview - optimized for speed."""
    # Simpler, faster GIF generation
    args = [
        "ffmpeg",
        "-i", input_path,
        "-vf", "fps=3,scale=160:-1:flags=fast_bilinear",
        "-t", "4",  # Only 4 seconds
        "-y",
        output_path
    ]
    return _run_ffmpeg_with_timeout(args, timeout=30)


def _get_video_duration(input_path):
    """Get video duration quickly."""
    args = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path
    ]
    returncode, stdout, _ = _run_ffmpeg_with_timeout(args, timeout=5)
    if returncode == 0:
        try:
            return float(stdout.decode().strip())
        except (ValueError, UnicodeDecodeError):
            pass
    return 0


def create_video_routes(zip_manager):
    """Create video-related routes."""
    bp = Blueprint("video", __name__)

    @bp.route("/stream/<zip_id>/<path:path>")
    @login_required
    def stream_video(zip_id, path):
        """Stream video with real-time FFmpeg transcoding for unsupported formats."""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info:
            abort(404)

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        # For browser-native formats, serve directly
        if not needs_transcoding(path):
            return redirect(url_for('browse.view_file', zip_id=zip_id, path=path))

        if not FFMPEG_AVAILABLE:
            return redirect(url_for('browse.view_file', zip_id=zip_id, path=path))

        # Get session ID for tracking
        session_id = request.cookies.get('session', 'default')
        
        # Check cache first - if already transcoded, serve from cache
        cache_path = cache_manager.get_video_cache_path(zip_id, path)
        
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="video/mp4", conditional=True)

        # Extract video from zip first
        zfile = zip_manager.get_zip_file_object(zip_id)
        if not zfile:
            abort(404)

        video_data = zfile.read(path)
        if hasattr(zfile, "close"):
            zfile.close()

        # Create temp input file
        ext = os.path.splitext(path)[1]
        temp_input = cache_manager.get_temp_path(ext)
        
        with open(temp_input, 'wb') as f:
            f.write(video_data)

        def generate_stream():
            """Generator that streams FFmpeg output directly."""
            process = None
            try:
                # Use fragmented MP4 for live streaming (no seek required)
                args = [
                    "ffmpeg",
                    "-i", temp_input,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-crf", "28",
                    "-c:a", "aac",
                    "-b:a", "96k",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-f", "mp4",
                    "-threads", "2",
                    "pipe:1"  # Output to stdout
                ]
                
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=8192
                )
                
                # Stream chunks as they become available
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    yield chunk
                    
            except Exception as e:
                print(f"Streaming error: {e}")
            finally:
                if process:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except Exception:
                        process.kill()
                # Clean up temp file
                try:
                    os.unlink(temp_input)
                except Exception:
                    pass

        return Response(
            generate_stream(),
            mimetype="video/mp4",
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "no-cache",
            }
        )

    @bp.route("/video-info/<zip_id>/<path:path>")
    @login_required
    def video_info(zip_id, path):
        """Get video information including whether transcoding is needed."""
        return jsonify({
            "needs_transcoding": needs_transcoding(path),
            "ffmpeg_available": FFMPEG_AVAILABLE,
            "stream_url": url_for('video.stream_video', zip_id=zip_id, path=path) if needs_transcoding(path) and FFMPEG_AVAILABLE else url_for('browse.view_file', zip_id=zip_id, path=path),
            "direct_url": url_for('browse.view_file', zip_id=zip_id, path=path)
        })

    @bp.route("/video-thumb/<zip_id>/<path:path>")
    @login_required
    def video_thumbnail(zip_id, path):
        """Generate a static thumbnail for a video."""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info or not FFMPEG_AVAILABLE:
            abort(404)

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        session_id = request.cookies.get('session', 'default')
        cache_path = cache_manager.get_thumb_cache_path(zip_id, path, "static")
        
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="image/jpeg")

        # Don't block on thumbnail generation - use quick timeout
        if not FFMPEG_LOCK.acquire(timeout=2):
            abort(503)

        try:
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            video_data = zfile.read(path)
            if hasattr(zfile, "close"):
                zfile.close()

            ext = os.path.splitext(path)[1]
            temp_input = cache_manager.get_temp_path(ext)
            
            try:
                with open(temp_input, 'wb') as f:
                    f.write(video_data)

                # Get duration for seek position
                duration = _get_video_duration(temp_input)
                seek_time = min(duration * 0.1, 1.0) if duration > 0 else 0

                # Extract thumbnail with timeout
                future = FFMPEG_EXECUTOR.submit(
                    _extract_thumbnail_sync, temp_input, cache_path, seek_time
                )
                
                try:
                    returncode, _, _ = future.result(timeout=15)
                except FuturesTimeoutError:
                    future.cancel()
                    abort(504)

                if returncode == 0 and cache_manager.cache_exists(cache_path):
                    cache_manager.track_file_access(session_id, cache_path)
                    return send_file(cache_path, mimetype="image/jpeg")
                else:
                    abort(500)

            finally:
                try:
                    os.unlink(temp_input)
                except Exception:
                    pass

        except Exception as e:
            print(f"Video thumbnail error: {e}")
            abort(500)
        finally:
            FFMPEG_LOCK.release()

    @bp.route("/video-thumb-gif/<zip_id>/<path:path>")
    @login_required
    def video_thumbnail_gif(zip_id, path):
        """Generate an animated GIF preview for a video."""
        zip_info = zip_manager.get_zip_info(zip_id)
        if not zip_info or not FFMPEG_AVAILABLE:
            abort(404)

        if not zip_info["zfile"]:
            if not zip_manager.load_zip_file(zip_id):
                abort(404)

        session_id = request.cookies.get('session', 'default')
        cache_path = cache_manager.get_thumb_cache_path(zip_id, path, "gif")
        
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="image/gif")

        # Quick timeout for GIF generation
        if not FFMPEG_LOCK.acquire(timeout=2):
            abort(503)

        try:
            zfile = zip_manager.get_zip_file_object(zip_id)
            if not zfile:
                abort(404)

            video_data = zfile.read(path)
            if hasattr(zfile, "close"):
                zfile.close()

            ext = os.path.splitext(path)[1]
            temp_input = cache_manager.get_temp_path(ext)
            
            try:
                with open(temp_input, 'wb') as f:
                    f.write(video_data)

                duration = _get_video_duration(temp_input)

                future = FFMPEG_EXECUTOR.submit(
                    _create_gif_preview_sync, temp_input, cache_path, duration
                )
                
                try:
                    returncode, _, _ = future.result(timeout=30)
                except FuturesTimeoutError:
                    future.cancel()
                    abort(504)

                if returncode == 0 and cache_manager.cache_exists(cache_path):
                    cache_manager.track_file_access(session_id, cache_path)
                    return send_file(cache_path, mimetype="image/gif")
                else:
                    abort(500)

            finally:
                try:
                    os.unlink(temp_input)
                except Exception:
                    pass

        except Exception as e:
            print(f"Video GIF error: {e}")
            abort(500)
        finally:
            FFMPEG_LOCK.release()

    @bp.route("/release-video/<zip_id>/<path:path>", methods=["POST"])
    @login_required
    def release_video(zip_id, path):
        """Release video cache when user closes video player."""
        session_id = request.cookies.get('session', 'default')
        cache_manager.release_video(session_id, zip_id, path)
        return jsonify({"success": True})

    return bp
