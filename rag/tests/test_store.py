from pathlib import Path

import pytest

from private_rag.embeddings import HashingEmbedding
from private_rag.store import KnowledgeBase


@pytest.mark.parametrize("name", ["../outside", "..\\outside", "D:/outside", "a/b", ""])
def test_collection_name_must_be_a_safe_component(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="collection_name"):
        KnowledgeBase(tmp_path / "db", embedding=HashingEmbedding(), collection_name=name)


def test_sync_is_incremental_and_query_returns_provenance(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    source.mkdir()
    (source / "kanban.md").write_text(
        "# 调度\n\nHermes Kanban 使用父子卡串联工作流，父卡完成后子卡解锁。",
        encoding="utf-8",
    )
    (source / "vector.md").write_text(
        "# 向量库\n\nChroma 保存文本向量和元数据。",
        encoding="utf-8",
    )
    kb = KnowledgeBase(tmp_path / "db", embedding=HashingEmbedding(dimensions=128))

    first = kb.sync(source)
    second = kb.sync(source)
    hits = kb.query("Kanban 父子卡如何解锁", top_k=2)

    assert first.indexed_files == 2
    assert second.indexed_files == 0
    assert second.skipped_files == 2
    assert hits[0].relative_path == "kanban.md"
    assert hits[0].locator == "调度"
    assert hits[0].chunk_id.startswith("chk_")
    assert "父卡完成后子卡解锁" in hits[0].text

    (source / "vector.md").unlink()
    third = kb.sync(source)
    assert third.deleted_sources == 1
    assert all(hit.relative_path != "vector.md" for hit in kb.query("Chroma", top_k=5))


def _seed(source: Path) -> None:
    source.mkdir(exist_ok=True)
    (source / "guide.md").write_text(
        "# 调度\n\n父卡完成后子卡解锁。", encoding="utf-8"
    )


def test_sync_repairs_manifest_when_sqlite_chunks_are_lost(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    _seed(source)
    db = tmp_path / "db"
    kb = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128))
    kb.sync(source)
    for sqlite_file in db.glob("*.sqlite3"):
        sqlite_file.unlink()

    repaired = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)

    assert repaired.chunk_count > 0
    assert (repaired.indexed_files, repaired.skipped_files) == (1, 0)


def test_sync_drops_orphan_chunks_when_manifest_is_lost(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    _seed(source)
    db = tmp_path / "db"
    kb = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128))
    kb.sync(source)
    (db / "manifest.json").unlink()

    resynced = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)

    assert resynced.indexed_files == 1
    assert resynced.chunk_count == 1


class _ShortEmbedding(HashingEmbedding):
    def encode(self, texts):
        return super().encode(texts)[: max(0, len(texts) - 1)]


def test_sync_fails_closed_when_embedding_returns_too_few_vectors(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    _seed(source)
    (source / "extra.md").write_text("# 第二篇\n\n另一段正文。", encoding="utf-8")
    kb = KnowledgeBase(tmp_path / "db", embedding=_ShortEmbedding(dimensions=128))

    with pytest.raises(ValueError, match="embedding returned"):
        kb.sync(source)


def test_sync_fails_closed_when_embedding_model_changes(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    _seed(source)
    db = tmp_path / "db"
    KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)

    with pytest.raises(ValueError, match="rebuild the index"):
        KnowledgeBase(db, embedding=HashingEmbedding(dimensions=64)).query("调度")


def test_query_fails_closed_on_stored_dimension_mismatch(tmp_path: Path) -> None:
    import json

    source = tmp_path / "documents"
    _seed(source)
    db = tmp_path / "db"
    KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)
    manifest_path = db / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dimensions"] = 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dimensions"):
        KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).query("调度")
