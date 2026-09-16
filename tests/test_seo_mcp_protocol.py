"""MCP 协议层测试：真起子进程、走 stdio、按 JSON-RPC 对话。

为什么不能只测函数：MCP server 有一整类只会在**协议层**爆炸的故障，
函数级测试一个都抓不到 ——

- handshake / capability 协商不对 → 客户端连不上；
- **stdout 被污染**（server 里一句漏掉的 `print()`）→ 帧被破坏、客户端报解析错误。
  这是 stdio 传输最经典的坑，而它在"直接 import 函数"的测试里完全不可见；
- tool schema 与实现不一致 → 客户端按 schema 传参、server 收到对不上的东西。

所以这里起真进程、走真 stdio。`is_error` / `isError` 的命名差异见下。
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

mcp_sdk = pytest.importorskip("mcp", reason="需要 mcp 包才能做协议测试")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "seo-checker" / "__init__.py"
MCP_SERVER = ROOT / "mcp" / "seo-checker" / "server.py"

SAMPLE = {
    "title": "RAG 检索质量评测：把「感觉还行」变成可复现的证据",
    "description": "用 30 题评测集把检索质量从感觉量成数字：Hit@1 从 0.111 到 0.481，含逐题翻转与边界说明。",
    "content": "# RAG 检索质量评测\n\n## 为什么需要评测\n正文若干。\n\n## 方法\n更多正文。",
    "tags": ["RAG", "评测"],
    "slug": "rag-retrieval-evaluation",
}


def _is_error(result) -> bool:
    """mcp 2.0 用 is_error，旧版用 isError。"""
    value = getattr(result, "is_error", None)
    return bool(getattr(result, "isError", False) if value is None else value)


async def _talk(call_args: dict | None = None):
    """起 server → 握手 → 列工具 →（可选）调一次，返回观测到的结果。"""
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            result = None
            if call_args is not None:
                result = await session.call_tool("check_seo", call_args)
            return init, tools, result


def test_handshake_and_tool_discovery() -> None:
    """客户端能连上、并发现 check_seo 这个工具。"""
    init, tools, _ = asyncio.run(_talk())

    assert init.server_info.name == "seo-checker"
    assert [t.name for t in tools.tools] == ["check_seo"]


def _input_schema(tool) -> dict:
    """mcp 2.0 用 input_schema，旧版用 inputSchema。"""
    schema = getattr(tool, "input_schema", None)
    return schema if schema is not None else tool.inputSchema


def test_tool_schema_matches_plugin_contract() -> None:
    """schema 必须与插件里那份一致：title 必填，其余可选。"""
    _, tools, _ = asyncio.run(_talk())
    schema = _input_schema(tools.tools[0])

    assert schema.get("type") == "object"
    props = schema.get("properties", {})
    for field in ("title", "description", "content", "keyword", "tags", "slug"):
        assert field in props, f"schema 缺少 {field}"
    assert schema.get("required") == ["title"]


def test_real_call_returns_real_data() -> None:
    """真调一次，拿回可解析的 JSON 且字段齐全（不是空壳）。"""
    _, _, result = asyncio.run(_talk(SAMPLE))

    assert result is not None
    assert _is_error(result) is False
    text = result.content[0].text
    parsed = json.loads(text)
    for key in ("ok", "score", "language", "issues", "warnings", "metrics"):
        assert key in parsed, f"返回缺少 {key}"
    assert parsed["language"] == "zh"
    assert isinstance(parsed["score"], int)


def test_missing_required_field_is_rejected_by_schema() -> None:
    """缺 title（schema 里 required）时，协议层应报错而不是静默返回垃圾。"""
    _, _, result = asyncio.run(_talk({"description": "没有标题"}))

    assert result is not None
    assert _is_error(result) is True


def test_server_delegates_instead_of_reimplementing() -> None:
    """MCP server 只应是适配器：它必须委托给核心实现，且不含检查逻辑本身。

    判据用**只可能出现在实现里**的东西：插件是正则驱动的，所以 `import re` 与评分
    公式是"复制了逻辑"的强信号。工具描述里出现 checks_skipped 之类字段名是合法的
    （那是抄给客户端看的契约说明），不作为判据。
    """
    source = MCP_SERVER.read_text(encoding="utf-8")

    assert "handle_check_seo" in source, "必须委托给插件的 handle_check_seo"
    assert "SEO_CHECKER_PLUGIN" in source, "应支持用环境变量覆盖核心路径"

    for logic_marker in ("import re", "thresholds_used", "keyword_density_pct", "def _detect_lang"):
        assert logic_marker not in source, (
            f"MCP server 里出现了实现细节 {logic_marker!r}，疑似复制了检查逻辑"
        )


def test_plugin_source_is_absent_of_stdout_writes() -> None:
    """stdio 传输下 stdout 是协议通道：插件实现里不能有任何 print/写 stdout。"""
    text = PLUGIN.read_text(encoding="utf-8")

    assert "print(" not in text, "插件里有 print()，会污染 stdio 协议帧"
    assert "sys.stdout" not in text
