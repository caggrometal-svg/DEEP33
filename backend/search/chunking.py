from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


TOKEN_RE = re.compile(r"[\\wáéíóúüñÁÉÍÓÚÜÑ]{2,}", re.UNICODE)
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\\s+")
SPACE_RE = re.compile(r"\\s+")


@dataclass(frozen=True)
class DocumentChunk:
    content: str
    chunk_index: int
    token_count: int
    metadata: dict[str, Any]


def _normalize_text(text: str) -> str:
    paragraphs = []
    for block in re.split(r"\\n\\s*\\n+", text.replace("\\r\\n", "\\n").replace("\\r", "\\n")):
        clean = SPACE_RE.sub(" ", block).strip()
        if clean:
            paragraphs.append(clean)
    return "\\n\\n".join(paragraphs)


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def _hard_split(text: str, target_chars: int) -> list[tuple[str, str]]:
    words = text.split()
    if not words:
        return []
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        projected = current_len + len(word) + (1 if current else 0)
        if current and projected > target_chars:
            parts.append((" ".join(current), "word"))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = projected
    if current:
        parts.append((" ".join(current), "word"))
    return parts


def _split_unit(text: str, target_chars: int) -> list[tuple[str, str]]:
    if len(text) <= target_chars:
        return [(text, "paragraph")]
    sentences = [item.strip() for item in SENTENCE_RE.split(text) if item.strip()]
    if len(sentences) <= 1:
        return _hard_split(text, target_chars)

    parts: list[tuple[str, str]] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        projected = current_len + len(sentence) + (1 if current else 0)
        if current and projected > target_chars:
            parts.append((" ".join(current), "sentence"))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len = projected
    if current:
        parts.append((" ".join(current), "sentence"))
    return parts


def _overlap_tail(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    first_space = tail.find(" ")
    return tail[first_space + 1 :].strip() if first_space >= 0 else tail.strip()


def chunk_document(
    text: str,
    *,
    base_metadata: dict[str, Any] | None = None,
    target_chars: int = 1400,
    overlap_chars: int = 220,
) -> list[DocumentChunk]:
    target_chars = max(400, min(4000, int(target_chars)))
    overlap_chars = max(0, min(target_chars // 2, int(overlap_chars)))
    normalized = _normalize_text(text)
    if not normalized:
        return []

    units: list[tuple[str, str, int]] = []
    for section_index, paragraph in enumerate(normalized.split("\n\n")):
        for unit, boundary in _split_unit(paragraph, target_chars):
            units.append((unit, boundary, section_index))

    chunks: list[DocumentChunk] = []
    current = ""
    current_boundary = ""
    current_section = 0

    def emit(value: str, boundary: str, section_index: int) -> None:
        index = len(chunks)
        tokens = _tokens(value)
        content_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        metadata = dict(base_metadata or {})
        metadata.update(
            {
                "chunk_index": index,
                "section_index": section_index,
                "boundary": boundary,
                "char_count": len(value),
                "token_count": len(tokens),
                "content_sha256": content_hash,
                "chunking": {
                    "strategy": "paragraph_sentence_word",
                    "target_chars": target_chars,
                    "overlap_chars": overlap_chars,
                },
            }
        )
        chunks.append(
            DocumentChunk(
                content=value,
                chunk_index=index,
                token_count=len(tokens),
                metadata=metadata,
            )
        )

    for unit, boundary, section_index in units:
        candidate = f"{current} {unit}".strip() if current else unit
        if current and len(candidate) > target_chars:
            emit(current, current_boundary or "paragraph", current_section)
            overlap = _overlap_tail(current, overlap_chars)
            current = f"{overlap} {unit}".strip() if overlap else unit
            current_boundary = boundary
            current_section = section_index
        else:
            current = candidate
            current_boundary = current_boundary or boundary
            current_section = section_index

    if current:
        emit(current, current_boundary or "paragraph", current_section)

    return chunks
