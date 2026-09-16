from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    title: str
    relative_path: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    source_id: str
    title: str
    relative_path: str
    locator: str
    text: str
    content_hash: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    source_id: str
    title: str
    relative_path: str
    locator: str
    text: str
    score: float
    access: str = "private"
    content_hash: str = ""


@dataclass(frozen=True)
class SyncStats:
    indexed_files: int
    skipped_files: int
    deleted_sources: int
    chunk_count: int
