from __future__ import annotations

import hashlib
import re

from .schema import DocumentChunk, SourceDocument


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

# 断开优先级：句子末尾 > 段落 > 空白。硬切只在整段没有任何边界时兜底。
SENTENCE_END_CHARS = "。！？；.!?;"
PARAGRAPH_MARKER = "\n\n"


def _cut_position(text: str, start: int, max_chars: int, floor: int) -> int:
    """在 [start+floor, start+max_chars] 里找最靠后的自然边界，返回切点。"""
    limit = start + max_chars
    floor_at = start + floor
    window = text[floor_at:limit]

    best = -1
    for char in SENTENCE_END_CHARS:
        index = window.rfind(char)
        if index != -1:
            best = max(best, index + len(char))
    if best > 0:
        return floor_at + best

    index = window.rfind(PARAGRAPH_MARKER)
    if index != -1:
        return floor_at + index + len(PARAGRAPH_MARKER)

    index = window.rfind(" ")
    if index > 0:
        return floor_at + index + 1

    return limit


def _windows(
    text: str,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> list[str]:
    """按自然边界切片。

    两个要点：
    - 不在句子中间硬切（硬切会产出「半个词」的块）；
    - 不留碎尾巴：末尾不足 min_chars 的碎片并入最后一块，而不是单独成块。
      这正是之前 PDF 里出现 '\\n7' 这类 2 字符块的来源。
    """
    if len(text) <= max_chars:
        return [text]
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    floor = max(min_chars, max_chars // 2)
    windows: list[str] = []
    starts: list[int] = []
    start = 0
    while len(text) - start > max_chars:
        cut = _cut_position(text, start, max_chars, floor)
        windows.append(text[start:cut])
        starts.append(start)
        advance = (cut - start) - overlap_chars
        start += advance if advance > 0 else 1

    tail = text[start:]
    if windows and len(tail) < min_chars:
        # 碎尾巴并入最后一块：从最后一块的起点一直取到文末。
        # 这会让最后一块比 max_chars 多出至多 min_chars，宁可略微超限也不能丢字
        # （早先写成 text[len-max_chars:] 会前移起点、把中间几个字吞掉）。
        windows[-1] = text[starts[-1] :]
    else:
        windows.append(tail)
    return windows


def chunk_document(
    document: SourceDocument,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 160,
    min_chars: int = 200,
) -> list[DocumentChunk]:
    headings: list[str] = []
    sections: list[tuple[str, str]] = []
    buffer: list[str] = []
    open_fence: str | None = None

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append((" > ".join(headings) or document.title, text))
        buffer.clear()

    for line in document.text.splitlines():
        fence = FENCE_RE.match(line)
        if fence:
            token = fence.group(1)[0]
            if open_fence is None:
                open_fence = token
            elif open_fence == token:
                open_fence = None
            buffer.append(line)
            continue
        if open_fence is None:
            match = HEADING_RE.match(line)
            if match:
                flush()
                level = len(match.group(1))
                headings[:] = headings[: level - 1]
                headings.append(match.group(2).strip())
                continue
        buffer.append(line)
    flush()

    chunks: list[DocumentChunk] = []
    ordinal = 0
    for locator, section_text in sections:
        for text in _windows(section_text, max_chars, overlap_chars, min_chars):
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            identity = f"{document.source_id}\n{locator}\n{ordinal}\n{content_hash}"
            chunk_id = "chk_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    source_id=document.source_id,
                    title=document.title,
                    relative_path=document.relative_path,
                    locator=locator,
                    text=text,
                    content_hash=content_hash,
                    metadata=document.metadata,
                )
            )
            ordinal += 1
    return chunks
