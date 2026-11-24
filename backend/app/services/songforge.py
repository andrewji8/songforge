"""Service facade used by the orchestrator.

Bundles intent parsing, lyrics generation, and input-audio ingestion
(voice / loop uploads) with deterministic fallbacks, so a stage never
depends on a single external service.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.config import Settings
from app.dsp.synthesis import Intent, VoiceTimbre, parse_key_key_to_intent
from app.dsp.wav_io import Audio
from app.services import fallback
from app.services.llm import LLMService

log = logging.getLogger(__name__)

# deterministic seed derived from the job id so retries are reproducible
def seed_from_job(job_id: str) -> int:
    return int(re.sub(r"\D", "", job_id), 16) % (2**31) if re.sub(r"\D", "", job_id) else 0


class IntentParseError(ValueError):
    pass


class SongForgeService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LLMService(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            settings.llm_timeout_s,
        )

    # --- intent -------------------------------------------------------
    def build_intent(
        self,
        description: str,
        style_hints: list[str],
        job_id: str,
        voice: Optional[VoiceTimbre] = None,
    ) -> tuple[Intent, dict]:
        """Parse description -> structured intent. LLM first, heuristics after."""
        raw: dict = {}
        if self.llm.enabled:
            try:
                raw = self.llm.parse_intent(description, style_hints)
                log.info("llm intent parsed: %s", raw.get("key"), extra={"stage": "intent"})
            except Exception as e:
                log.warning("llm intent failed (%s); using heuristics", e,
                            extra={"stage": "intent"})
                raw = {}
        if not raw:
            raw = fallback.fallback_intent(description, style_hints)

        key = raw.get("key", "Am")
        if key not in ("Am", "A", "C", "Cm", "Dm", "Em", "G", "F"):
            key = "Am"
        bpm = int(max(60, min(200, raw.get("bpm", 90))))
        energy = float(max(0.0, min(1.0, raw.get("energy", 0.5))))
        meta = {
            "genre": raw.get("genre", "pop"),
            "mood": raw.get("mood", "warm"),
            "source": "llm" if self.llm.enabled else "heuristic",
        }
        intent = parse_key_key_to_intent(key, bpm, energy, voice=voice)
        intent.energy = energy
        return intent, meta

    # --- lyrics -------------------------------------------------------
    def generate_lyrics(
        self,
        description: str,
        language: str,
        genre: str,
        custom_lyrics: Optional[str],
    ) -> dict:
        """Return {sections:[{section,lines}], source}."""
        if self.llm.enabled and not custom_lyrics:
            try:
                res = self.llm.write_lyrics(description, language, custom_lyrics, genre)
                if res.get("sections"):
                    res.setdefault("source", "llm")
                    return res
            except Exception as e:
                log.warning("llm lyrics failed (%s); using template", e,
                            extra={"stage": "lyrics"})
        res = fallback.fallback_lyrics(description, language)
        if custom_lyrics:
            res["source"] = "user"
        return res
