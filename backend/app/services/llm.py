"""OpenAI-compatible LLM client for intent parsing + lyrics.

Optional: when SF_LLM_BASE_URL / SF_LLM_API_KEY are absent the callers
fall back to deterministic heuristics (see `fallback.py`), so lyrics and
structure still work in dev/CI without any external model.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

INTENT_SYSTEM = (
    "You are the intent engine of SongForge. Given a one-line user idea, "
    "return strict JSON: {\"key\": \"Am|A|C|Cm|Dm|Em|G|F\", "
    "\"bpm\": int 60..200, \"energy\": 0.0..1.0, "
    "\"genre\": str, \"mood\": str}. No prose."
)

LYRICS_SYSTEM = (
    "You write release-ready song lyrics in the requested language. "
    "Output strict JSON: {\"sections\": [{\"section\": \"intro|verse|"
    "chorus|bridge|outro\", \"lines\": [str]}]}. Use the user's idea as "
    "the hook/theme. Keep lines short (<= 18 chars) and singable."
)


class LLMService:
    def __init__(
        self,
        base_url: Optional[str],
        api_key: Optional[str],
        model: str = "gpt-4o-mini",
        timeout_s: float = 30.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _chat(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

    def parse_intent(self, description: str, style_hints: list[str]) -> dict:
        user = (
            f"idea: {description}\nstyle_hints: {', '.join(style_hints) or 'none'}\n"
            "Reply with the JSON object only."
        )
        raw = self._chat(INTENT_SYSTEM, user)
        return _extract_json(raw)

    def write_lyrics(
        self,
        idea: str,
        language: str,
        custom_lyrics: Optional[str],
        genre: str,
    ) -> dict:
        if custom_lyrics:
            return {"sections": _split_custom(custom_lyrics)}
        user = (
            f"idea: {idea}\nlanguage: {language}\ngenre: {genre}\n"
            "Reply with the JSON object only."
        )
        raw = self._chat(LYRICS_SYSTEM, user)
        return _extract_json(raw)


def _extract_json(text: str) -> dict:
    """Robustly pull the first JSON object out of a model reply."""
    text = text.strip()
    if not text.startswith("{"):
        s = text.find("{")
        e = text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    return json.loads(text)


def _split_custom(text: str) -> list[dict]:
    """Minimal split of user-supplied lyrics into sections by [tag] or
    blank lines; defaults to verse/chorus alternation when untagged."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    sections: list[dict] = []
    current: Optional[dict] = None
    for ln in lines:
        tag = ln.strip("[] ").lower()
        if ln.startswith("[") and ln.endswith("]"):
            current = {"section": tag or "verse", "lines": []}
            sections.append(current)
            continue
        if current is None:
            current = {"section": "verse", "lines": []}
            sections.append(current)
        current["lines"].append(ln)
    # if user gave a single blob with no tags, split into verse/chorus
    if len(sections) == 1 and len(sections[0]["lines"]) > 8:
        mid = len(sections[0]["lines"]) // 2
        first = sections[0]
        out = [
            {"section": "verse", "lines": first["lines"][:mid]},
            {"section": "chorus", "lines": first["lines"][mid:]},
        ]
        sections = out
    return sections or [{"section": "verse", "lines": lines}]
