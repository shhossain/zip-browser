"""
Video processing routes for streaming, transcoding, thumbnails,
audio track selection, and subtitle extraction.
"""

import os
import subprocess
import threading
import time
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
    extract_single_subtitle,
    extract_thumbnail,
    create_gif_preview,
    get_subtitle_worker,
)


def _is_temp_file(path):
    """Return True if *path* is a local temp file (not a remote URL)."""
    return path and not path.startswith(("http://", "https://"))


def _extract_to_tempfile(zip_manager, zip_id, path):
    """Extract a file from the archive to a temp file. Returns path or None.

    For URL-backed handlers the file is *not* re-downloaded; instead the
    direct URL is returned so the caller can pass it straight to ffmpeg.
    """
    # If the handler can give us a direct URL, prefer that (ffmpeg can
    # read from HTTP directly, saving a full download round-trip).
    direct_url = zip_manager.get_file_url(zip_id, path)
    if direct_url:
        return direct_url

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
        quality = request.args.get("quality", "auto")
        if quality not in ("auto", "low", "medium", "high"):
            quality = "auto"
        info = probe_full_info(temp_input)

        # Piggyback subtitle extraction for remote URLs on initial play.
        # This extracts VTT files as extra FFmpeg outputs within the same
        # transcode pass, avoiding a separate full-file download (~2 min).
        sub_dir = None
        piggybacking_subs = False
        if (not _is_temp_file(temp_input)
                and seek_time == 0
                and info.get("subtitle_tracks")):
            sub_dir = cache_manager.get_sub_cache_dir(zip_id, path)
            os.makedirs(sub_dir, exist_ok=True)
            marker = os.path.join(sub_dir, ".extracting")
            has_any_sub = any(
                os.path.exists(os.path.join(sub_dir, f"sub_{i}.vtt"))
                for i in range(len(info["subtitle_tracks"]))
            )
            if not has_any_sub and not os.path.exists(marker):
                piggybacking_subs = True
                try:
                    with open(marker, "w") as f:
                        f.write(str(os.getpid()))
                except OSError:
                    pass

        ffmpeg_args = build_stream_args(
            temp_input, info=info, audio_track_idx=audio_track_idx,
            seek_time=seek_time, quality=quality,
            sub_output_dir=sub_dir if piggybacking_subs else None,
            subtitle_tracks=info.get("subtitle_tracks") if piggybacking_subs else None,
        )

        def generate():
            process = None
            try:
                process = subprocess.Popen(
                    ffmpeg_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=256 * 1024,
                )
                while True:
                    chunk = process.stdout.read(64 * 1024)
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
                if _is_temp_file(temp_input):
                    try:
                        os.unlink(temp_input)
                    except Exception:
                        pass
                # Remove piggyback marker so subtitle endpoint knows
                # extraction finished (or was interrupted).
                if piggybacking_subs and sub_dir:
                    try:
                        os.unlink(os.path.join(sub_dir, ".extracting"))
                    except OSError:
                        pass

        return Response(
            generate(),
            mimetype="video/mp4",
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
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

            # Kick off subtitle extraction — synchronous for local files
            # (fast, disk I/O).  For remote URLs, start a persistent
            # SubtitleWorker per track that streams SRT to memory.
            sub_dir = cache_manager.get_sub_cache_dir(zip_id, path)
            if info["subtitle_tracks"]:
                os.makedirs(sub_dir, exist_ok=True)
                if _is_temp_file(temp_input):
                    # Local files: extract immediately (fast, disk I/O only).
                    extract_subtitles(temp_input, sub_dir, info["subtitle_tracks"])
                else:
                    # Remote URLs: start subtitle workers (VLC-style
                    # persistent demux).  Each worker runs FFmpeg reading
                    # the remote URL and parsing SRT to memory.
                    for i, t in enumerate(info["subtitle_tracks"]):
                        if t.get("codec") in TEXT_SUB_CODECS:
                            get_subtitle_worker(
                                temp_input, t["index"], auto_start=True,
                            )

            is_remote = not _is_temp_file(temp_input)
            base_response["duration"] = info.get("duration")
            base_response["audio_tracks"] = [
                {"index": i, "label": t["label"], "lang": t.get("lang"), "codec": t.get("codec")}
                for i, t in enumerate(info["audio_tracks"])
            ]
            sub_track_list = []
            for i, t in enumerate(info["subtitle_tracks"]):
                is_text = t.get("codec") in TEXT_SUB_CODECS
                entry = {
                    "index": i,
                    "label": t["label"],
                    "lang": t.get("lang"),
                    "codec": t.get("codec"),
                }
                if is_text and is_remote:
                    # Remote: use streaming subs-at endpoint (worker-backed)
                    entry["subs_at_url"] = url_for(
                        "video.subs_at", zip_id=zip_id, path=path,
                        stream_index=t["index"],
                    )
                    entry["vtt_url"] = None
                elif is_text:
                    # Local: use pre-extracted VTT file
                    entry["vtt_url"] = url_for(
                        "video.subtitle_track", zip_id=zip_id,
                        path=path, index=i,
                    )
                    entry["subs_at_url"] = None
                else:
                    entry["vtt_url"] = None
                    entry["subs_at_url"] = None
                sub_track_list.append(entry)
            base_response["subtitle_tracks"] = sub_track_list
        finally:
            if _is_temp_file(temp_input):
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
        """Serve an extracted WebVTT subtitle file.

        For remote URLs subtitles are piggybacked on the video transcode
        and written progressively.  This endpoint serves whatever is
        available; the client re-fetches every few seconds for new cues.
        """
        sub_dir = cache_manager.get_sub_cache_dir(zip_id, path)
        vtt_file = os.path.join(sub_dir, f"sub_{index}.vtt")
        marker = os.path.join(sub_dir, ".extracting")

        def _serve():
            """Return the VTT file with an extraction-in-progress header."""
            resp = send_file(vtt_file, mimetype="text/vtt")
            if os.path.exists(marker):
                resp.headers["X-Sub-Extracting"] = "1"
                resp.headers["Cache-Control"] = "no-store"
            return resp

        # Serve immediately if the file already has content.
        if os.path.exists(vtt_file) and os.path.getsize(vtt_file) > 0:
            return _serve()

        # Wait briefly for piggyback extraction to start writing.
        for _ in range(20):  # up to 10 s
            time.sleep(0.5)
            if os.path.exists(vtt_file) and os.path.getsize(vtt_file) > 0:
                return _serve()
            # No marker means nothing is running — stop waiting.
            if not os.path.exists(marker):
                break

        # On-demand fallback: extract just this one track.
        if not os.path.exists(vtt_file):
            temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
            if not temp_input:
                abort(404)
            try:
                info = probe_full_info(temp_input)
                os.makedirs(sub_dir, exist_ok=True)
                sub_tracks = info.get("subtitle_tracks", [])
                if index < len(sub_tracks):
                    track = sub_tracks[index]
                    if track.get("codec") in TEXT_SUB_CODECS:
                        timeout = 600 if not _is_temp_file(temp_input) else 120
                        extract_single_subtitle(
                            temp_input, vtt_file, track["index"],
                            timeout=timeout,
                        )
            finally:
                if _is_temp_file(temp_input):
                    try:
                        os.unlink(temp_input)
                    except Exception:
                        pass

        if os.path.exists(vtt_file) and os.path.getsize(vtt_file) > 0:
            return _serve()

        # Extraction in progress but file not yet created — return an
        # empty VTT so the client keeps refreshing instead of a hard 404.
        if os.path.exists(marker):
            return Response(
                "WEBVTT\n\n", mimetype="text/vtt",
                headers={"X-Sub-Extracting": "1", "Cache-Control": "no-store"},
            )

        abort(404)

    # ------------------------------------------------------------------
    # Streaming subtitle cues (VLC-style worker-backed)
    # ------------------------------------------------------------------

    @bp.route("/video-subs-at/<zip_id>/<int:stream_index>/<path:path>")
    @login_required
    def subs_at(zip_id, stream_index, path):
        """Return subtitle cues near a given timestamp as JSON.

        Query params:
            t     – playback time in seconds (required)
            window – seconds around *t* to return (default 30)

        The endpoint is backed by a persistent SubtitleWorker that
        incrementally parses SRT from FFmpeg stdout.  Responses are
        instant (O(log n) bisect lookup, no disk I/O).
        """
        t = request.args.get("t", 0, type=float)
        window = request.args.get("window", 30, type=float)

        # Find or create the worker.
        temp_input = _extract_to_tempfile(zip_manager, zip_id, path)
        if not temp_input:
            abort(404)

        worker = get_subtitle_worker(temp_input, stream_index, auto_start=True)

        # Don't restart the worker — FFmpeg's -ss doesn't work for
        # subtitle-only extraction from remote MKV over HTTP, so
        # restarting would just kill progress and start from 0 again.
        # Instead, return whatever cues are available now; the client
        # will poll again in a couple of seconds.

        cues = worker.get_cues_at(t, window=window)
        return jsonify({
            "cues": cues,
            "total": worker.cue_count,
            "max_time": worker.max_parsed_time,
            "done": worker.done,
        })

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
                if _is_temp_file(temp_input):
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
                if _is_temp_file(temp_input):
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
