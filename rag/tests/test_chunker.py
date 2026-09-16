from private_rag.chunker import chunk_document
from private_rag.schema import SourceDocument


def test_markdown_chunks_keep_heading_locator_and_stable_ids() -> None:
    document = SourceDocument(
        source_id="src_demo",
        title="演示文档",
        relative_path="guide.md",
        text="# 安装\n\n第一段说明。\n\n## Windows\n\n第二段说明。",
    )

    first = chunk_document(document, max_chars=40, overlap_chars=8)
    second = chunk_document(document, max_chars=40, overlap_chars=8)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert any(chunk.locator == "安装 > Windows" for chunk in first)
    assert all(chunk.source_id == "src_demo" for chunk in first)
    assert all(len(chunk.text) <= 40 for chunk in first)
