"""
Video pipeline tests – ffmpeg_utils functions and video routes.

Tests are organised into three groups:

1. **Unit tests** – exercise ``ffmpeg_utils`` helpers with mocked FFmpeg
   (always runnable, no FFmpeg binary needed).

2. **Integration tests** – exercise ``ffmpeg_utils`` with real FFmpeg against
   small synthetic local video files (requires ``ffmpeg`` on PATH).

3. **Live HTTP tests** – stream/subtitle/piggyback against a real HTTP video.
   Skipped by default; pass ``--video-url URL`` to pytest to enable.

Run:
    pytest tests/test_video_pipeline.py -v           # unit + local integration
    VIDEO_URL="http://host/video.mkv" pytest tests/test_video_pipeline.py -v  # + live
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from src.ffmpeg_utils import (
    FFMPEG_AVAILABLE,
    QUALITY_PRESETS,
    TEXT_SUB_CODECS,
    build_stream_args,
    extract_single_subtitle,
    extract_subtitles,
    probe_full_info,
    run_ffmpeg,
    _video_codec_args,
    _audio_codec_args,
)

# ---------------------------------------------------------------------------
# Pytest plumbing
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def video_url():
    url = os.environ.get("VIDEO_URL")
    if not url:
        pytest.skip("VIDEO_URL env var not set")
    return url


@pytest.fixture()
def has_ffmpeg():
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg not found on PATH")


# Small synthetic video with burned-in subtitles for local tests.
@pytest.fixture(scope="session")
def local_video_with_subs(tmp_path_factory):
    """Create a tiny MKV with 1 video, 1 audio, and 2 SRT subtitle tracks."""
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg not found")

    base = tmp_path_factory.mktemp("video")
    srt1 = base / "en.srt"
    srt1.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello English\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld English\n"
    )
    srt2 = base / "es.srt"
    srt2.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHola Español\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nMundo Español\n"
    )

    out = base / "test.mkv"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        # Generate 2 s of colour bars + silent audio
        "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-i", str(srt1),
        "-i", str(srt2),
        "-map", "0:v", "-map", "1:a", "-map", "2", "-map", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "51",
        "-c:a", "aac", "-b:a", "32k",
        "-c:s", "srt",
        "-metadata:s:s:0", "language=eng",
        "-metadata:s:s:0", "title=English",
        "-metadata:s:s:1", "language=spa",
        "-metadata:s:s:1", "title=Spanish",
        "-t", "2",
        str(out),
    ]
    rc = subprocess.run(cmd, capture_output=True, timeout=30)
    if rc.returncode != 0:
        pytest.skip(f"Could not create test video: {rc.stderr.decode()[:200]}")
    return str(out)


# =========================================================================
# 1. Unit tests — no FFmpeg binary required
# =========================================================================


class TestCodecArgs:
    """Verify _video_codec_args / _audio_codec_args return expected flags."""

    def test_h264_copy_no_seek(self):
        assert _video_codec_args("h264", seeking=False) == ["-c:v", "copy"]

    def test_h264_reencode_on_seek(self):
        args = _video_codec_args("h264", seeking=True)
        assert args[1] == "libx264"
        assert "-preset" in args
        assert "ultrafast" in args

    def test_hevc_always_reencodes(self):
        args = _video_codec_args("hevc")
        assert "libx264" in args

    def test_zerolatency_present(self):
        args = _video_codec_args("hevc")
        assert "-tune" in args
        idx = args.index("-tune")
        assert args[idx + 1] == "zerolatency"

    def test_aac_copy_no_seek(self):
        assert _audio_codec_args("aac", seeking=False) == ["-c:a", "copy"]

    def test_non_aac_reencodes(self):
        args = _audio_codec_args("eac3")
        assert "aac" in args

    def test_quality_low_adds_scale(self):
        args = _video_codec_args("hevc", quality="low")
        assert "-vf" in args
        idx = args.index("-vf")
        assert "480" in args[idx + 1]

    def test_quality_medium_720p(self):
        args = _video_codec_args("hevc", quality="medium")
        assert "-vf" in args
        idx = args.index("-vf")
        assert "720" in args[idx + 1]

    def test_quality_high_1080p(self):
        args = _video_codec_args("hevc", quality="high")
        assert "-vf" in args
        idx = args.index("-vf")
        assert "1080" in args[idx + 1]
        # high uses veryfast preset
        assert "veryfast" in args

    def test_quality_auto_no_scale(self):
        args = _video_codec_args("hevc", quality="auto")
        assert "-vf" not in args

    def test_h264_copy_skipped_when_quality_not_auto(self):
        args = _video_codec_args("h264", seeking=False, quality="low")
        assert "libx264" in args  # re-encodes to apply scaling


class TestBuildStreamArgs:
    """Verify build_stream_args constructs correct FFmpeg commands."""

    @pytest.fixture()
    def mock_info(self):
        return {
            "duration": 120.0,
            "vcodec": "hevc",
            "audio_tracks": [
                {"index": 1, "codec": "aac", "lang": "eng", "title": "English", "label": "eng - English"},
                {"index": 2, "codec": "eac3", "lang": "kor", "title": "Korean", "label": "kor - Korean"},
            ],
            "subtitle_tracks": [
                {"index": 3, "codec": "subrip", "lang": "eng", "title": "English", "label": "eng - English"},
                {"index": 4, "codec": "subrip", "lang": "spa", "title": "Spanish", "label": "spa - Spanish"},
                {"index": 5, "codec": "hdmv_pgs_subtitle", "lang": "jpn", "title": "Japanese", "label": "jpn - Japanese"},
            ],
        }

    def test_basic_args(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info)
        assert args[0] == "ffmpeg"
        assert "pipe:1" in args
        assert "-f" in args
        assert "mp4" in args[args.index("-f") + 1]

    def test_seek_adds_ss(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info, seek_time=120)
        assert "-ss" in args
        idx = args.index("-ss")
        assert args[idx + 1] == "120"

    def test_audio_track_selection(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info, audio_track_idx=1)
        # Should map the Korean track (index 2)
        assert "0:2" in args

    def test_threads_zero(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info)
        idx = args.index("-threads")
        assert args[idx + 1] == "0"

    def test_subs_stripped_from_video(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info)
        assert "-sn" in args

    def test_no_piggyback_without_params(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info)
        # Should not contain subtitle mapping
        assert "-c:s" not in args

    def test_piggyback_adds_subtitle_outputs(self, mock_info, tmp_path):
        sub_dir = str(tmp_path / "subs")
        os.makedirs(sub_dir)
        args = build_stream_args(
            "/fake/video.mkv",
            info=mock_info,
            sub_output_dir=sub_dir,
            subtitle_tracks=mock_info["subtitle_tracks"],
        )
        # Should have webvtt outputs for text-based subs only (not hdmv_pgs)
        assert args.count("-c:s") == 2  # two text subs
        assert args.count("webvtt") == 2
        # Bitmap sub (hdmv_pgs_subtitle) should be excluded
        assert "0:5" not in args

    def test_piggyback_skipped_on_seek(self, mock_info, tmp_path):
        sub_dir = str(tmp_path / "subs")
        os.makedirs(sub_dir)
        args = build_stream_args(
            "/fake/video.mkv",
            info=mock_info,
            seek_time=60,
            sub_output_dir=sub_dir,
            subtitle_tracks=mock_info["subtitle_tracks"],
        )
        # seek_time > 0 →  no piggyback
        assert "-c:s" not in args

    def test_piggyback_vtt_paths(self, mock_info, tmp_path):
        sub_dir = str(tmp_path / "subs")
        os.makedirs(sub_dir)
        args = build_stream_args(
            "/fake/video.mkv",
            info=mock_info,
            sub_output_dir=sub_dir,
            subtitle_tracks=mock_info["subtitle_tracks"],
        )
        # VTT files named by position in subtitle_tracks list
        assert os.path.join(sub_dir, "sub_0.vtt") in args
        assert os.path.join(sub_dir, "sub_1.vtt") in args
        # Position 2 is bitmap → should NOT appear
        assert os.path.join(sub_dir, "sub_2.vtt") not in args

    def test_pipe_before_sub_outputs(self, mock_info, tmp_path):
        """FFmpeg multi-output: pipe:1 must come before subtitle file outputs."""
        sub_dir = str(tmp_path / "subs")
        os.makedirs(sub_dir)
        args = build_stream_args(
            "/fake/video.mkv",
            info=mock_info,
            sub_output_dir=sub_dir,
            subtitle_tracks=mock_info["subtitle_tracks"],
        )
        pipe_idx = args.index("pipe:1")
        # All subtitle codec flags should appear after pipe:1
        for i, a in enumerate(args):
            if a == "-c:s":
                assert i > pipe_idx

    def test_quality_param_forwarded(self, mock_info):
        args = build_stream_args("/fake/video.mkv", info=mock_info, quality="low")
        assert "-vf" in args
        idx = args.index("-vf")
        assert "480" in args[idx + 1]


# =========================================================================
# 2. Integration tests — require ffmpeg on PATH + local synthetic video
# =========================================================================


class TestProbeIntegration:
    """probe_full_info against real local files."""

    def test_probe_local_video(self, has_ffmpeg, local_video_with_subs):
        info = probe_full_info(local_video_with_subs)
        assert info["vcodec"] == "h264"
        assert info["duration"] is not None
        assert info["duration"] > 0
        assert len(info["audio_tracks"]) >= 1
        assert len(info["subtitle_tracks"]) == 2

    def test_subtitle_codecs_detected(self, has_ffmpeg, local_video_with_subs):
        info = probe_full_info(local_video_with_subs)
        for st in info["subtitle_tracks"]:
            assert st["codec"] in TEXT_SUB_CODECS

    def test_subtitle_languages(self, has_ffmpeg, local_video_with_subs):
        info = probe_full_info(local_video_with_subs)
        langs = {t["lang"] for t in info["subtitle_tracks"]}
        assert "eng" in langs
        assert "spa" in langs


class TestExtractSubtitlesIntegration:
    """extract_subtitles / extract_single_subtitle against local files."""

    def test_extract_all_subtitles(self, has_ffmpeg, local_video_with_subs, tmp_path):
        info = probe_full_info(local_video_with_subs)
        out_dir = str(tmp_path / "subs")
        os.makedirs(out_dir)
        result = extract_subtitles(
            local_video_with_subs, out_dir, info["subtitle_tracks"], timeout=30
        )
        assert len(result) == 2
        for pos, path in result:
            assert os.path.exists(path)
            content = open(path).read()
            assert "WEBVTT" in content

    def test_extract_single_subtitle(self, has_ffmpeg, local_video_with_subs, tmp_path):
        info = probe_full_info(local_video_with_subs)
        track = info["subtitle_tracks"][0]
        vtt_path = str(tmp_path / "single.vtt")
        ok = extract_single_subtitle(
            local_video_with_subs, vtt_path, track["index"], timeout=30
        )
        assert ok is True
        content = open(vtt_path).read()
        assert "WEBVTT" in content
        assert "English" in content  # from subtitle text "Hello English"

    def test_extract_single_second_track(self, has_ffmpeg, local_video_with_subs, tmp_path):
        info = probe_full_info(local_video_with_subs)
        track = info["subtitle_tracks"][1]
        vtt_path = str(tmp_path / "spanish.vtt")
        ok = extract_single_subtitle(
            local_video_with_subs, vtt_path, track["index"], timeout=30
        )
        assert ok is True
        content = open(vtt_path).read()
        assert "WEBVTT" in content
        assert "Español" in content or "Espanol" in content or "spa" in content.lower()

    def test_marker_file_lifecycle(self, has_ffmpeg, local_video_with_subs, tmp_path):
        """Marker should exist during extraction and be removed after."""
        info = probe_full_info(local_video_with_subs)
        out_dir = str(tmp_path / "subs")
        os.makedirs(out_dir)
        marker = os.path.join(out_dir, ".extracting")
        extract_subtitles(
            local_video_with_subs, out_dir, info["subtitle_tracks"], timeout=30
        )
        assert not os.path.exists(marker), "Marker should be removed after extraction"

    def test_concurrent_extraction_skipped(self, has_ffmpeg, local_video_with_subs, tmp_path):
        """If marker already exists, extraction should return [] immediately."""
        info = probe_full_info(local_video_with_subs)
        out_dir = str(tmp_path / "subs")
        os.makedirs(out_dir)
        marker = os.path.join(out_dir, ".extracting")
        with open(marker, "w") as f:
            f.write("fake")
        result = extract_subtitles(
            local_video_with_subs, out_dir, info["subtitle_tracks"], timeout=30
        )
        assert result == []
        # Clean up
        os.unlink(marker)


class TestBuildStreamArgsIntegration:
    """build_stream_args with probing against real local files."""

    def test_stream_args_local_h264(self, has_ffmpeg, local_video_with_subs):
        info = probe_full_info(local_video_with_subs)
        args = build_stream_args(local_video_with_subs, info=info)
        # H264 input + no seek → copy codec
        assert "-c:v" in args
        idx = args.index("-c:v")
        assert args[idx + 1] == "copy"

    def test_stream_args_with_seek_reencodes(self, has_ffmpeg, local_video_with_subs):
        info = probe_full_info(local_video_with_subs)
        args = build_stream_args(local_video_with_subs, info=info, seek_time=1)
        idx = args.index("-c:v")
        assert args[idx + 1] == "libx264"

    def test_piggyback_produces_valid_vtt(self, has_ffmpeg, local_video_with_subs, tmp_path):
        """Full integration: run build_stream_args with piggyback, pipe output, verify VTTs."""
        info = probe_full_info(local_video_with_subs)
        sub_dir = str(tmp_path / "pb_subs")
        os.makedirs(sub_dir)
        args = build_stream_args(
            local_video_with_subs,
            info=info,
            sub_output_dir=sub_dir,
            subtitle_tracks=info["subtitle_tracks"],
        )
        # Actually run FFmpeg
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=256 * 1024
        )
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, f"FFmpeg failed: {stderr.decode()[:300]}"
        assert len(stdout) > 0, "Should have piped video output"
        # Check VTT files
        for i, track in enumerate(info["subtitle_tracks"]):
            if track["codec"] in TEXT_SUB_CODECS:
                vtt = os.path.join(sub_dir, f"sub_{i}.vtt")
                assert os.path.exists(vtt), f"sub_{i}.vtt missing"
                content = open(vtt).read()
                assert "WEBVTT" in content


# =========================================================================
# 3. Live HTTP tests — require --video-url
# =========================================================================


class TestLiveProbe:
    """Probe a real HTTP video URL."""

    def test_probe_http_video(self, has_ffmpeg, video_url):
        info = probe_full_info(video_url)
        assert info["vcodec"] is not None, "Should detect video codec"
        assert info["duration"] is not None and info["duration"] > 0
        assert len(info["audio_tracks"]) > 0

    def test_probe_detects_subtitles(self, has_ffmpeg, video_url):
        info = probe_full_info(video_url)
        assert len(info["subtitle_tracks"]) > 0, "Expected subtitle tracks"
        text_subs = [t for t in info["subtitle_tracks"] if t["codec"] in TEXT_SUB_CODECS]
        assert len(text_subs) > 0, "Expected text-based subtitles"


class TestLiveStream:
    """Stream video from HTTP via FFmpeg transcode."""

    def test_stream_first_byte_latency(self, has_ffmpeg, video_url):
        """First byte should arrive within a reasonable time."""
        info = probe_full_info(video_url)
        args = build_stream_args(video_url, info=info)
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=256 * 1024
        )
        try:
            chunk = proc.stdout.read(1024)
            first_byte = time.perf_counter() - t0
            assert len(chunk) > 0, "No data received"
            assert first_byte < 10, f"First byte took {first_byte:.1f}s (too slow)"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_stream_seek(self, has_ffmpeg, video_url):
        """Seeking should produce valid output starting from the seek point."""
        info = probe_full_info(video_url)
        args = build_stream_args(video_url, info=info, seek_time=60)
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=256 * 1024
        )
        try:
            data = proc.stdout.read(256 * 1024)
            assert len(data) > 0, "No data received after seek"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestLivePiggyback:
    """Piggyback subtitle extraction with live HTTP video."""

    def test_piggyback_produces_vtts(self, has_ffmpeg, video_url, tmp_path):
        """Piggybacked subtitles should be written alongside the video stream."""
        info = probe_full_info(video_url)
        text_subs = [t for t in info["subtitle_tracks"] if t["codec"] in TEXT_SUB_CODECS]
        if not text_subs:
            pytest.skip("No text subtitles in video")

        sub_dir = str(tmp_path / "piggyback")
        os.makedirs(sub_dir)
        args = build_stream_args(
            video_url,
            info=info,
            sub_output_dir=sub_dir,
            subtitle_tracks=info["subtitle_tracks"],
        )
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=256 * 1024
        )
        # Read ~5 MB of video (enough for FFmpeg to start writing subs)
        total = 0
        try:
            while total < 5 * 1024 * 1024:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
        finally:
            proc.terminate()
            proc.wait(timeout=10)

        # Even with an early termination, some VTTs may have partial content
        vtt_files = [f for f in os.listdir(sub_dir) if f.endswith(".vtt")]
        # At minimum we should see the VTT files created (even if partially written)
        assert len(vtt_files) > 0, "Expected at least one VTT file from piggyback"

    def test_single_track_extraction_http(self, has_ffmpeg, video_url, tmp_path):
        """On-demand single-track extraction from HTTP URL."""
        info = probe_full_info(video_url)
        text_subs = [t for t in info["subtitle_tracks"] if t["codec"] in TEXT_SUB_CODECS]
        if not text_subs:
            pytest.skip("No text subtitles in video")

        track = text_subs[0]
        vtt_path = str(tmp_path / "single_http.vtt")
        ok = extract_single_subtitle(video_url, vtt_path, track["index"], timeout=300)
        assert ok is True
        content = open(vtt_path).read()
        assert "WEBVTT" in content
        assert len(content) > 50, "VTT file suspiciously small"
