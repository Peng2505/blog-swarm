from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from .chunker import chunk_document
from .embeddings import EmbeddingProvider
from .loaders import load_documents
from .schema import RetrievalHit, SourceDocument, SyncStats


COLLECTION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
# 切分算法改成边界感知后必须提升：旧索引的 chunk 边界与现在不一致，
# 静默沿用会得到"看起来没变化"的错误结论。
INDEX_VERSION = 4


class KnowledgeBase:
    """Small-corpus persistent vector store backed by stdlib SQLite."""

    def __init__(
        self,
        db_dir: str | Path,
        *,
        embedding: EmbeddingProvider,
        collection_name: str = "blog_private_knowledge",
    ) -> None:
        if not COLLECTION_NAME_RE.fullmatch(collection_name):
            raise ValueError(
                "collection_name must be 1-128 chars of letters, digits, "
                "underscore or hyphen"
            )
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.embedding = embedding
        self.manifest_path = self.db_dir / "manifest.json"
        self.sqlite_path = self.db_dir / f"{collection_name}.sqlite3"
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    access TEXT NOT NULL,
                    embedding TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection that always closes (Windows keeps a lock)."""
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {
                "embedding": self.embedding.name,
                "index_version": INDEX_VERSION,
                "dimensions": getattr(self.embedding, "dimensions", None),
                "sources": {},
            }
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if data.get("embedding") != self.embedding.name:
            raise ValueError(
                "embedding model changed; rebuild the index before syncing "
                f"({data.get('embedding')} -> {self.embedding.name})"
            )
        if data.get("index_version") != INDEX_VERSION:
            raise ValueError(
                "index format changed; rebuild the index before syncing "
                f"({data.get('index_version')} -> {INDEX_VERSION})"
            )
        return data

    def _encode_checked(
        self, texts: list[str], dimensions: int | None
    ) -> tuple[list[list[float]], int]:
        vectors = self.embedding.encode(texts)
        if len(vectors) != len(texts):
            raise ValueError(
                f"embedding returned {len(vectors)} vectors for {len(texts)} texts"
            )
        expected = dimensions
        for index, vector in enumerate(vectors):
            values = list(vector)
            if expected is None:
                expected = len(values)
                if expected == 0:
                    raise ValueError("embedding produced zero-length vectors")
            if len(values) != expected:
                raise ValueError(
                    f"embedding vector {index} has {len(values)} dimensions, "
                    f"expected {expected}"
                )
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"embedding vector {index} contains non-finite values")
        return [[float(value) for value in vector] for vector in vectors], int(expected or 0)

    def _write_manifest(self, manifest: dict) -> None:
        temp = self.manifest_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp.replace(self.manifest_path)

    @staticmethod
    def _document_hash(document: SourceDocument) -> str:
        payload = json.dumps(asdict(document), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def sync(self, source_dir: str | Path) -> SyncStats:
        manifest = self._load_manifest()
        manifest.setdefault("index_version", INDEX_VERSION)
        manifest.setdefault("dimensions", getattr(self.embedding, "dimensions", None))
        old_sources: dict[str, dict] = manifest["sources"]
        documents = load_documents(source_dir)
        if not documents and old_sources:
            # A misconfigured path, an unmounted drive or a temporarily renamed
            # directory all look like "every source disappeared". Wiping the
            # index on that is silent data loss, so refuse and let the operator
            # confirm.
            raise ValueError(
                "refusing to wipe the index: 0 documents found in "
                f"{Path(source_dir).resolve()} while manifest still lists "
                f"{len(old_sources)} source(s). Fix the source path, or delete "
                "the index to rebuild on purpose."
            )
        current_ids = {document.source_id for document in documents}
        deleted = set(old_sources) - current_ids
        indexed = 0
        skipped = 0

        with self._connect() as connection:
            db_chunk_ids = {
                str(row[0]) for row in connection.execute("SELECT chunk_id FROM chunks")
            }
            recorded_chunk_ids = {
                str(chunk_id)
                for entry in old_sources.values()
                for chunk_id in entry.get("chunk_ids", []) or []
            }
            orphans = db_chunk_ids - recorded_chunk_ids
            if orphans:
                connection.executemany(
                    "DELETE FROM chunks WHERE chunk_id = ?",
                    [(chunk_id,) for chunk_id in sorted(orphans)],
                )
                db_chunk_ids -= orphans
            for source_id, entry in list(old_sources.items()):
                recorded = set(entry.get("chunk_ids", []) or [])
                if not recorded or not recorded <= db_chunk_ids:
                    # Manifest and SQLite disagree: drop the entry so the file is
                    # re-indexed instead of being skipped as unchanged.
                    del old_sources[source_id]

            for source_id in deleted:
                connection.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
                old_sources.pop(source_id, None)

            for document in documents:
                document_hash = self._document_hash(document)
                previous = old_sources.get(document.source_id)
                if previous and previous.get("document_hash") == document_hash:
                    skipped += 1
                    continue
                connection.execute(
                    "DELETE FROM chunks WHERE source_id = ?", (document.source_id,)
                )
                chunks = chunk_document(document)
                embeddings, dimensions = self._encode_checked(
                    [chunk.text for chunk in chunks], manifest.get("dimensions")
                )
                if dimensions:
                    manifest["dimensions"] = dimensions
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, source_id, title, relative_path, locator,
                        text, content_hash, access, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.source_id,
                            chunk.title,
                            chunk.relative_path,
                            chunk.locator,
                            chunk.text,
                            chunk.content_hash,
                            "private",
                            json.dumps(vector),
                        )
                        for chunk, vector in zip(chunks, embeddings)
                    ],
                )
                old_sources[document.source_id] = {
                    "relative_path": document.relative_path,
                    "document_hash": document_hash,
                    "chunk_ids": [chunk.chunk_id for chunk in chunks],
                }
                indexed += 1
            chunk_count = int(
                connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            )

        self._write_manifest(manifest)
        return SyncStats(
            indexed_files=indexed,
            skipped_files=skipped,
            deleted_sources=len(deleted),
            chunk_count=chunk_count,
        )

    def query(self, query: str, *, top_k: int = 5) -> list[RetrievalHit]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        manifest = self._load_manifest()
        stored_dimensions = manifest.get("dimensions")
        query_vector, _ = self._encode_checked([query], stored_dimensions)
        query_vector = query_vector[0]
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM chunks").fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vector = json.loads(row["embedding"])
            if stored_dimensions is not None and len(vector) != len(query_vector):
                raise ValueError(
                    f"stored vector for {row['chunk_id']} has {len(vector)} dimensions, "
                    f"query vector has {len(query_vector)}; rebuild the index"
                )
            score = sum(a * b for a, b in zip(query_vector, vector))
            scored.append((float(score), row))
        scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        return [
            RetrievalHit(
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                title=row["title"],
                relative_path=row["relative_path"],
                locator=row["locator"],
                text=row["text"],
                score=score,
                access=row["access"],
                content_hash=row["content_hash"],
            )
            for score, row in scored[:top_k]
        ]