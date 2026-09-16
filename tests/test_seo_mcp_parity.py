"""奇偶校验：MCP 适配器与 Hermes 插件适配器必须逐字同输出。

这是本次改造最重要的一条测试。

MCP server 刻意**不重新实现**任何 SEO 规则，它 import 的是插件里那份
`handle_check_seo`。于是同一份实现有两个适配器：

    Hermes 插件（工具面 blog_tools） ──┐
                                        ├──→ handle_check_seo（唯一实现）
    MCP server（stdio）               ──┘

风险在于日后有人"顺手"把逻辑复制一份到 MCP 那边、或改了一边忘了另一边 ——
两条路径就开始各自漂移，而**两条路径分别看都很正常**，没人会发现。
本文件用一个输入集合把两个适配器的输出逐字节钉在一起。

另外还锁两件事：
- MCP server 加载的核心必须来自仓库内那份插件源码（不是另一份拷贝）；
- 仓库内的插件源码与已部署到 profile 的副本一致（这份漂移我在 private-rag 上真踩过：
  doctor 一路报 OK，实际副本停在旧版本）。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "seo-checker" / "__init__.py"
MCP_SERVER = ROOT / "mcp" / "seo-checker" / "server.py"

# 覆盖：空标题（原版 IndexError 那例）、纯中文、纯英文、缺省字段、空 tags、
# 显式 keyword、正文含代码围栏（切分/结构检查的边界）。
CASES = [
    {"title": "", "description": "", "content": "内容非空，用于触发原版的 IndexError。"},
    {
        "title": "RAG 检索质量评测：把「感觉还行」变成可复现的证据",
        "description": "用 30 题评测集把检索质量从感觉量成数字：Hit@1 从 0.111 到 0.481，含逐题翻转与边界说明。",
        "content": "# RAG 检索质量评测\n\n## 为什么需要评测\n正文内容若干。\n\n## 方法\n更多正文。",
        "tags": ["RAG", "评测"],
        "slug": "rag-retrieval-evaluation",
    },
    {
        "title": "Why Retrieval Evaluation Beats Vibes",
        "description": "A 30-question eval set turns retrieval quality from a feeling into a number, with flips and bounds shown.",
        "content": "# Heading\n\n## Section\nBody text here.\n\n## Another\nMore body.",
        "tags": ["rag", "eval", "retrieval"],
        "keyword": "evaluation",
        "slug": "why-retrieval-evaluation",
    },
    {"title": "只有标题"},
    {"title": "空 tags 用例", "tags": []},
    {
        "title": "含代码围栏的正文",
        "content": "# T\n\n## Code\n\n```bash\nhermes mcp list   # 这行 '#' 不应被当成标题\n```\n\n## After\n尾巴。",
        "tags": ["a"],
    },
]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    # `from __future__ import annotations` 下，dataclass/typing 解析字符串注解要靠
    # sys.modules 找到自己；不注册会报 'NoneType' object has no attribute '__dict__'。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin():
    return _load("seo_plugin_for_parity", PLUGIN)


@pytest.fixture(scope="module")
def mcp_server():
    return _load("seo_mcp_for_parity", MCP_SERVER)


def test_mcp_loads_the_repo_plugin(mcp_server) -> None:
    """MCP server 用的核心必须就是仓库里这份插件源码。"""
    assert Path(mcp_server.CORE_PATH).resolve() == PLUGIN.resolve()


@pytest.mark.parametrize("case", CASES, ids=lambda c: (c.get("title") or "(empty)")[:24])
def test_plugin_and_mcp_return_identical_output(plugin, mcp_server, case) -> None:
    """同一输入 → 两个适配器输出必须逐字节相同。"""
    direct = plugin.handle_check_seo(dict(case))
    via_mcp = mcp_server.check_seo(
        title=case.get("title", ""),
        description=case.get("description", ""),
        content=case.get("content", ""),
        keyword=case.get("keyword", ""),
        tags=case.get("tags"),
        slug=case.get("slug", ""),
    )

    assert via_mcp == direct, "MCP 路径与插件路径输出不一致（两条适配器已漂移）"


def test_both_adapters_return_parsable_json(plugin, mcp_server) -> None:
    """两边都必须返回可解析 JSON —— 不允许一边返回裸文本。"""
    case = CASES[1]
    for payload in (plugin.handle_check_seo(dict(case)), mcp_server.check_seo(**{
        "title": case["title"],
        "description": case["description"],
        "content": case["content"],
        "tags": case["tags"],
        "slug": case["slug"],
    })):
        parsed = json.loads(payload)
        assert "score" in parsed and "issues" in parsed


def test_missing_tags_does_not_diverge(plugin, mcp_server) -> None:
    """省略 tags 时 MCP 传 []、插件拿到 None —— 结果仍须一致。"""
    title = "省略 tags 的用例"

    assert mcp_server.check_seo(title=title) == plugin.handle_check_seo({"title": title})


def test_deployed_profile_copy_matches_repo(plugin) -> None:
    """仓库副本与已部署到 profile 的副本必须一致（否则插件与 MCP 跑的是两份代码）。"""
    profiles_root = Path.home() / "AppData" / "Local" / "hermes" / "profiles"
    if not profiles_root.is_dir():
        pytest.skip("本机没有 Hermes profile 目录")

    deployed = [
        profiles_root / name / "plugins" / "seo-checker" / "__init__.py"
        for name in ("reviewer", "researcher", "writer")
    ]
    present = [p for p in deployed if p.is_file()]
    if not present:
        pytest.skip("seo-checker 未部署到任何 profile")

    source = PLUGIN.read_text(encoding="utf-8")
    for path in present:
        assert path.read_text(encoding="utf-8") == source, (
            f"{path} 与仓库副本不一致 —— 部署副本已漂移"
        )
