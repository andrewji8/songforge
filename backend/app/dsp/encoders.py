"""ffmpeg-backed encode/decode helpers.

MP3 export and arbitrary-input decode (voice .webm, loop .mp3/.m4a).
ffmpeg is a hard runtime dep in the container (see Dockerfile). When the
binary is missing the helpers raise a clear error so the caller can fall
back gracefully (e.g. WAV-only) instead of crashing silently.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.dsp.wav_io import Audio


class FfmpegError(RuntimeError):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FfmpegError("ffmpeg not found on PATH; install it for MP3/export")
    return exe


def wav_to_mp3(wav_path: str | Path, out_path: str | Path, kbps: int = 320) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exe = _ffmpeg()
    cmd = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(Path(wav_path)),
        "-codec:a", "libmp3lame", "-b:a", f"{kbps}k",
        "-ar", "44100", "-ac", "2",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise FfmpegError(f"ffmpeg mp3 encode failed: {r.stderr.strip()[:400]}")
    return out_path


def decode_to_wav(src_path: str | Path, out_path: str | Path, sr: int = 44100) -> Path:
    """Decode any audio container ffmpeg understands to PCM16 mono/stereo WAV."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    exe = _ffmpeg()
    cmd = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(Path(src_path)),
        "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", "2",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise FfmpegError(f"ffmpeg decode failed: {r.stderr.strip()[:400]}")
    return out_path


def audio_duration_seconds(path: str | Path) -> float:
    """Duration via ffprobe when available, else WAV header / ffmpeg parse."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            try:
                return float(r.stdout.strip())
            except ValueError:
                pass
    # fallback: read wav header
    try:
        return Audio.from_wav(path, sr=None).duration_s
    except Exception:
        raise FfmpegError(f"cannot determine duration of {path}")
