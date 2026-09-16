"""Regression tests for the defects found by the second independent review."""

import json
from pathlib import Path

import pytest

from private_rag.citations import verify_citations
from private_rag.embeddings import HashingEmbedding
from private_rag.store import KnowledgeBase


# --- citations: real drafts must not be false-flagged ----------------------


def _ledger(path: Path, source: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "I01",
                        "kind": "private",
                        "title": "内部资料",
                        "uri": str(source),
                        "locator": "调度",
                        "evidence": "父卡完成后子卡自动解锁",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_versions_section_numbers_and_plain_decimals_are_not_flagged(tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_text("父卡完成后子卡自动解锁", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "运行 Python 3.11 与 langgraph 1.2.11。\n"
        "详见 3.5 节。\n"
        "温度设为 0.2。\n"
        "父卡完成后子卡自动解锁。[I01]\n\n"
        "## Sources\n\n- [I01] 内部资料\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is True, report.issues


def test_reference_section_heading_variant_is_not_treated_as_prose(tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_text("父卡完成后子卡自动解锁", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "父卡完成后子卡自动解锁。[I01]\n\n"
        "## 说明与参考\n\n"
        "- Brown, Cai & DasGupta (2001) Statistical Science 16(2): 101-133.\n"
        "- [I01] 内部资料，命中率 0.81\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is True, report.issues


def test_citation_after_a_wrapped_line_still_covers_the_sentence(tmp_path: Path) -> None:
    source = tmp_path / "src.md"
    source.write_text("父卡完成后子卡自动解锁", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "处理速度提升 30%，\n这一结论来自本机实测。[I01]\n\n## Sources\n\n- [I01] 内部资料\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is True, report.issues


@pytest.mark.parametrize(
    "claim",
    [
        "响应耗时约 350 ms。",
        "价格从 ¥99 起。",
        "吞吐提升 3 倍。",
        "并发上限是 20 workers。",
        "支持 v0.21.0 及以上版本。",
    ],
)
def test_real_quantitative_claims_still_blocked_when_uncited(
    tmp_path: Path, claim: str
) -> None:
    source = tmp_path / "src.md"
    source.write_text("稳定文本", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(claim, encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("量化断言缺少引用" in issue for issue in report.issues)


# --- store: data-loss and crash paths -------------------------------------


def test_sync_refuses_to_wipe_index_when_source_dir_is_empty(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    source.mkdir()
    (source / "guide.md").write_text("# 调度\n\n父卡完成后解锁。", encoding="utf-8")
    db = tmp_path / "db"
    KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)
    (source / "guide.md").unlink()

    with pytest.raises(ValueError, match="refusing to wipe"):
        KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)


def test_sync_handles_deleting_a_source_that_produced_no_chunks(tmp_path: Path) -> None:
    source = tmp_path / "documents"
    source.mkdir()
    (source / "empty.md").write_text("", encoding="utf-8")
    (source / "guide.md").write_text("# 调度\n\n父卡完成后解锁。", encoding="utf-8")
    db = tmp_path / "db"
    kb = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128))
    kb.sync(source)
    (source / "empty.md").unlink()

    stats = KnowledgeBase(db, embedding=HashingEmbedding(dimensions=128)).sync(source)

    assert stats.deleted_sources == 1


def test_dimensionless_embedding_with_no_chunks_does_not_lock_the_index(
    tmp_path: Path,
) -> None:
    class NoDimensions:
        @property
        def name(self) -> str:
            return "no-dims"

        def encode(self, texts):
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    source = tmp_path / "documents"
    source.mkdir()
    (source / "empty.md").write_text("", encoding="utf-8")
    db = tmp_path / "db"
    KnowledgeBase(db, embedding=NoDimensions()).sync(source)
    (source / "empty.md").unlink()
    (source / "guide.md").write_text("# 调度\n\n父卡完成后解锁。", encoding="utf-8")

    stats = KnowledgeBase(db, embedding=NoDimensions()).sync(source)

    assert stats.chunk_count == 1
    assert KnowledgeBase(db, embedding=NoDimensions()).query("调度", top_k=1)
