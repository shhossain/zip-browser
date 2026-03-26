"""FFmpeg utility functions for video probing, transcoding, thumbnails, and subtitles."""

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

# Thread pool for FFmpeg operations (max 2 concurrent)
FFMPEG_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ffmpeg")
FFMPEG_LOCK = threading.Lock()

# Subtitle codecs that can be converted to WebVTT
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}


def check_ffmpeg_available():
    """Check if FFmpeg is available on the system."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


FFMPEG_AVAILABLE = check_ffmpeg_available()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def run_ffmpeg(args, timeout=60):
    """Run FFmpeg with proper timeout handling. Returns (returncode, stdout, stderr)."""
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
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


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def probe_full_info(input_path):
    """Probe video for duration, codecs, audio tracks, and subtitle tracks.

    Returns a dict::

        {
            "duration": float | None,
            "vcodec": str | None,
            "audio_tracks": [{"index": int, "codec": str, "lang": str|None, "title": str|None, "label": str}, ...],
            "subtitle_tracks": [{"index": int, "codec": str, "lang": str|None, "title": str|None, "label": str}, ...],
        }
    """
    args = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        input_path,
    ]
    returncode, stdout, _ = run_ffmpeg(args, timeout=10)
    empty = {"duration": None, "vcodec": None, "audio_tracks": [], "subtitle_tracks": []}
    if returncode != 0:
        return empty
    try:
        info = json.loads(stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return empty

    duration_raw = info.get("format", {}).get("duration")
    try:
        duration = float(duration_raw) if duration_raw else None
    except (ValueError, TypeError):
        duration = None

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    vcodec = video.get("codec_name") if video else None

    audio_tracks = []
    for i, s in enumerate(s for s in streams if s.get("codec_type") == "audio"):
        tags = s.get("tags", {})
        lang = tags.get("language")
        title = tags.get("title")
        label = " - ".join(filter(None, [lang, title])) or f"Audio {i + 1}"
        audio_tracks.append({
            "index": s["index"], "codec": s.get("codec_name"),
            "lang": lang, "title": title, "label": label,
        })

    subtitle_tracks = []
    for i, s in enumerate(s for s in streams if s.get("codec_type") == "subtitle"):
        tags = s.get("tags", {})
        lang = tags.get("language")
        title = tags.get("title")
        label = " - ".join(filter(None, [lang, title])) or f"Subtitle {i + 1}"
        subtitle_tracks.append({
            "index": s["index"], "codec": s.get("codec_name"),
            "lang": lang, "title": title, "label": label,
        })

    return {
        "duration": duration,
        "vcodec": vcodec,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
    }


def get_duration(input_path):
    """Get video duration in seconds."""
    args = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    returncode, stdout, _ = run_ffmpeg(args, timeout=5)
    if returncode == 0:
        try:
            return float(stdout.decode().strip())
        except (ValueError, UnicodeDecodeError):
            pass
    return 0


# ---------------------------------------------------------------------------
# FFmpeg argument builders
# ---------------------------------------------------------------------------

def _video_codec_args(vcodec):
    if vcodec in ("h264",):
        return ["-c:v", "copy"]
    return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "28"]


def _audio_codec_args(acodec):
    if acodec in ("aac",):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]


def build_stream_args(input_path, info=None, audio_track_idx=0, seek_time=0):
    """Build FFmpeg args for piped MP4 streaming output.

    *info* should come from ``probe_full_info``.  *audio_track_idx* selects
    which audio track (by position in the audio_tracks list) to include.
    *seek_time* (seconds) adds ``-ss`` for seeking.
    """
    if info is None:
        info = probe_full_info(input_path)

    audio_tracks = info.get("audio_tracks", [])
    audio = audio_tracks[audio_track_idx] if audio_track_idx < len(audio_tracks) else (audio_tracks[0] if audio_tracks else None)

    args = ["ffmpeg"]
    if seek_time > 0:
        args += ["-ss", str(seek_time)]
    args += ["-i", input_path]

    # Map video + chosen audio
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]

    args += _video_codec_args(info.get("vcodec"))
    args += _audio_codec_args(audio["codec"] if audio else None)
    args += ["-sn"]  # strip subs from video stream

    args += [
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "-threads", "2",
        "pipe:1",
    ]
    return args


# ---------------------------------------------------------------------------
# Subtitle extraction
# ---------------------------------------------------------------------------

def extract_subtitles(input_path, output_dir, subtitle_tracks):
    """Extract all text-based subtitle tracks to WebVTT files.

    Files are written as ``sub_0.vtt``, ``sub_1.vtt``, … inside *output_dir*.
    Returns a list of (track_position, vtt_path) tuples that succeeded.
    """
    text_tracks = [
        (i, t) for i, t in enumerate(subtitle_tracks)
        if t.get("codec") in TEXT_SUB_CODECS
    ]
    if not text_tracks:
        return []

    args = ["ffmpeg", "-hide_banner", "-v", "error", "-i", input_path]
    paths = []
    for pos, track in text_tracks:
        vtt_path = os.path.join(output_dir, f"sub_{pos}.vtt")
        args += ["-map", f"0:{track['index']}", "-c:s", "webvtt", "-y", vtt_path]
        paths.append((pos, vtt_path))

    run_ffmpeg(args, timeout=120)

    return [(pos, p) for pos, p in paths if os.path.exists(p)]


# ---------------------------------------------------------------------------
# Thumbnails / previews
# ---------------------------------------------------------------------------

def extract_thumbnail(input_path, output_path, seek_time=0, timeout=15):
    """Extract a single JPEG thumbnail from video."""
    args = [
        "ffmpeg",
        "-ss", str(seek_time),
        "-i", input_path,
        "-vframes", "1",
        "-vf", "scale=320:-1",
        "-q:v", "5",
        "-y",
        output_path,
    ]
    return run_ffmpeg(args, timeout=timeout)


def create_gif_preview(input_path, output_path, timeout=30):
    """Create a short animated GIF preview."""
    args = [
        "ffmpeg",
        "-i", input_path,
        "-vf", "fps=3,scale=160:-1:flags=fast_bilinear",
        "-t", "4",
        "-y",
        output_path,
    ]
    return run_ffmpeg(args, timeout=timeout)
