"""Split long NPC dialogue into TTS-safe chunks at natural boundaries."""
from __future__ import annotations

import re
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from webapp.tts.model_config import TTSModelConfig

# Sentence boundaries: period, question, exclamation, ellipsis (incl. unicode …).
_SENTENCE_BOUNDARY = re.compile(
    r'(?<=[.!?…])(?:["\'\)\]]+)?(?=\s+|$)'
)

# Clause boundaries when a single sentence is still too long.
_CLAUSE_BOUNDARY = re.compile(
    r'(?<=[,;:—–])(?:["\'\)\]]+)?(?=\s+|$)'
)

# Stage directions / asides we prefer to keep attached to the following speech.
_LEADING_ASIDE = re.compile(
    r'^(\[[^\]]+\]|\*[^*]+\*)\s*',
    re.IGNORECASE,
)


def split_tts_text(
    text: str,
    max_chars: Optional[int] = None,
    config: Optional['TTSModelConfig'] = None,
) -> List[str]:
    """
    Split dialogue into chunks that respect provider length limits.

    Pass ``config`` (from ``get_model_config``) for per-model limits, or
    ``max_chars`` explicitly.
    """
    if config is not None and max_chars is None:
        max_chars = config.max_chars
    if max_chars is None:
        from webapp.tts.model_config import MODEL_PROFILES
        max_chars = MODEL_PROFILES['cosyvoice'].max_chars

    normalized = ' '.join(str(text or '').split())
    if not normalized:
        return []

    limit = max_chars
    if len(normalized) <= limit:
        return [normalized]

    segments = _split_sentences(normalized)
    packed = _pack_segments(segments, limit)
    packed = [_split_oversized(chunk, limit) for chunk in packed]
    flat: List[str] = [piece for chunk in packed for piece in chunk if piece.strip()]
    flat = _merge_tiny_tail(flat, limit)
    return flat


def _split_sentences(text: str) -> List[str]:
    parts = _SENTENCE_BOUNDARY.split(text)
    segments: List[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        # Attach leading [aside] or *action* to the same segment body.
        aside = ''
        while True:
            match = _LEADING_ASIDE.match(piece)
            if not match:
                break
            aside += match.group(0)
            piece = piece[match.end():].strip()
        if aside and piece:
            segments.append(f'{aside}{piece}')
        elif aside:
            segments.append(aside.strip())
        else:
            segments.append(piece)
    return segments


def _pack_segments(segments: List[str], limit: int) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        join_len = len(segment) + (1 if current else 0)
        if current_len + join_len <= limit:
            current.append(segment)
            current_len += join_len
            continue
        if current:
            chunks.append(' '.join(current))
            current = []
            current_len = 0
        if len(segment) <= limit:
            current = [segment]
            current_len = len(segment)
        else:
            chunks.extend(_split_oversized(segment, limit))

    if current:
        chunks.append(' '.join(current))
    return chunks


def _split_oversized(text: str, limit: int) -> List[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]

    # Try clause-level splits first.
    clause_parts = _CLAUSE_BOUNDARY.split(text)
    if len(clause_parts) > 1:
        return _pack_segments([p.strip() for p in clause_parts if p.strip()], limit)

    # Word-wrap as last resort.
    words = text.split()
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= limit:
            current.append(word)
            current_len += add_len
        else:
            if current:
                chunks.append(' '.join(current))
            if len(word) > limit:
                # Extremely long token; hard split.
                for i in range(0, len(word), limit):
                    chunks.append(word[i:i + limit])
                current = []
                current_len = 0
            else:
                current = [word]
                current_len = len(word)
    if current:
        chunks.append(' '.join(current))
    return chunks


def _merge_tiny_tail(chunks: List[str], limit: int, min_tail: int = 48) -> List[str]:
    if len(chunks) < 2:
        return chunks
    tail = chunks[-1]
    if len(tail) >= min_tail:
        return chunks
    merged = f'{chunks[-2]} {tail}'.strip()
    if len(merged) <= limit:
        return chunks[:-2] + [merged]
    return chunks
