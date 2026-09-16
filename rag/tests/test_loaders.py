from pathlib import Path

import private_rag.loaders as loaders
import pytest
from private_rag.loaders import load_documents


def test_load_markdown_preserves_title_and_stable_source_id(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    path = source / "guide.md"
    path.write_text(
        "---\ntitle: Hermes 指南\nupdated_at: 2026-09-16\n---\n\n# 安装\n\n使用 CLI 安装。\n",
        encoding="utf-8",
    )

    first = load_documents(source)
    second = load_documents(source)

    assert len(first) == 1
    assert first[0].title == "Hermes 指南"
    assert first[0].relative_path == "guide.md"
    assert first[0].source_id == second[0].source_id
    assert first[0].source_id.startswith("src_")
    assert "使用 CLI 安装" in first[0].text
    assert first[0].metadata["updated_at"] == "2026-09-16"


def test_load_pdf_preserves_page_boundaries(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    pdf = source / "manual.pdf"
    pdf.write_bytes(b"fake-pdf")

    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        pages = [Page("第一页内容"), Page("第二页内容")]

    monkeypatch.setattr(loaders, "PdfReader", lambda _: Reader())

    documents = load_documents(source)

    assert len(documents) == 1
    assert "# Page 1\n\n第一页内容" in documents[0].text
    assert "# Page 2\n\n第二页内容" in documents[0].text
    assert documents[0].metadata["page_count"] == "2"


def test_missing_source_directory_fails_instead_of_looking_empty(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "missing")


def test_loader_rejects_candidate_resolving_outside_knowledge_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "documents"
    root.mkdir()
    outside = tmp_path / "secret.md"
    outside.write_text("不应被索引", encoding="utf-8")
    original_rglob = Path.rglob

    def fake_rglob(path: Path, pattern: str):
        if path == root.resolve():
            return iter([outside])
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", fake_rglob)

    with pytest.raises(ValueError, match="escapes knowledge root"):
        load_documents(root)
