"""Input-audio ingestion: validate, normalize, extract features.

Handles the two upload entries (voice clip, beat/loop):
- validate size + duration within configured bounds
- decode arbitrary container via ffmpeg to PCM16 WAV
- resample to 44.1 kHz
- voice: extract `VoiceTimbre`
- loop: estimate `LoopAnalysis` (bpm/key/energy) for intent alignment

All artifacts are stored under the job's storage folder for audit.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.config import Settings
from app.dsp import encoders
from app.dsp.beat_analysis import LoopAnalysis, analyze_loop
from app.dsp.voiceprint import VoiceFeatures, extract_voice_features
from app.dsp.synthesis import VoiceTimbre
from app.dsp.wav_io import Audio

log = logging.getLogger(__name__)


class IngestError(ValueError):
    pass


class AudioIngest:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ------------------------------------------------------------------
    def ingest_voice(self, raw_path: str | Path, job_dir: Path) -> tuple[Audio, VoiceTimbre]:
        raw_path = Path(raw_path)
        if raw_path.stat().st_size > self.settings.max_upload_bytes:
            raise IngestError("voice clip exceeds upload size limit")
        wav = encoders.decode_to_wav(raw_path, job_dir / "input.wav",
                                     sr=self.settings.sample_rate)
        audio = Audio.from_wav(wav, sr=self.settings.sample_rate)
        if audio.duration_s < self.settings.min_voice_secs:
            raise IngestError(
                f"voice clip too short ({audio.duration_s:.1f}s < "
                f"{self.settings.min_voice_secs}s)"
            )
        if audio.duration_s > self.settings.max_voice_secs:
            raise IngestError(
                f"voice clip too long ({audio.duration_s:.1f}s > "
                f"{self.settings.max_voice_secs}s)"
            )
        feats = extract_voice_features(audio)
        vt = VoiceTimbre(
            f0_mean_hz=max(70.0, min(350.0, feats.f0_mean_hz)),
            brightness=feats.brightness,
        )
        log.info(
            "voice ingested: %.1fs f0=%.0fHz brightness=%.2f",
            audio.duration_s, feats.f0_mean_hz, feats.brightness,
            extra={"stage": "ingest_voice"},
        )
        return audio, vt

    def ingest_loop(self, raw_path: str | Path, job_dir: Path) -> tuple[Audio, LoopAnalysis]:
        raw_path = Path(raw_path)
        if raw_path.stat().st_size > self.settings.max_upload_bytes:
            raise IngestError("loop exceeds upload size limit")
        wav = encoders.decode_to_wav(raw_path, job_dir / "input.wav",
                                     sr=self.settings.sample_rate)
        audio = Audio.from_wav(wav, sr=self.settings.sample_rate)
        if audio.duration_s < 2.0:
            raise IngestError("loop too short to analyze (< 2s)")
        analysis = analyze_loop(audio)
        log.info(
            "loop ingested: %.1fs bpm=%s key=%s energy=%.2f",
            audio.duration_s, analysis.bpm, analysis.key_hint, analysis.energy,
            extra={"stage": "ingest_loop"},
        )
        return audio, analysis
