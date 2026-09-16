"""代码围栏感知的守门测试。

之前切分器不认 ``` 围栏，于是：
- 围栏行本身成了独立小块（` ```bash `）；
- 围栏里的命令被切成孤立小块（`git pull origin main`）；
- 代码块里以 # 开头的注释被当成 Markdown 标题，污染 locator。

真机语料（hermes-docs-full.txt）里 547 个 <50 字符的块基本全来自这一条。
"""

from private_rag.chunker import chunk_document
from private_rag.schema import SourceDocument


def make(text: str, title: str = "文档") -> SourceDocument:
    return SourceDocument(
        source_id="src_test", title=title, relative_path="doc.md", text=text
    )


def test_hash_comment_inside_code_fence_is_not_a_heading() -> None:
    document = make(
        "# 安装\n\n```bash\n# 先装依赖\nuv pip install -e .\n```\n\n正文结束。"
    )

    chunks = chunk_document(document)

    assert len(chunks) == 1, [c.locator for c in chunks]
    assert chunks[0].locator == "安装"
    assert "# 先装依赖" in chunks[0].text


def test_code_fence_is_not_split_into_noise_chunks() -> None:
    document = make(
        "#### Pull latest code\n\n```bash\ngit pull origin main\n```\n\n#### Reinstall\n\n"
        "```bash\nuv pip install -e .\n```"
    )

    chunks = chunk_document(document)

    assert all(len(chunk.text) >= 20 for chunk in chunks), [
        (c.locator, c.text) for c in chunks
    ]
    assert not any(chunk.text.strip() in {"```", "```bash"} for chunk in chunks)


def test_command_and_its_fence_stay_together() -> None:
    document = make("# 升级\n\n```bash\nnix flake update hermes-agent\n```")

    chunks = chunk_document(document)

    body = "\n".join(chunk.text for chunk in chunks)
    assert "```bash" in body
    assert "nix flake update hermes-agent" in body
    assert len(chunks) == 1


def test_real_headings_after_a_fence_still_split() -> None:
    document = make(
        "# A\n\n```sh\necho hi\n```\n\n# B\n\n后面的内容。"
    )

    chunks = chunk_document(document)

    assert sorted(chunk.locator for chunk in chunks) == ["A", "B"]


def test_tilde_fences_are_also_recognised() -> None:
    document = make("# A\n\n~~~python\n# 注释\nx = 1\n~~~\n\n正文。")

    chunks = chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].locator == "A"


def test_content_is_preserved_byte_for_byte_including_fences() -> None:
    body = "```bash\n# 注释\ngit pull origin main\n```"
    document = make(f"# A\n\n{body}")

    chunks = chunk_document(document)

    assert body in chunks[0].text
