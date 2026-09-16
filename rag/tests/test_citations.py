import json
from pathlib import Path

import pytest

from private_rag.citations import add_private_hits, add_public_source, verify_citations
from private_rag.schema import RetrievalHit


def _write_ledger(path: Path, source_path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "I01",
                        "kind": "private",
                        "title": "内部说明",
                        "uri": str(source_path),
                        "locator": "安装",
                        "evidence": "父卡完成后子卡自动解锁",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_verify_accepts_known_citation_with_exact_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 安装\n父卡完成后子卡自动解锁。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "父卡完成后子卡自动解锁。[I01]\n\n## Sources\n\n- [I01] 内部说明\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is True
    assert report.issues == ()


def test_verify_rejects_unknown_id_and_uncited_number(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("稳定文本", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "Hermes v0.21 引入了该能力。[I99]\n处理速度提升 30%。\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("未知引用 I99" in issue for issue in report.issues)
    assert any("量化断言缺少引用" in issue for issue in report.issues)


def test_verify_rejects_broken_ledger_schema(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("父卡完成后子卡自动解锁。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    ledger.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "id": "I01",
                        "kind": "private",
                        "uri": str(source),
                        "evidence": "父卡完成后子卡自动解锁",
                    },
                    {
                        "id": "I01",
                        "kind": "public",
                        "uri": "https://example.com",
                        "evidence": "父卡完成后子卡自动解锁",
                    },
                    {
                        "id": "W02",
                        "kind": "private",
                        "uri": str(source),
                        "evidence": "父卡完成后子卡自动解锁",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    draft = tmp_path / "draft.md"
    draft.write_text("父卡完成后子卡自动解锁。[I01]", encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("重复" in issue for issue in report.issues)
    assert any("kind" in issue for issue in report.issues)


def test_verify_rejects_cited_draft_without_sources_section(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("父卡完成后子卡自动解锁。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text("父卡完成后子卡自动解锁。[I01]", encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("缺少 ## Sources" in issue for issue in report.issues)


def test_verify_rejects_source_list_without_inline_citation(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("父卡完成后子卡自动解锁。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(
        "父卡完成后子卡自动解锁。\n\n## Sources\n\n- [I01] 内部说明\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert report.cited_ids == ()
    assert any("未在正文引用" in issue for issue in report.issues)


@pytest.mark.parametrize(
    "claim",
    [
        "并发上限是 20 workers。",
        "该接口支持 4 threads。",
        "响应耗时约 350 ms。",
        "价格从 ¥99 起。",
        "吞吐提升 3 倍。",
        "默认预算 8192 tokens。",
    ],
)
def test_verify_rejects_uncited_quantitative_claim(tmp_path: Path, claim: str) -> None:
    source = tmp_path / "source.md"
    source.write_text("稳定文本", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text(claim, encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("量化断言缺少引用" in issue for issue in report.issues)


def test_verify_rejects_draft_without_prose(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("稳定文本", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text("```\ncode only\n```\n", encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("正文为空" in issue for issue in report.issues)


def test_verify_rejects_evidence_not_found_in_source(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("真实原文", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    _write_ledger(ledger, source)
    draft = tmp_path / "draft.md"
    draft.write_text("父卡完成后子卡自动解锁。[I01]", encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("证据原文不存在" in issue for issue in report.issues)


def test_add_private_hits_is_idempotent_and_uses_absolute_source_path(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    source = source_root / "guide.md"
    source.write_text("父卡完成后子卡自动解锁。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    hit = RetrievalHit(
        chunk_id="chk_demo",
        source_id="src_demo",
        title="内部说明",
        relative_path="guide.md",
        locator="调度",
        text="父卡完成后子卡自动解锁。",
        score=0.9,
    )

    first = add_private_hits(ledger, source_root, [hit])
    second = add_private_hits(ledger, source_root, [hit])
    stored = json.loads(ledger.read_text(encoding="utf-8"))

    assert first == ["I01"]
    assert second == ["I01"]
    assert len(stored["sources"]) == 1
    assert stored["sources"][0]["uri"] == str(source.resolve())


def test_private_pdf_hit_uses_run_snapshot_for_verification(tmp_path: Path) -> None:
    source_root = tmp_path / "documents"
    source_root.mkdir()
    pdf = source_root / "manual.pdf"
    pdf.write_bytes(b"%PDF-binary-placeholder")
    run = tmp_path / "run"
    ledger = run / "citations.json"
    hit = RetrievalHit(
        chunk_id="chk_pdf",
        source_id="src_pdf",
        title="PDF 手册",
        relative_path="manual.pdf",
        locator="Page 2",
        text="从 PDF 提取出的逐字证据",
        score=0.8,
    )
    add_private_hits(ledger, source_root, [hit])
    draft = run / "draft.md"
    draft.write_text(
        "从 PDF 提取出的逐字证据。[I01]\n\n## Sources\n\n- [I01] PDF 手册\n",
        encoding="utf-8",
    )

    report = verify_citations(draft, ledger)

    assert report.ok is True


def test_add_public_source_requires_exact_evidence_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "official.md"
    snapshot.write_text("官方说明：父卡完成后子卡进入 ready。", encoding="utf-8")
    ledger = tmp_path / "citations.json"

    first = add_public_source(
        ledger,
        title="官方文档",
        url="https://example.com/docs",
        evidence="父卡完成后子卡进入 ready",
        snapshot_path=snapshot,
        locator="调度章节",
    )
    second = add_public_source(
        ledger,
        title="官方文档",
        url="https://example.com/docs",
        evidence="父卡完成后子卡进入 ready",
        snapshot_path=snapshot,
        locator="调度章节",
    )

    assert first == "W01"
    assert second == "W01"
    with pytest.raises(ValueError, match="evidence"):
        add_public_source(
            ledger,
            title="错误证据",
            url="https://example.com/bad",
            evidence="页面里没有这句话",
            snapshot_path=snapshot,
        )


def test_verify_rechecks_public_evidence_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "official.md"
    snapshot.write_text("官方原文保存在这里。", encoding="utf-8")
    ledger = tmp_path / "citations.json"
    add_public_source(
        ledger,
        title="官方文档",
        url="https://example.com/docs",
        evidence="官方原文保存在这里",
        snapshot_path=snapshot,
    )
    draft = tmp_path / "draft.md"
    draft.write_text("官方原文保存在这里。[W01]", encoding="utf-8")
    snapshot.write_text("快照后来被错误覆盖。", encoding="utf-8")

    report = verify_citations(draft, ledger)

    assert report.ok is False
    assert any("公开来源快照" in issue for issue in report.issues)
