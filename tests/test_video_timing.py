#!/usr/bin/env python3
"""
Video pipeline timing & correctness tests.

Runs FFprobe/FFmpeg directly against an HTTP video URL to identify
bottlenecks in each stage of the pipeline: probing, subtitle extraction,
stream startup, seeking, and first-byte latency.

Usage:
    python tests/test_video_timing.py [URL]

Default URL is the test MKV from the local server.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_URL = (
    "http://172.16.50.14/DHAKA-FLIX-14/KOREAN%20TV%20%26%20WEB%20Series/"
    "Sweet%20Home%20%28TV%20Series%202020%E2%80%932024%29%201080p%20%5BMulti%20Audio%5D/"
    "Season%202/"
    "Sweet%20Home%20S02E02%20%201080p%20NF%20WEBRip%20x265%20HEVC%20MSubs%20"
    "%5BMulti%20Audio%5D%5BEnglish%205.1%2BHindi%205.1%2BKorean%205.1%5D%20-PSA.mkv"
)

TMPDIR = tempfile.mkdtemp(prefix="vp_timing_")


def _ts():
    return time.perf_counter()


def run(args, timeout=30, capture=True):
    """Run a subprocess and return (returncode, stdout, stderr, elapsed)."""
    t0 = _ts()
    try:
        p = subprocess.run(
            args,
            capture_output=capture,
            timeout=timeout,
        )
        elapsed = _ts() - t0
        return p.returncode, p.stdout, p.stderr, elapsed
    except subprocess.TimeoutExpired:
        return -1, b"", b"TIMEOUT", _ts() - t0


def run_popen_firstbyte(args, timeout=30):
    """Start a subprocess (piped stdout) and measure time to first byte and first 1MB."""
    t0 = _ts()
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=256 * 1024)
    first_byte_time = None
    first_mb_time = None
    total_read = 0
    try:
        while True:
            remaining = timeout - (_ts() - t0)
            if remaining <= 0:
                break
            chunk = p.stdout.read(64 * 1024)
            if not chunk:
                break
            total_read += len(chunk)
            if first_byte_time is None:
                first_byte_time = _ts() - t0
            if first_mb_time is None and total_read >= 1024 * 1024:
                first_mb_time = _ts() - t0
            # Stop after 2 MB — we just need timing
            if total_read >= 2 * 1024 * 1024:
                break
    finally:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
    total_time = _ts() - t0
    return {
        "first_byte_s": first_byte_time,
        "first_mb_s": first_mb_time,
        "total_read_bytes": total_read,
        "wall_s": total_time,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_probe_current(url):
    """Current probe args (as used in production code)."""
    args = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    rc, stdout, stderr, elapsed = run(args, timeout=30)
    info = json.loads(stdout.decode()) if rc == 0 else {}
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    print(f"  [CURRENT] probe_full_info:  rc={rc}  time={elapsed:.2f}s")
    print(f"    duration={fmt.get('duration')}  streams={len(streams)}")
    if rc != 0:
        print(f"    stderr: {stderr.decode(errors='replace')[:300]}")
    return elapsed, info


def test_probe_fast_header_only(url):
    """Faster: only read initial headers, skip full file scan."""
    args = [
        "ffprobe", "-v", "error",
        "-analyzeduration", "3000000",   # 3 s
        "-probesize", "5000000",         # 5 MB
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    rc, stdout, stderr, elapsed = run(args, timeout=30)
    info = json.loads(stdout.decode()) if rc == 0 else {}
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    print(f"  [FAST]    probe (3s/5MB):   rc={rc}  time={elapsed:.2f}s")
    print(f"    duration={fmt.get('duration')}  streams={len(streams)}")
    return elapsed, info


def test_probe_ultra_fast(url):
    """Ultra-fast: minimal scan — may miss some tracks."""
    args = [
        "ffprobe", "-v", "error",
        "-analyzeduration", "1000000",   # 1 s
        "-probesize", "2000000",         # 2 MB
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    rc, stdout, stderr, elapsed = run(args, timeout=30)
    info = json.loads(stdout.decode()) if rc == 0 else {}
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    print(f"  [ULTRA]   probe (1s/2MB):   rc={rc}  time={elapsed:.2f}s")
    print(f"    duration={fmt.get('duration')}  streams={len(streams)}")
    return elapsed, info


def compare_stream_info(label_a, info_a, label_b, info_b):
    """Compare streams found by two probe methods."""
    sa = {s.get("index"): s.get("codec_name") for s in info_a.get("streams", [])}
    sb = {s.get("index"): s.get("codec_name") for s in info_b.get("streams", [])}
    if sa == sb:
        print(f"  ✓ [{label_a}] and [{label_b}] found same streams")
    else:
        only_a = set(sa) - set(sb)
        only_b = set(sb) - set(sa)
        if only_a:
            print(f"  ✗ [{label_a}] has extra streams: {only_a}")
        if only_b:
            print(f"  ✗ [{label_b}] has extra streams: {only_b}")


def test_stream_startup_current(url, info):
    """Current streaming pipeline: x265→x264 transcode, frag MP4."""
    audio_tracks = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    audio = audio_tracks[0] if audio_tracks else None
    vcodec = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            vcodec = s.get("codec_name")
            break

    args = ["ffmpeg", "-hide_banner", "-v", "error"]
    args += ["-i", url]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]
    # Current: always re-encode for non-h264
    args += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]
    args += ["-sn"]
    args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    args += ["-f", "mp4", "-threads", "2", "pipe:1"]

    print(f"  [CURRENT] stream startup (libx264 fast crf23, 2 threads):")
    result = run_popen_firstbyte(args, timeout=30)
    print(f"    first byte: {result['first_byte_s']:.2f}s" if result['first_byte_s'] else "    first byte: NONE")
    print(f"    first 1MB:  {result['first_mb_s']:.2f}s" if result['first_mb_s'] else "    first 1MB: NONE")
    print(f"    total read: {result['total_read_bytes'] / 1024:.0f} KB in {result['wall_s']:.2f}s")
    return result


def test_stream_startup_ultrafast(url, info):
    """Optimized: ultrafast preset, lower CRF, more threads."""
    audio_tracks = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    audio = audio_tracks[0] if audio_tracks else None

    args = ["ffmpeg", "-hide_banner", "-v", "error"]
    args += ["-i", url]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-tune", "zerolatency"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]
    args += ["-sn"]
    args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    args += ["-f", "mp4", "-threads", "0", "pipe:1"]

    print(f"  [ULTRAFAST] stream startup (libx264 ultrafast crf28 zerolatency, auto threads):")
    result = run_popen_firstbyte(args, timeout=30)
    print(f"    first byte: {result['first_byte_s']:.2f}s" if result['first_byte_s'] else "    first byte: NONE")
    print(f"    first 1MB:  {result['first_mb_s']:.2f}s" if result['first_mb_s'] else "    first 1MB: NONE")
    print(f"    total read: {result['total_read_bytes'] / 1024:.0f} KB in {result['wall_s']:.2f}s")
    return result


def test_stream_startup_veryfast(url, info):
    """Middle ground: veryfast preset."""
    audio_tracks = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    audio = audio_tracks[0] if audio_tracks else None

    args = ["ffmpeg", "-hide_banner", "-v", "error"]
    args += ["-i", url]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]
    args += ["-sn"]
    args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    args += ["-f", "mp4", "-threads", "0", "pipe:1"]

    print(f"  [VERYFAST] stream startup (libx264 veryfast crf26, auto threads):")
    result = run_popen_firstbyte(args, timeout=30)
    print(f"    first byte: {result['first_byte_s']:.2f}s" if result['first_byte_s'] else "    first byte: NONE")
    print(f"    first 1MB:  {result['first_mb_s']:.2f}s" if result['first_mb_s'] else "    first 1MB: NONE")
    print(f"    total read: {result['total_read_bytes'] / 1024:.0f} KB in {result['wall_s']:.2f}s")
    return result


def test_seek_time(url, info, seek_to=300):
    """Measure seek startup time (seeking to 5 min mark)."""
    audio_tracks = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    audio = audio_tracks[0] if audio_tracks else None

    # Current approach: input-level -ss
    args = ["ffmpeg", "-hide_banner", "-v", "error"]
    args += ["-ss", str(seek_to)]
    args += ["-i", url]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", f"0:{audio['index']}"]
    else:
        args += ["-map", "0:a:0?"]
    args += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]
    args += ["-sn"]
    args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    args += ["-f", "mp4", "-threads", "2", "pipe:1"]

    print(f"  [CURRENT] seek to {seek_to}s (input-level -ss, fast, 2 threads):")
    result = run_popen_firstbyte(args, timeout=30)
    print(f"    first byte: {result['first_byte_s']:.2f}s" if result['first_byte_s'] else "    first byte: NONE")
    print(f"    first 1MB:  {result['first_mb_s']:.2f}s" if result['first_mb_s'] else "    first 1MB: NONE")

    # Optimized seek
    args2 = ["ffmpeg", "-hide_banner", "-v", "error"]
    args2 += ["-ss", str(seek_to)]
    args2 += ["-i", url]
    args2 += ["-map", "0:v:0"]
    if audio:
        args2 += ["-map", f"0:{audio['index']}"]
    else:
        args2 += ["-map", "0:a:0?"]
    args2 += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-tune", "zerolatency"]
    args2 += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"]
    args2 += ["-sn"]
    args2 += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    args2 += ["-f", "mp4", "-threads", "0", "pipe:1"]

    print(f"  [ULTRAFAST] seek to {seek_to}s (ultrafast, zerolatency, auto threads):")
    result2 = run_popen_firstbyte(args2, timeout=30)
    print(f"    first byte: {result2['first_byte_s']:.2f}s" if result2['first_byte_s'] else "    first byte: NONE")
    print(f"    first 1MB:  {result2['first_mb_s']:.2f}s" if result2['first_mb_s'] else "    first 1MB: NONE")

    return result, result2


def test_subtitle_extraction(url, info):
    """Time subtitle extraction from HTTP source."""
    sub_tracks = [s for s in info.get("streams", []) if s.get("codec_type") == "subtitle"]
    if not sub_tracks:
        print("  No subtitle tracks found — skipping")
        return None

    text_codecs = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt"}
    text_subs = [(i, s) for i, s in enumerate(sub_tracks) if s.get("codec_name") in text_codecs]
    print(f"  Found {len(sub_tracks)} subtitle tracks, {len(text_subs)} text-based")

    if not text_subs:
        print("  No text subtitles to extract")
        return None

    out_dir = os.path.join(TMPDIR, "subs")
    os.makedirs(out_dir, exist_ok=True)

    # Current: extract all at once (full download required for MKV over HTTP)
    args = ["ffmpeg", "-hide_banner", "-v", "error", "-i", url]
    paths = []
    for pos, track in text_subs:
        vtt = os.path.join(out_dir, f"sub_{pos}.vtt")
        args += ["-map", f"0:{track['index']}", "-c:s", "webvtt", "-y", vtt]
        paths.append(vtt)

    print(f"  [CURRENT] extracting {len(text_subs)} sub tracks (all at once):")
    rc, _, stderr, elapsed = run(args, timeout=300)
    sizes = [os.path.getsize(p) if os.path.exists(p) else 0 for p in paths]
    print(f"    rc={rc}  time={elapsed:.2f}s  files={[f'{s/1024:.1f}KB' for s in sizes]}")
    if rc != 0:
        print(f"    stderr: {stderr.decode(errors='replace')[:300]}")

    # Only-first-sub: extract just the first track (faster?)
    out2 = os.path.join(TMPDIR, "subs2")
    os.makedirs(out2, exist_ok=True)
    pos0, track0 = text_subs[0]
    vtt0 = os.path.join(out2, f"sub_{pos0}.vtt")
    args2 = ["ffmpeg", "-hide_banner", "-v", "error", "-i", url,
             "-map", f"0:{track0['index']}", "-c:s", "webvtt", "-y", vtt0]
    print(f"  [SINGLE]  extracting 1 sub track:")
    rc2, _, stderr2, elapsed2 = run(args2, timeout=300)
    sz = os.path.getsize(vtt0) if os.path.exists(vtt0) else 0
    print(f"    rc={rc2}  time={elapsed2:.2f}s  size={sz/1024:.1f}KB")

    return elapsed, elapsed2


def test_probe_timeout_behavior(url):
    """Check if ffprobe with 10s timeout (production default) works for HTTP."""
    args = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    rc, stdout, stderr, elapsed = run(args, timeout=10)
    print(f"  [10s TIMEOUT] probe:  rc={rc}  time={elapsed:.2f}s")
    if rc != 0:
        print(f"    → FAILED (current production timeout is 10s!)")
        print(f"    stderr: {stderr.decode(errors='replace')[:200]}")
    else:
        info = json.loads(stdout.decode())
        print(f"    → OK, streams={len(info.get('streams', []))}")
    return rc, elapsed


def test_probe_with_analyzeduration(url):
    """Probe with analyzeduration to speed up HTTP probing."""
    args = [
        "ffprobe", "-v", "error",
        "-analyzeduration", "5000000",   # 5 s analysis
        "-probesize", "10000000",        # 10 MB
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,codec_long_name:stream_tags=language,title",
        "-of", "json",
        url,
    ]
    rc, stdout, stderr, elapsed = run(args, timeout=30)
    print(f"  [5s/10MB]  probe:  rc={rc}  time={elapsed:.2f}s")
    if rc == 0:
        info = json.loads(stdout.decode())
        print(f"    streams={len(info.get('streams', []))} duration={info.get('format', {}).get('duration')}")
    return rc, elapsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(f"═══════════════════════════════════════════════════════════")
    print(f"Video Pipeline Timing Tests")
    print(f"URL: {url[:100]}{'...' if len(url) > 100 else ''}")
    print(f"Temp dir: {TMPDIR}")
    print(f"═══════════════════════════════════════════════════════════")

    # --- Probe tests ---
    print(f"\n{'─'*60}")
    print("1. PROBING PERFORMANCE")
    print(f"{'─'*60}")

    rc_10s, elapsed_10s = test_probe_timeout_behavior(url)
    print()
    t_cur, info_cur = test_probe_current(url)
    print()
    t_fast, info_fast = test_probe_fast_header_only(url)
    print()
    t_ultra, info_ultra = test_probe_ultra_fast(url)
    print()
    test_probe_with_analyzeduration(url)
    print()

    # Compare stream detection
    compare_stream_info("CURRENT", info_cur, "FAST", info_fast)
    compare_stream_info("CURRENT", info_cur, "ULTRA", info_ultra)

    # Use the best complete info for further tests
    info = info_cur if info_cur.get("streams") else info_fast

    # --- Stream startup tests ---
    print(f"\n{'─'*60}")
    print("2. STREAM STARTUP (first playback)")
    print(f"{'─'*60}")

    r_cur = test_stream_startup_current(url, info)
    print()
    r_uf = test_stream_startup_ultrafast(url, info)
    print()
    r_vf = test_stream_startup_veryfast(url, info)

    # --- Seek tests ---
    print(f"\n{'─'*60}")
    print("3. SEEK PERFORMANCE (to 300s)")
    print(f"{'─'*60}")

    test_seek_time(url, info, seek_to=300)

    # --- Subtitle extraction ---
    print(f"\n{'─'*60}")
    print("4. SUBTITLE EXTRACTION")
    print(f"{'─'*60}")

    test_subtitle_extraction(url, info)

    # --- Summary ---
    print(f"\n{'═'*60}")
    print("SUMMARY & RECOMMENDATIONS")
    print(f"{'═'*60}")

    if rc_10s != 0:
        print(f"  ⚠ ffprobe FAILED with 10s timeout (current production default)")
        print(f"    → Need to increase timeout or add -analyzeduration/-probesize")

    print(f"\n  Probe times:")
    print(f"    Current (no limits):  {t_cur:.2f}s")
    print(f"    Fast (3s/5MB):        {t_fast:.2f}s")
    print(f"    Ultra (1s/2MB):       {t_ultra:.2f}s")

    if r_cur['first_byte_s'] and r_uf['first_byte_s']:
        speedup = r_cur['first_byte_s'] / r_uf['first_byte_s']
        print(f"\n  Stream first-byte speedup (ultrafast vs fast): {speedup:.1f}x")

    print(f"\n  Temp files in: {TMPDIR}")
    print(f"  Done.\n")


if __name__ == "__main__":
    main()
