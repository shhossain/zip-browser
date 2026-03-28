"""FFmpeg utility functions for video probing, transcoding, thumbnails, and subtitles."""

import json
import os
import re
import subprocess
import threading
import time
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor

# Thread pool for FFmpeg operations (max 4 concurrent)
FFMPEG_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ffmpeg")
FFMPEG_LOCK = threading.Lock()

# Probe result cache — avoids re-probing the same file within a short window.
# Keyed by input_path, value is (timestamp, result_dict).
_probe_cache: dict[str, tuple[float, dict]] = {}
_probe_cache_lock = threading.Lock()
_PROBE_CACHE_TTL = 300  # seconds

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

    Results are cached for ``_PROBE_CACHE_TTL`` seconds so that consecutive
    calls (e.g. ``video_info`` then ``stream_video``) don't re-probe the
    same remote URL.

    Returns a dict::

        {
            "duration": float | None,
            "vcodec": str | None,
            "audio_tracks": [{"index": int, "codec": str, "lang": str|None, "title": str|None, "label": str}, ...],
            "subtitle_tracks": [{"index": int, "codec": str, "lang": str|None, "title": str|None, "label": str}, ...],
        }
    """
    now = time.time()
    with _probe_cache_lock:
        cached = _probe_cache.get(input_path)
        if cached and (now - cached[0]) < _PROBE_CACHE_TTL:
            return cached[1]

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

    result = {
        "duration": duration,
        "vcodec": vcodec,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
    }

    with _probe_cache_lock:
        _probe_cache[input_path] = (time.time(), result)

    return result


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

# Quality presets: name → (crf, max_height or None, preset)
QUALITY_PRESETS = {
    "auto":   (23, None, "ultrafast"),
    "low":    (28, 480,  "ultrafast"),
    "medium": (23, 720,  "ultrafast"),
    "high":   (20, 1080, "veryfast"),
}


def _video_codec_args(vcodec, seeking=False, quality="auto"):
    if vcodec in ("h264",) and not seeking and quality == "auto":
        return ["-c:v", "copy"]
    preset_info = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["auto"])
    crf, max_h, preset = preset_info
    # ultrafast + zerolatency: minimises first-byte latency for piped
    # streaming (1.7× faster than "fast" in benchmarks).  Quality tradeoff
    # is acceptable for real-time playback.
    args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-tune", "zerolatency"]
    if max_h:
        # Scale down to max height, keep aspect ratio, ensure even dimensions
        args += ["-vf", f"scale=-2:'min({max_h},ih)'"]
    return args


def _audio_codec_args(acodec, seeking=False):
    if acodec in ("aac",) and not seeking:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]


def build_stream_args(input_path, info=None, audio_track_idx=0, seek_time=0,
                      sub_output_dir=None, subtitle_tracks=None, quality="auto"):
    """Build FFmpeg args for piped MP4 streaming output.

    *info* should come from ``probe_full_info``.  *audio_track_idx* selects
    which audio track (by position in the audio_tracks list) to include.
    *seek_time* (seconds) adds ``-ss`` for seeking.
    *quality* selects a preset from ``QUALITY_PRESETS``.

    If *sub_output_dir* and *subtitle_tracks* are provided and *seek_time*
    is 0, subtitle VTT files are written as extra outputs in the same FFmpeg
    pass ("piggyback extraction"), avoiding a separate full download of the
    remote file.
    """
    if info is None:
        info = probe_full_info(input_path)

    audio_tracks = info.get("audio_tracks", [])
    audio = audio_tracks[audio_track_idx] if audio_track_idx < len(audio_tracks) else (audio_tracks[0] if audio_tracks else None)

    args = ["ffmpeg"]
    # Fast input-level seek to nearest keyframe, then precise output-level
    # trim.  Always re-encode when seeking so the output starts at the
    # exact requested timestamp (copy mode snaps to keyframes, causing
    # A/V desync).
    seeking = seek_time > 0
    if seeking:
        args += ["-ss", str(seek_time)]
    args += ["-i", input_path]

    # Map video + chosen audio
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]

    args += _video_codec_args(info.get("vcodec"), seeking=seeking, quality=quality)
    args += _audio_codec_args(audio["codec"] if audio else None, seeking=seeking)
    args += ["-sn"]  # strip subs from video stream

    args += [
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "-threads", "0",
        "pipe:1",
    ]

    # Piggyback subtitle extraction: write VTT files as additional outputs
    # in the same FFmpeg pass.  Only on initial play (no seek) so the full
    # input is read, giving complete subtitles.
    if sub_output_dir and subtitle_tracks and seek_time == 0:
        text_tracks = [
            (i, t) for i, t in enumerate(subtitle_tracks)
            if t.get("codec") in TEXT_SUB_CODECS
        ]
        for pos, track in text_tracks:
            vtt_path = os.path.join(sub_output_dir, f"sub_{pos}.vtt")
            args += ["-map", f"0:{track['index']}", "-c:s", "webvtt", "-y", vtt_path]

    return args


# ---------------------------------------------------------------------------
# Subtitle extraction
# ---------------------------------------------------------------------------

def extract_subtitles(input_path, output_dir, subtitle_tracks, timeout=120):
    """Extract all text-based subtitle tracks to WebVTT files.

    Files are written as ``sub_0.vtt``, ``sub_1.vtt``, … inside *output_dir*.
    A ``.extracting`` marker file is created while extraction is running so
    that other code (e.g. the subtitle-serving endpoint) can tell that work
    is in progress and wait accordingly.

    Returns a list of (track_position, vtt_path) tuples that succeeded.
    """
    text_tracks = [
        (i, t) for i, t in enumerate(subtitle_tracks)
        if t.get("codec") in TEXT_SUB_CODECS
    ]
    if not text_tracks:
        return []

    marker = os.path.join(output_dir, ".extracting")
    # If another extraction is already running, skip.
    if os.path.exists(marker):
        return []

    try:
        with open(marker, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    args = ["ffmpeg", "-hide_banner", "-v", "error", "-i", input_path]
    paths = []
    for pos, track in text_tracks:
        vtt_path = os.path.join(output_dir, f"sub_{pos}.vtt")
        args += ["-map", f"0:{track['index']}", "-c:s", "webvtt", "-y", vtt_path]
        paths.append((pos, vtt_path))

    try:
        run_ffmpeg(args, timeout=timeout)
    finally:
        try:
            os.unlink(marker)
        except OSError:
            pass

    return [(pos, p) for pos, p in paths if os.path.exists(p)]


def extract_single_subtitle(input_path, output_path, stream_index, timeout=600):
    """Extract a single subtitle track to WebVTT.

    Used as an on-demand fallback when piggyback extraction didn't
    complete (e.g. user seeked or closed the stream early).
    """
    args = [
        "ffmpeg", "-hide_banner", "-v", "error",
        "-i", input_path,
        "-map", f"0:{stream_index}",
        "-c:s", "webvtt",
        "-y", output_path,
    ]
    rc, _, _ = run_ffmpeg(args, timeout=timeout)
    return rc == 0 and os.path.exists(output_path)


# ---------------------------------------------------------------------------
# Persistent subtitle worker (VLC-style streaming demux)
# ---------------------------------------------------------------------------

# SRT block regex: index, timestamp line, then text
_SRT_TS_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{2,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{2,3})"
)


def _parse_srt_time(s):
    """Parse SRT timestamp ``HH:MM:SS,mmm`` to float seconds."""
    s = s.replace(",", ".").strip()
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


class SubtitleWorker:
    """Persistent FFmpeg process that streams SRT to stdout and parses
    cues incrementally into an in-memory buffer.

    Cues are sorted by start time and queryable via :meth:`get_cues_at`
    with O(log n) bisect lookup.

    Usage::

        w = SubtitleWorker(input_url, stream_index=3)
        w.start()
        cues = w.get_cues_at(t=1234.5, window=15)
        w.stop()
    """

    MAX_CUES = 10_000  # memory cap

    def __init__(self, input_path, stream_index, probesize="2M", analyzeduration="2M"):
        self.input_path = input_path
        self.stream_index = stream_index
        self.probesize = probesize
        self.analyzeduration = analyzeduration

        self._lock = threading.Lock()
        self._cues: list[dict] = []        # sorted by start time
        self._starts: list[float] = []     # parallel list of start times for bisect
        self._seen_keys: set[tuple] = set()  # (start, end) dedup keys
        self._max_time: float = 0.0        # furthest cue end time parsed so far
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._done = False  # True when FFmpeg exited cleanly

    # ----- public API -----

    @property
    def running(self):
        return self._started and not self._stop_event.is_set()

    @property
    def done(self):
        return self._done

    @property
    def cue_count(self):
        with self._lock:
            return len(self._cues)

    @property
    def max_parsed_time(self):
        """Furthest subtitle end-time parsed so far."""
        with self._lock:
            return self._max_time

    def has_coverage(self, t, margin=10):
        """Return True if the worker has parsed past time *t*.

        A finished worker always returns True (it parsed the whole file).
        """
        if self._done:
            return True
        with self._lock:
            return self._max_time >= t - margin

    def start(self, seek=0):
        """Start (or restart) the worker, optionally seeking to *seek* seconds."""
        self.stop()
        self._stop_event.clear()
        self._done = False
        self._thread = threading.Thread(
            target=self._run, args=(seek,), daemon=True
        )
        self._started = True
        self._thread.start()

    def restart_from(self, seek):
        """Restart from a new position, keeping already-parsed cues."""
        self._stop_event.set()
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                pass
            self._process = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        # Keep cues — don't clear them.  Start a fresh FFmpeg from seek.
        self._stop_event.clear()
        self._done = False
        self._thread = threading.Thread(
            target=self._run, args=(seek,), daemon=True
        )
        self._started = True
        self._thread.start()

    def stop(self):
        """Stop the worker and kill the FFmpeg process."""
        self._stop_event.set()
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=3)
            except Exception:
                pass
            self._process = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._started = False

    def get_cues_at(self, t, window=30):
        """Return cues whose time range overlaps [t-window, t+window].

        Uses bisect for O(log n) lookup. Returns list of dicts with
        ``start``, ``end``, ``text`` keys.
        """
        with self._lock:
            if not self._starts:
                return []
            lo = bisect_left(self._starts, t - window)
            hi = bisect_right(self._starts, t + window)
            return list(self._cues[lo:hi])

    def get_all_cues(self):
        """Return a copy of all parsed cues."""
        with self._lock:
            return list(self._cues)

    # ----- internal -----

    def _run(self, seek):
        args = [
            "ffmpeg", "-hide_banner", "-v", "error",
            "-probesize", self.probesize,
            "-analyzeduration", self.analyzeduration,
        ]
        if seek > 0:
            args += ["-ss", str(max(0, seek - 30))]  # start 30 s early
        args += [
            "-i", self.input_path,
            "-map", f"0:{self.stream_index}",
            "-f", "srt",
            "-",  # stdout
        ]

        try:
            self._process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=64 * 1024,
            )
        except Exception:
            self._done = True
            return

        buf = ""
        try:
            while not self._stop_event.is_set():
                data = self._process.stdout.read(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                # Parse complete SRT blocks (separated by blank lines)
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    self._parse_block(block.strip())
            # Parse anything remaining in buffer
            if buf.strip():
                self._parse_block(buf.strip())
        except Exception:
            pass
        finally:
            if self._process:
                try:
                    self._process.wait(timeout=3)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
            self._done = True

    def _parse_block(self, block):
        m = _SRT_TS_RE.search(block)
        if not m:
            return
        start = _parse_srt_time(m.group(1))
        end = _parse_srt_time(m.group(2))
        # Text is everything after the timestamp line
        lines = block.split("\n")
        ts_idx = next(
            (i for i, l in enumerate(lines) if "-->" in l), -1
        )
        text = "\n".join(lines[ts_idx + 1:]).strip() if ts_idx >= 0 else ""
        if not text:
            return

        key = (round(start, 2), round(end, 2))
        with self._lock:
            if key in self._seen_keys:
                return
            self._seen_keys.add(key)

            # Track how far we've parsed
            if end > self._max_time:
                self._max_time = end

            # Insert in sorted order
            idx = bisect_left(self._starts, start)
            self._starts.insert(idx, start)
            self._cues.insert(idx, {"start": start, "end": end, "text": text})

            # Enforce memory cap
            if len(self._cues) > self.MAX_CUES:
                self._seen_keys.discard(
                    (round(self._cues[0]["start"], 2), round(self._cues[0]["end"], 2))
                )
                self._cues.pop(0)
                self._starts.pop(0)


# Registry of active subtitle workers, keyed by (input_path, stream_index).
_subtitle_workers: dict[tuple[str, int], SubtitleWorker] = {}
_subtitle_workers_lock = threading.Lock()


def get_subtitle_worker(input_path, stream_index, auto_start=True):
    """Get or create a SubtitleWorker for the given input and track.

    If *auto_start* is True and no worker exists, one is created and
    started immediately.
    """
    key = (input_path, stream_index)
    with _subtitle_workers_lock:
        w = _subtitle_workers.get(key)
        if w and (w.running or w.done):
            return w
        if auto_start:
            w = SubtitleWorker(input_path, stream_index)
            _subtitle_workers[key] = w
            w.start()
            return w
    return None


def stop_subtitle_workers(input_path=None):
    """Stop subtitle workers. If *input_path* given, stop only those for
    that input; otherwise stop all."""
    with _subtitle_workers_lock:
        keys = list(_subtitle_workers.keys())
        for k in keys:
            if input_path is None or k[0] == input_path:
                _subtitle_workers[k].stop()
                del _subtitle_workers[k]


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
