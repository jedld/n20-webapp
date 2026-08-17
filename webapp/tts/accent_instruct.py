"""Accent labels → TTS instruct fragments and bake calibration lines.

YAML ``voice.accent`` is a short label (british, irish, eastern european, …).
CosyVoice Instruct2 is trained on short ``speak in X`` clauses. Qwen3 VoiceDesign
follows natural-language descriptions and defaults toward General American unless
the accent is named early and the calibration line is dialect-colored.
"""

from __future__ import annotations

from typing import Optional

# Short CosyVoice Instruct2 clauses.
COSYVOICE_ACCENT_INSTRUCT: dict[str, str] = {
    "american": "speak in clear American English",
    "us": "speak in clear American English",
    "usa": "speak in clear American English",
    "british": "speak in British English, not American",
    "uk": "speak in British English, not American",
    "rp": "speak in Received Pronunciation British English, not American",
    "english": "speak in clear English",
    "en": "speak in clear English",
    "irish": "speak in English with an Irish accent, not American",
    "scottish": "speak in English with a Scottish accent, not American",
    "australian": "speak in English with an Australian accent, not American",
    "romanian": "speak in English with a Romanian accent",
    "eastern european": "speak in English with an Eastern European accent",
    "eastern_european": "speak in English with an Eastern European accent",
    "transylvanian": "speak in English with a Transylvanian accent",
    "slavic": "speak in English with a Slavic accent",
    "german": "speak in English with a German accent",
    "french": "speak in English with a French accent",
    "spanish": "speak in English with a Spanish accent",
    "italian": "speak in English with an Italian accent",
    "russian": "speak in English with a Russian accent",
    "chinese": "speak in Mandarin Chinese",
    "zh": "speak in Mandarin Chinese",
    "mandarin": "speak in Mandarin Chinese",
    "cantonese": "speak Cantonese",
    "yue": "speak Cantonese",
}

# Qwen3 VoiceDesign: lead with accent. Official examples look like
# "Male, 40s, British accent, formal and refined speaking style".
QWEN3_ACCENT_INSTRUCT: dict[str, str] = {
    "british": (
        "British English accent, not American, not General American. "
        "Received Pronunciation: non-rhotic, dropped R after vowels, "
        "broad A in bath and dance, clipped consonants"
    ),
    "uk": (
        "British English accent, not American, not General American. "
        "Received Pronunciation: non-rhotic, dropped R after vowels, "
        "broad A in bath and dance, clipped consonants"
    ),
    "rp": (
        "Received Pronunciation British English, not American: "
        "non-rhotic, clipped, formal"
    ),
    "irish": (
        "Irish English accent, not American: lilting melody, "
        "softer rhotic R, distinctive Irish vowels"
    ),
    "scottish": (
        "Scottish English accent, not American: rolled R, "
        "short vowels, Highland cadence"
    ),
    "australian": (
        "Australian English accent, not American: broader vowels, "
        "rising terminals"
    ),
    "eastern european": (
        "English with a clear Eastern European accent, Romanian or "
        "Transylvanian, not American: darker vowels, slightly rolled R, "
        "even stress, old-world cadence"
    ),
    "eastern_european": (
        "English with a clear Eastern European accent, Romanian or "
        "Transylvanian, not American: darker vowels, slightly rolled R, "
        "even stress, old-world cadence"
    ),
    "romanian": (
        "English with a Romanian accent, not American: darker vowels, "
        "slightly rolled R, even stress"
    ),
    "transylvanian": (
        "English with a Transylvanian / Romanian accent, not American: "
        "aristocratic, darker vowels, slightly rolled R"
    ),
    "slavic": (
        "English with a Slavic accent, not American: heavier consonants, "
        "rolled R, flattened intonation"
    ),
    "russian": (
        "English with a Russian accent, not American: dark L, rolled R, "
        "hard consonants"
    ),
    "german": "English with a German accent, not American: sharper consonants",
    "french": "English with a French accent, not American: uvular R, even rhythm",
    "spanish": "English with a Spanish accent, not American: tapped R, pure vowels",
    "italian": "English with an Italian accent, not American: open vowels, musical cadence",
}

# Spoken calibration line for VoiceDesign bake. Must stay dialect-neutral-to-target;
# do not describe a different voice (the old gravelly default fought British/EE).
ACCENT_BAKE_TEXT: dict[str, str] = {
    "british": (
        "Good afternoon. I have not forgotten the garden party after dark. "
        "I shall speak just as I always do."
    ),
    "uk": (
        "Good afternoon. I have not forgotten the garden party after dark. "
        "I shall speak just as I always do."
    ),
    "rp": (
        "Good afternoon. I have not forgotten the garden party after dark. "
        "I shall speak just as I always do."
    ),
    "irish": (
        "Ah, it's a grand morning to be talking. I'll speak just as I always do."
    ),
    "scottish": (
        "Aye, I'll speak these words clearly, just as I always do."
    ),
    "eastern european": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
    "eastern_european": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
    "romanian": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
    "transylvanian": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
    "slavic": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
    "russian": (
        "I speak these words slowly and clearly, just as I always speak. "
        "The evening is quiet, and I have not forgotten what must be done. "
        "This village has three roads, and the river is very cold tonight."
    ),
}

DEFAULT_BAKE_TEXT = (
    "I will speak these words clearly and naturally, just as I always speak."
)


def normalize_accent_label(accent: Optional[str]) -> str:
    return " ".join(str(accent or "").strip().lower().replace("-", " ").replace("_", " ").split())


def qwen3_accent_instruct(accent: Optional[str]) -> str:
    """VoiceDesign instruct clause, or empty for American / unset."""
    key = normalize_accent_label(accent)
    if not key or key in ("none", "american", "us", "usa"):
        return ""
    if key in QWEN3_ACCENT_INSTRUCT:
        return QWEN3_ACCENT_INSTRUCT[key]
    return f"{accent.strip()} accent, not American"


def bake_text_for_accent(accent: Optional[str]) -> str:
    key = normalize_accent_label(accent)
    return ACCENT_BAKE_TEXT.get(key) or DEFAULT_BAKE_TEXT
