"""Music-provider abstraction.

The pipeline's `compose` stage talks to a `MusicProvider`. Two concrete
implementations ship:

- SimulatedProvider  : built-in deterministic renderer (DSP.synthesis).
  Default in dev/CI; no external calls; reproducible per seed.
- HttpMusicProvider  : thin gateway to an external model API (Suno /
  OpenAudio-style). Expects a REST contract:

    POST {base}/composes
    body:  {intent: {...}, seed: int, duration_s: float, stems: bool}
    resp:  {audio_b64: str, stems_b64: {name: str}, key, bpm, meta}

  When `SF_PROVIDER=http` and a key/url are configured, the worker uses it.
  Failures surface as `ProviderError` and are retried by the orchestrator.
"""
from __future__ import annotations

import base64
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping, Optional

import numpy as np
import wave

from app.dsp.synthesis import Intent, render_song
from app.dsp.wav_io import Audio
from app.observability import metrics

log = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retriable: bool = True):
        super().__init__(message)
        self.retriable = retriable


@dataclass
class ComposeResult:
    stems: Mapping[str, np.ndarray]  # name -> float array
    key: str
    bpm: int
    section_map: list
    seed: int
    source: str  # simulated | http
    extra: dict = field(default_factory=dict)


class MusicProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def compose_full(
        self, intent: Intent, duration_s: float, seed: int
    ) -> ComposeResult: ...

    def compose_hook(
        self, intent: Intent, seconds: float, seed: int
    ) -> Mapping[str, np.ndarray]:
        """Fast hook/preview synthesis. Default falls back to full compose
        trimmed, but SimulatedProvider overrides with a direct chorus render
        to meet the 25s SLO."""
        res = self.compose_full(intent, seconds, seed)
        return res.stems


class SimulatedProvider(MusicProvider):
    name = "simulated"

    def compose_full(
        self, intent: Intent, duration_s: float, seed: int
    ) -> ComposeResult:
        stems, section_map = render_song(intent, duration_s, seed)
        return ComposeResult(
            stems=stems,
            key=intent.key,
            bpm=intent.bpm,
            section_map=section_map,
            seed=seed,
            source="simulated",
        )

    def compose_hook(
        self, intent: Intent, seconds: float, seed: int
    ) -> Mapping[str, np.ndarray]:
        from app.dsp.synthesis import render_hook

        return render_hook(intent, seconds, seed)


def _wav_bytes_from_stems(stems: Mapping[str, np.ndarray], sr: int = 44100) -> bytes:
    """Mix stems to stereo and return an in-memory WAV blob."""
    from app.dsp.mastering import mix_stems

    mix = mix_stems(stems, sr)  # (2, n)
    pcm = (np.clip(mix.T, -1, 1).astype("<i2") * 32767)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return bio.getvalue()


class HttpMusicProvider(MusicProvider):
    name = "http"

    def __init__(self, base_url: str, api_key: str | None, timeout_s: float = 240.0):
        if not base_url:
            raise ProviderError("SF_PROVIDER=http requires SF_PROVIDER_BASE_URL",
                                retriable=False)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def _post(self, path: str, payload: dict) -> dict:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                r = client.post(url, json=payload, headers=headers)
        except Exception as e:  # network / timeout -> retriable
            metrics.PROVIDER_ERRORS.labels(self.name).inc()
            raise ProviderError(f"provider network error: {e}") from e
        if r.status_code >= 500 or r.status_code in (429,):
            metrics.PROVIDER_ERRORS.labels(self.name).inc()
            raise ProviderError(f"provider returned {r.status_code}", retriable=True)
        if r.status_code >= 400:
            raise ProviderError(
                f"provider rejected request: {r.status_code} {r.text[:200]}",
                retriable=False,
            )
        return r.json()

    def compose_full(
        self, intent: Intent, duration_s: float, seed: int
    ) -> ComposeResult:
        # JSON-safe intent (voice is a dataclass / None; scale is tuples)
        intent_payload = {
            "key": intent.key,
            "bpm": intent.bpm,
            "energy": intent.energy,
            "scale": [float(s) for s in intent.scale],
            "root_midi": float(intent.root_midi),
            "time_signature": intent.time_signature,
            "voice": (
                None
                if intent.voice is None
                else {
                    "f0_mean_hz": intent.voice.f0_mean_hz,
                    "brightness": intent.voice.brightness,
                    "formants": list(intent.voice.formants),
                }
            ),
        }
        payload = {
            "intent": intent_payload,
            "seed": seed,
            "duration_s": duration_s,
            "stems": True,
        }
        data = self._post("/composes", payload)
        audio_b64 = data.get("audio_b64")
        stems_b64 = data.get("stems_b64") or {}
        stems: dict[str, np.ndarray] = {}
        if stems_b64:
            for name, b64 in stems_b64.items():
                arr = _stems_from_b64(b64)
                stems[str(name)] = arr
        if not stems and audio_b64:
            # only full mix returned: split into a single "mix" stem
            stems["mix"] = _audio_b64_to_float(audio_b64)
        if not stems:
            raise ProviderError("provider returned no audio payload", retriable=False)
        return ComposeResult(
            stems=stems,
            key=str(data.get("key", intent.key)),
            bpm=int(data.get("bpm", intent.bpm)),
            section_map=data.get("section_map", []),
            seed=seed,
            source="http",
            extra=data.get("meta", {}),
        )


def _stems_from_b64(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    w = io.BytesIO(raw)
    with wave.open(w, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    arr = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        arr = arr.reshape(-1, ch)
    return arr


def _audio_b64_to_float(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    w = io.BytesIO(raw)
    with wave.open(w, "rb") as wf:
        ch = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    arr = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        arr = arr.reshape(-1, ch).mean(axis=1)
    return arr


def build_provider(settings) -> MusicProvider:
    if settings.provider == "http":
        try:
            return HttpMusicProvider(
                settings.provider_base_url,
                settings.provider_api_key,
                settings.provider_timeout_s,
            )
        except ProviderError as e:
            log.warning("http provider unavailable (%s); falling back to simulated", e)
            metrics.PROVIDER_ERRORS.labels("http").inc()
            return SimulatedProvider()
    return SimulatedProvider()
