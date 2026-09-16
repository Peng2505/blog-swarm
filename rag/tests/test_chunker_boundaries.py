"""切分改进的守门测试：块必须在自然边界断开，且不产生碎块。"""

from private_rag.chunker import chunk_document
from private_rag.schema import SourceDocument


def make(text: str, title: str = "文档") -> SourceDocument:
    return SourceDocument(
        source_id="src_test",
        title=title,
        relative_path="doc.md",
        text=text,
    )


def test_short_section_stays_a_single_chunk() -> None:
    document = make("# 一\n\n短正文。")

    chunks = chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].text == "短正文。"


def test_no_chunk_exceeds_max_chars() -> None:
    long_sentence = "这是一个很长的句子，用来把段落撑到超过上限。" * 30
    document = make(f"# 一\n\n{long_sentence}")

    chunks = chunk_document(document, max_chars=300, overlap_chars=40, min_chars=50)

    assert len(chunks) > 1
    # 最后一块允许超出至多 min_chars：那是"碎尾巴并入前一块"的代价，
    # 比丢字可接受。其余块必须严格不超限。
    assert all(len(chunk.text) <= 300 + 50 for chunk in chunks)
    assert all(len(chunk.text) <= 300 for chunk in chunks[:-1])


def test_chunks_break_at_sentence_endings_not_mid_sentence() -> None:
    text = "".join(f"第{i}句结束。" for i in range(1, 41))
    document = make(f"# 一\n\n{text}")

    chunks = chunk_document(document, max_chars=120, overlap_chars=0, min_chars=20)

    # 除最后一块外，每块都应以句末标点收尾，而不是被切成半句
    for chunk in chunks[:-1]:
        assert chunk.text.endswith("。"), f"块未在句末断开: ...{chunk.text[-25:]!r}"


def test_tiny_trailing_fragment_is_not_emitted() -> None:
    """硬切会留下几个字符的尾巴，那正是之前 8 个碎块的来源。"""
    text = "甲乙丙丁戊己庚辛壬癸。" * 25 + "尾巴"
    document = make(f"# 一\n\n{text}")

    chunks = chunk_document(document, max_chars=100, overlap_chars=20, min_chars=40)

    assert all(len(chunk.text) >= 40 for chunk in chunks), [
        len(chunk.text) for chunk in chunks
    ]


def test_adjacent_chunks_still_overlap() -> None:
    text = "".join(f"句子{i}。" for i in range(1, 61))
    document = make(f"# 一\n\n{text}")

    chunks = chunk_document(document, max_chars=100, overlap_chars=30, min_chars=20)

    assert len(chunks) >= 3
    # 前一块的结尾应出现在后一块里（重叠生效）
    tail = chunks[0].text[-10:]
    assert tail in chunks[1].text


def test_pdf_style_page_sections_keep_their_locator() -> None:
    document = make("# Page 1\n\n第一页内容。\n\n# Page 2\n\n第二页内容。")

    chunks = chunk_document(document)

    assert [chunk.locator for chunk in chunks] == ["Page 1", "Page 2"]


def test_all_content_is_preserved_across_chunks() -> None:
    text = "".join(f"要点{i}。" for i in range(1, 51))
    document = make(f"# 一\n\n{text}")

    chunks = chunk_document(document, max_chars=80, overlap_chars=0, min_chars=10)

    joined = "".join(chunk.text for chunk in chunks)
    assert all(f"要点{i}。" in joined for i in range(1, 51))
