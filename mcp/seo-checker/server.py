#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_seo 的 MCP server（stdio 传输）。

    python server.py            # 由 MCP 客户端（Hermes / Claude Desktop / Cursor…）拉起

## 为什么要有它

`check_seo` 原本只能被 Hermes 的插件系统调用（工具面 `blog_tools`）。把它按
Model Context Protocol 暴露之后，**任何 MCP 客户端**都能用同一个检查器 —— 不绑定 Hermes。

## 单一来源：这里不重写任何检查逻辑

本文件**不实现**任何 SEO 规则，它 `import` 的是插件里那份 `handle_check_seo`
（`plugins/seo-checker/__init__.py`）。所以：

    Hermes 插件 ──┐
                  ├──→ handle_check_seo（唯一实现）
    MCP server  ──┘

两条适配器共用同一份实现，`tests/test_seo_mcp_parity.py` 用奇偶校验锁死这一点
（同一输入必须逐字同输出），防止日后有人复制一份逻辑出来各自漂移。

## stdio 传输的一个硬约束

**stdout 是协议通道**。本文件（以及被它 import 的插件）绝不能 `print()` 到 stdout，
否则协议帧会被污染、客户端报解析错误。诊断信息一律走 stderr。
（`handle_check_seo` 实测无任何 stdout 输出。）

## 环境变量

- `SEO_CHECKER_PLUGIN`：指向 `check_seo` 实现文件的绝对路径。
  不设时默认用仓库内的副本 `plugins/seo-checker/__init__.py`（版本受控的那份）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

PLUGIN_ENV = "SEO_CHECKER_PLUGIN"
SERVER_NAME = "seo-checker"
SERVER_VERSION = "1.0.0"


def _plugin_path() -> Path:
    """定位 check_seo 的唯一实现。找不到就 fail loud（不要静默降级成空实现）。"""
    override = (os.environ.get(PLUGIN_ENV) or "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.is_file():
            raise SystemExit(f"[{SERVER_NAME}] {PLUGIN_ENV} 指向的文件不存在：{candidate}")
        return candidate

    # server.py 在 <repo>/mcp/seo-checker/server.py → parents[2] = <repo>
    repo_copy = Path(__file__).resolve().parents[2] / "plugins" / "seo-checker" / "__init__.py"
    if repo_copy.is_file():
        return repo_copy

    raise SystemExit(
        f"[{SERVER_NAME}] 找不到 check_seo 实现。"
        f"期望位置：{repo_copy}；或用环境变量 {PLUGIN_ENV} 指定。"
    )


def _load_core(path: Path):
    """按文件路径加载。

    不能写 `import seo_checker` —— 目录名带连字符，不是合法模块名；
    插件本身也是以 `__init__.py` 形式被 Hermes 按路径加载的，这里保持一致。
    """
    name = "seo_checker_core"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[{SERVER_NAME}] 无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：插件用 `from __future__ import annotations`，
    # dataclass/typing 解析字符串注解时要靠 sys.modules 找到自己。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "handle_check_seo"):
        raise SystemExit(f"[{SERVER_NAME}] {path} 里没有 handle_check_seo")
    return module


CORE_PATH = _plugin_path()
_CORE = _load_core(CORE_PATH)
print(f"[{SERVER_NAME}] 核心实现：{CORE_PATH}", file=sys.stderr, flush=True)

server = MCPServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    description="博客 SEO 元数据质量检查器（与 Hermes 插件共用同一份实现）。",
)


@server.tool(
    name="check_seo",
    description=(
        "Check SEO metadata quality for a blog post. Returns JSON with score (0-100), "
        "issues (must-fix), warnings (nice-to-have), checks_skipped and raw metrics. "
        "Chinese and English thresholds differ automatically."
    ),
)
def check_seo(
    title: str,
    description: str = "",
    content: str = "",
    keyword: str = "",
    tags: list[str] | None = None,
    slug: str = "",
) -> str:
    """检查一篇文章的 SEO 元数据质量，返回 JSON 字符串。

    Args:
        title: 文章标题（来自 frontmatter），必填。
        description: meta description（Hugo frontmatter `description`）。
        content: 正文，前 ~500 字符足够做关键词检查；给更多才能做结构检查（H1/H2、图片 alt）。
        keyword: 主关键词；省略时回退到 tags[0]。
        tags: frontmatter 的标签列表。
        slug: URL 文件名，不含 `.md`。
    """
    return _CORE.handle_check_seo(
        {
            "title": title,
            "description": description,
            "content": content,
            "keyword": keyword,
            "tags": tags or [],
            "slug": slug,
        }
    )


def main() -> None:
    """入口点（供 `python server.py` 与 pyproject 的 console script 共用）。"""
    server.run()  # 默认 stdio


if __name__ == "__main__":
    main()
