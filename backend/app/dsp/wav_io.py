"""WAV I/O for PCM16 mono/stereo.

Deliberately dependency-free (wave + numpy) so the engine runs anywhere,
including CI without ffmpeg. MP3 paths go through `encoders`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import wave


@dataclass(frozen=True)
class Audio:
    """Float32 audio in [-1, 1], shape (samples,) or (samples, channels)."""

    data: np.ndarray
    sample_rate: int

    @property
    def channels(self) -> int:
        return self.data.ndim

    @property
    def num_frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def duration_s(self) -> float:
        return self.num_frames / float(self.sample_rate)

    def mono(self) -> "Audio":
        if self.data.ndim == 2:
            return Audio(self.data.mean(axis=1).astype(np.float32), self.sample_rate)
        return self

    def resample(self, target_sr: int) -> "Audio":
        if target_sr == self.sample_rate:
            return self
        from scipy.signal import resample_poly
        from math import gcd

        g = gcd(self.sample_rate, target_sr)
        up, down = target_sr // g, self.sample_rate // g
        out = resample_poly(self.data, up, down)
        return Audio(out.astype(np.float32), target_sr)

    def to_pcm16(self) -> np.ndarray:
        """Quantize to int16 in channel order (samples, ch)."""
        d = np.clip(self.data, -1.0, 1.0)
        return (d * 32767).astype("<i2")

    def save_wav(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = self.to_pcm16()
        if pcm.ndim == 1:
            pcm = pcm[:, None]
        with wave.open(str(path), "wb") as w:
            w.setnchannels(pcm.shape[1])
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())

    @classmethod
    def from_wav(cls, path: str | Path, sr: int | None = None) -> "Audio":
        with wave.open(str(path), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"only 16-bit PCM WAV supported (got {width * 8} bit)")
        raw = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            raw = raw.reshape(-1, ch)
        if sr is not None:
            audio = cls(raw, sr)
            return audio.resample(sr)
        return cls(raw, sr)

    def slice_seconds(self, start_s: float, end_s: float) -> "Audio":
        a, b = int(start_s * self.sample_rate), int(end_s * self.sample_rate)
        a = max(0, a)
        b = min(self.num_frames, b)
        return Audio(self.data[a:b], self.sample_rate)

    @classmethod
    def silence(cls, seconds: float, sr: int = 44100, channels: int = 2) -> "Audio":
        n = int(seconds * sr)
        shape = (n,) if channels == 1 else (n, channels)
        return cls(np.zeros(shape, dtype=np.float32), sr)
