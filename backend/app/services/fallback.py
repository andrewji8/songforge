"""Deterministic fallbacks so the full pipeline works with zero external
models (CI, offline, or when the LLM gateway is unconfigured).

- `fallback_intent` : keyword -> (key, bpm, energy, genre, mood)
- `fallback_lyrics` : template sections seeded by a word hash of the idea

Both are stable per-input (hash-based), which keeps results reproducible
and auditable.
"""
from __future__ import annotations

import hashlib
from typing import Optional

# (key, bpm, energy, genre, mood) — keep the key set aligned with the
# synthesis engine's known keys: Am A C Cm Dm Em G F
_TABLE: list[tuple[tuple, tuple[str, ...]]] = [
    (("lofi", "lo-fi", "lofi"), (("Am", 84, 0.35, "lo-fi", "chill"),
                                  ("Cm", 78, 0.3, "lo-fi", "dreamy"))),
    (("sad", "melanchol", "sad", "rain", "rain", "lonel", "miss"),
     (("Am", 72, 0.3, "acoustic ballad", "sad"),
      ("Cm", 68, 0.25, "piano ballad", "heartbroken"))),
    (("happy", "upbeat", "party", "summer", "joy", "joyful", "celebrat"),
     (("C", 118, 0.7, "pop", "uplifting"), ("G", 122, 0.75, "dance-pop", "euphoric"))),
    (("edm", "electronic", "bass", "festival", "club"),
     (("Am", 128, 0.85, "EDM", "anthemic"), ("F", 132, 0.9, "big-room", "energetic"))),
    (("rock", "guitar", "indie"),
     (("Em", 110, 0.7, "indie rock", "driving"), ("Dm", 104, 0.65, "alt rock", "raw"))),
    (("jazz", "swing", "blues"),
     (("F", 96, 0.5, "jazz", "smooth"), ("Cm", 92, 0.45, "soul", "wistful"))),
    (("cinematic", "epic", "trailer", "game", "orchestr"),
     (("Em", 90, 0.8, "cinematic", "epic"), ("Am", 88, 0.75, "orchestral", "heroic"))),
    (("hiphop", "hip-hop", "trap", "drill", "rap"),
     (("Dm", 90, 0.6, "hip-hop", "confident"), ("Cm", 140, 0.8, "trap", "dark"))),
    (("calm", "sleep", "study", "ambient", "meditat", "lofi"),
     (("C", 66, 0.25, "ambient", "serene"), ("Cm", 60, 0.2, "ambient", "meditative"))),
]

_DEFAULT = ("Am", 90, 0.5, "pop", "warm")


def fallback_intent(description: str, style_hints: list[str]) -> dict:
    text = " ".join([description or ""] + list(style_hints or [])).lower()
    idx = int(
        hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16
    )
    # pick a matching row or the default by keyword
    picked = None
    for keywords, options in _TABLE:
        if any(k in text for k in keywords):
            picked = options[idx % len(options)]
            break
    key, bpm, energy, genre, mood = picked if picked else _DEFAULT
    if style_hints:
        # style hints can nudge the key deterministically
        hint_idx = int(
            hashlib.sha256(",".join(style_hints).encode()).hexdigest()[:4], 16
        )
        keys = ["Am", "A", "C", "Cm", "Dm", "Em", "G", "F"]
        key = keys[hint_idx % len(keys)]
    return {"key": key, "bpm": int(bpm), "energy": float(energy),
            "genre": genre, "mood": mood}


def fallback_lyrics(idea: str, language: str = "zh") -> dict:
    """Template lyrics seeded by the idea hash; theme words are woven in."""
    seed = int(hashlib.sha256((idea or "song").encode()).hexdigest()[:6], 16)
    theme = (idea or "这首歌").strip()[:12]

    if language in ("zh", "zh-CN", "cn"):
        templates = [
            [
                ("intro", []),
                ("verse", [f"{theme}，在耳边回响", "城市霓虹 染亮窗外月光",
                            "把心事折成纸船 放下水中央", "时间走得太慢 像停在昨天"]),
                ("chorus", [f"{theme} 是我唯一的远方", "就算世界静音 你仍有声响",
                            "把心跳调到 同一频率", "这首歌 替你唱给那晚的月光"]),
                ("verse", ["旧照片泛黄 笑容还滚烫", "风把承诺 吹散在走廊",
                            "如果重来 我会更勇敢", "把遗憾写进 下一段副歌"]),
                ("chorus", [f"{theme} 是我唯一的远方", "就算世界静音 你仍有声响",
                            "把心跳调到 同一频率", "这首歌 替你唱给那晚的月光"]),
                ("bridge", ["别再怕天亮", "别再假装无恙", "把名字 唱给风去收藏"]),
                ("outro", [f"{theme}……", "（渐弱）"]),
            ],
            [
                ("intro", []),
                ("verse", ["雨落在 旧站台", f"{theme} 被风吹散", "你转身 那一眼",
                            "定格成 黑白片"]),
                ("chorus", [f"{theme} 在夜里发烫", "每个节拍 都是回信的行",
                            "我追着 不回头的方向", "把想念 调成整夜循环"]),
                ("verse", ["咖啡凉了 故事短", "你的位置 空了一排座椅",
                            "我还在 原地练习", "练习 不带你的语气"]),
                ("chorus", [f"{theme} 在夜里发烫", "每个节拍 都是回信的行",
                            "我追着 不回头的方向", "把想念 调成整夜循环"]),
                ("bridge", ["如果时间 可以倒带", "我会把 沉默换成拥抱"]),
                ("outro", [f"{theme}……"]),
            ],
        ]
    else:
        templates = [
            [
                ("intro", []),
                ("verse", [f"{theme} keeps humming in my head",
                           "City lights and the sound we made",
                           "Fold the worry into paper boats", "Time moves slow where you are"]),
                ("chorus", [f"{theme} is the far side of me",
                            "Even in the hush you still sound",
                            "Tuning our hearts to the same beat",
                            "This song sings it to the moon"]),
                ("verse", ["Old photos warm, your smile still there",
                           "Wind took the promise, scattered it in the air",
                            "If I could do it over, braver",
                            "I'd write the regrets in the next chorus"]),
                ("chorus", [f"{theme} is the far side of me",
                            "Even in the hush you still sound",
                            "Tuning our hearts to the same beat",
                            "This song sings it to the moon"]),
                ("bridge", ["Don't be scared of the dawn",
                            "Don't pretend you're fine", "I'll sing your name to the wind"]),
                ("outro", [f"{theme}…"]),
            ]
        ]
    chosen = templates[seed % len(templates)]
    # convert (section, lines) tuples into section dicts; keep intro even if empty
    sections = [
        {"section": name, "lines": list(lines)}
        for (name, lines) in chosen
    ]
    return {
        "sections": sections,
        "source": "template",
        "seed": seed,
    }
