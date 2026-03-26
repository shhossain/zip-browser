"""
Video processing routes for streaming, transcoding, thumbnails,
audio track selection, and subtitle extraction.
"""

import os
import subprocess
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
from concurrent.futures import TimeoutError as FuturesTimeoutError

from ..utils import needs_transcoding
from ..cache_manager import cache_manager
from ..ffmpeg_utils import (
    FFMPEG_AVAILABLE,
    FFMPEG_EXECUTOR,
    FFMPEG_LOCK,
    TEXT_SUB_CODECS,
    probe_full_info,
    get_duration,
    build_stream_args,
    extract_subtitles,
    extract_thumbnail,
    create_gif_preview,
)


def _extract_to_tempfile(zip_manager, zip_id, path):
    """Extract a file from the archive to a temp file. Returns path or None."""
    zip_info = zip_manager.get_zip_info(zip_id)
    if not zip_info:
        return None
    if not zip_info["zfile"] and not zip_manager.load_zip_file(zip_id):
        return None

    zfile = zip_manager.get_zip_file_object(zip_id)
    if not zfile:
        return None

    data = zfile.read(path)
    if hasattr(zfile, "close"):
        zfile.close()

    ext = os.path.splitext(path)[1]
    temp_path = cache_manager.get_temp_path(ext)
    with open(temp_path, "wb") as f:
        f.write(data)
    return temp_path


def create_video_routes(zip_manager):
    """Create video-related routes."""
    bp = Blueprint("video", __name__)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    @bp.route("/stream/<zip_id>/<path:path>")
    @login_required
    def stream_video(zip_id, path):
        """Stream video with real-time FFmpeg transcoding."""
        if not needs_transcoding(path) or not FFMPEG_AVAILABLE:
            return redirect(url_for("browse.view_file", zip_id=zip_id, path=path))

        session_id = request.cookies.get("session", "default")
        cache_path = cache_manager.get_video_cache_path(zip_id, path)
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="video/mp4", conditional=True)

        temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
        if not temp_input:
            abort(404)

        audio_track_idx = request.args.get("audio", 0, type=int)
        seek_time = request.args.get("ss", 0, type=float)
        info = probe_full_info(temp_input)
        ffmpeg_args = build_stream_args(
            temp_input, info=info, audio_track_idx=audio_track_idx, seek_time=seek_time
        )

        def generate():
            process = None
            try:
                process = subprocess.Popen(
                    ffmpeg_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=8192,
                )
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
                try:
                    os.unlink(temp_input)
                except Exception:
                    pass

        return Response(
            generate(),
            mimetype="video/mp4",
            headers={"Content-Type": "video/mp4", "Cache-Control": "no-cache"},
        )

    # ------------------------------------------------------------------
    # Video info (tracks, codecs, URLs)
    # ------------------------------------------------------------------

    @bp.route("/video-info/<zip_id>/<path:path>")
    @login_required
    def video_info(zip_id, path):
        """Return detailed video info: codecs, audio tracks, subtitle tracks."""
        transcode = needs_transcoding(path)
        can_stream = transcode and FFMPEG_AVAILABLE

        base_response = {
            "needs_transcoding": transcode,
            "ffmpeg_available": FFMPEG_AVAILABLE,
            "stream_url": (
                url_for("video.stream_video", zip_id=zip_id, path=path)
                if can_stream
                else url_for("browse.view_file", zip_id=zip_id, path=path)
            ),
            "direct_url": url_for("browse.view_file", zip_id=zip_id, path=path),
            "duration": None,
            "audio_tracks": [],
            "subtitle_tracks": [],
        }

        if not can_stream:
            return jsonify(base_response)

        # Probe the file for tracks
        temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
        if not temp_input:
            return jsonify(base_response)

        try:
            info = probe_full_info(temp_input)

            # Extract subtitles in the background and cache the VTT files
            sub_dir = cache_manager.get_sub_cache_dir(zip_id, path)
            os.makedirs(sub_dir, exist_ok=True)
            extract_subtitles(temp_input, sub_dir, info["subtitle_tracks"])

            base_response["duration"] = info.get("duration")
            base_response["audio_tracks"] = [
                {"index": i, "label": t["label"], "lang": t.get("lang"), "codec": t.get("codec")}
                for i, t in enumerate(info["audio_tracks"])
            ]
            base_response["subtitle_tracks"] = [
                {
                    "index": i,
                    "label": t["label"],
                    "lang": t.get("lang"),
                    "codec": t.get("codec"),
                    "vtt_url": (
                        url_for("video.subtitle_track", zip_id=zip_id, path=path, index=i)
                        if t.get("codec") in TEXT_SUB_CODECS
                        else None
                    ),
                }
                for i, t in enumerate(info["subtitle_tracks"])
            ]
        finally:
            try:
                os.unlink(temp_input)
            except Exception:
                pass

        return jsonify(base_response)

    # ------------------------------------------------------------------
    # Subtitle serving
    # ------------------------------------------------------------------

    @bp.route("/video-sub/<zip_id>/<int:index>/<path:path>")
    @login_required
    def subtitle_track(zip_id, index, path):
        """Serve an extracted WebVTT subtitle file."""
        sub_dir = cache_manager.get_sub_cache_dir(zip_id, path)
        vtt_file = os.path.join(sub_dir, f"sub_{index}.vtt")
        if not os.path.exists(vtt_file):
            abort(404)
        return send_file(vtt_file, mimetype="text/vtt")

    # ------------------------------------------------------------------
    # Thumbnails
    # ------------------------------------------------------------------

    @bp.route("/video-thumb/<zip_id>/<path:path>")
    @login_required
    def video_thumbnail(zip_id, path):
        """Generate a static JPEG thumbnail for a video."""
        if not FFMPEG_AVAILABLE:
            abort(404)

        session_id = request.cookies.get("session", "default")
        cache_path = cache_manager.get_thumb_cache_path(zip_id, path, "static")
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="image/jpeg")

        if not FFMPEG_LOCK.acquire(timeout=2):
            abort(503)

        try:
            temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
            if not temp_input:
                abort(404)
            try:
                duration = get_duration(temp_input)
                seek = min(duration * 0.1, 1.0) if duration > 0 else 0

                future = FFMPEG_EXECUTOR.submit(extract_thumbnail, temp_input, cache_path, seek)
                try:
                    rc, _, _ = future.result(timeout=15)
                except FuturesTimeoutError:
                    future.cancel()
                    abort(504)

                if rc == 0 and cache_manager.cache_exists(cache_path):
                    cache_manager.track_file_access(session_id, cache_path)
                    return send_file(cache_path, mimetype="image/jpeg")
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
        if not FFMPEG_AVAILABLE:
            abort(404)

        session_id = request.cookies.get("session", "default")
        cache_path = cache_manager.get_thumb_cache_path(zip_id, path, "gif")
        if cache_manager.cache_exists(cache_path):
            cache_manager.track_file_access(session_id, cache_path)
            return send_file(cache_path, mimetype="image/gif")

        if not FFMPEG_LOCK.acquire(timeout=2):
            abort(503)

        try:
            temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
            if not temp_input:
                abort(404)
            try:
                future = FFMPEG_EXECUTOR.submit(create_gif_preview, temp_input, cache_path)
                try:
                    rc, _, _ = future.result(timeout=30)
                except FuturesTimeoutError:
                    future.cancel()
                    abort(504)

                if rc == 0 and cache_manager.cache_exists(cache_path):
                    cache_manager.track_file_access(session_id, cache_path)
                    return send_file(cache_path, mimetype="image/gif")
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @bp.route("/release-video/<zip_id>/<path:path>", methods=["POST"])
    @login_required
    def release_video(zip_id, path):
        """Release video cache when user closes video player."""
        session_id = request.cookies.get("session", "default")
        cache_manager.release_video(session_id, zip_id, path)
        return jsonify({"success": True})

    return bp
