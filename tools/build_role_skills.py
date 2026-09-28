#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 blog_swarm.py 的卡正文模板生成「角色契约」Skill。

单一事实来源 = blog_swarm.py 里的 ORCH_BODY / RESEARCH_BODY / WRITER_BODY /
REVIEW_BODY / PUBLISH_BODY。本脚本把每个角色的
「触发条件 / I/O 契约 / 权限与边界 / 模板正文 / 验收」渲染成符合 Hermes
SKILL.md 约定的文档，写到 skills/<name>/SKILL.md，并附机器可读的
references/io-contract.json。

为什么不手抄：手抄的副本一定会漂移，而两条路径「分别看都很正常」——
这和 tests/test_seo_mcp_parity.py 防的是同一类腐烂。

用法:
    python tools/build_role_skills.py             # 生成/更新
    python tools/build_role_skills.py --check     # 只校验，不一致 exit 2
    python tools/build_role_skills.py --install   # 生成后拷进各 worker profile

构建期校验（任一不过就抛错、不出文件）:
  1. 每个 *_BODY 常量存在且非空；
  2. 模板里必须出现 `第 N/5 段`，与声明的 stage 序号一致；
  3. 模板里必须出现该角色的英文名（如 `researcher`）；
  4. 声明的每个输出文件名必须真的出现在模板正文里 —— 这条是 I/O 契约
     不变成空话的关键：改了模板却没改契约，构建直接失败。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SWARM = PROJECT / "blog_swarm.py"
OUT_ROOT = PROJECT / "skills"
CATEGORY = "autonomous-ai-agents"

# ---------------------------------------------------------------- 角色声明
# 这里的 inputs/outputs/tools/forbidden 是**契约**，不是注释；outputs 会被
# 逐一回到模板正文里核对（见 validate()）。

ROLES = [
    dict(
        name="blog-research-contract",
        stage=2,
        assignee="researcher",
        body_const="RESEARCH_BODY",
        skill_title="博客调研契约（researcher · 2/5 段）",
        description="Use when researching a blog topic for the pipeline.",
        intro=(
            "把博客流水线第 2/5 段的调研交付标准固化为可复用契约：取证顺序、"
            "可信度分档、公开来源快照规则，以及引用编号（Ixx 私有 / Wxx 公开）"
            "的产生方式。新内容类型接入时只需换卡正文里的场景描述，这段口径不用重写。"
        ),
        triggers=[
            "要把一个选题做成「可核实的素材包」（结论 → 依据 → 来源）",
            "需要用私有知识库 + 公开网页两条来源取数，并保留逐字证据",
            "需要产出供下游写作/审校使用的引用账本 citations.json",
            "Don't use for: 写正文（那是 blog-writing-contract）、审校（blog-review-contract）",
        ],
        inputs=[
            "父卡片 handoff：上游 `01-outline.md` 的「待调研问题清单」",
            "本 run 的 workspace 目录（`HERMES_KANBAN_WORKSPACE`，由卡下发）",
            "本机私有知识库索引（默认语义库 `index-bge-m3`）",
        ],
        outputs=[
            "02-research.md",
            "citations.json",
            "evidence/web-*.md",
        ],
        output_notes={
            "02-research.md": "逐条回答待调研问题；可引用事实清单 3–6 条；三档可信度标注",
            "citations.json": "稳定引用编号 + 逐字证据；私有 Ixx / 公开 Wxx",
            "evidence/web-*.md": "网页**原文快照**，禁止只引用搜索摘要",
        },
        tools=["retrieve_private_knowledge", "record_public_source"],
        forbidden=[
            "**绝对不许编造**：找不到来源就写「未找到可靠来源」，不要写貌似合理的数字",
            "不得写正文（正文是第 3 段的事），只交素材",
            "不得复制大段原文 —— 用要点概括并保留链接",
            "私有来源（Ixx）不得包装成公开事实",
            "跨 run 读写会被拒：文件访问收口在本卡 workspace",
        ],
        accept=[
            "`02-research.md` 存在，且每条待调研问题都有「结论 → 依据 → 来源」",
            "每条断言都能指到来源，或明确标为「未找到可靠来源」",
            "`citations.json` 中每个 Ixx 都绑定了知识库 chunk、每个 Wxx 都有落盘快照",
            "summary 写明：文件绝对路径 + 已核实条数 / 未核实条数 + 关键来源",
        ],
    ),
    dict(
        name="blog-writing-contract",
        stage=3,
        assignee="writer",
        body_const="WRITER_BODY",
        skill_title="博客写作风格指南（writer · 3/5 段）",
        description="Use when writing the pipeline draft: structure and rules.",
        intro=(
            "把博客流水线第 3/5 段的写作标准固化为可复用风格指南：结构递进、"
            "字数硬门槛、引用格式、事实来源限制。换内容类型（如短图文）时只改"
            "篇幅与结构要求，引用/事实这套规则直接复用。"
        ),
        triggers=[
            "要按已核实的素材写一篇技术正文（素材已由 2/5 段产出）",
            "需要确保字数、引用格式、frontmatter 符合发布要求",
            "Don't use for: 调研取数（blog-research-contract）、审校改稿（blog-review-contract）",
        ],
        inputs=[
            "`01-outline.md`（结构）、`02-research.md`（**只能用作事实来源**）、`citations.json`（引用编号）",
            "父卡片 handoff",
        ],
        outputs=["03-draft.md"],
        output_notes={
            "03-draft.md": "中文 Markdown 技术博客；frontmatter 固定 title/date/tags/author 四项；正文汉字 ≥2000（实测）",
        },
        tools=["（本段不需要私有库/门禁工具，只读素材 + 写文件）"],
        forbidden=[
            "事实只能用 `02-research.md` 里标为 `已核实` 的内容；`未核实`/`推断` 要么删、要么在文中显式声明",
            "**禁止自行编号**：引用编号必须来自 `citations.json`",
            "不许留 TODO、占位段落、大段空话凑字数",
            "不许写「约 2000 字」蒙混 —— 必须跑统计命令拿实测数字",
            "`## Sources` 里 Ixx 只写可公开的文档标题，**不暴露本机绝对路径**",
        ],
        accept=[
            "`03-draft.md` 存在，frontmatter 四项齐全",
            "正文汉字数 **实测** ≥2000（命令见模板正文），数字写进 summary",
            "所有外部事实句末带内联引用；`## Sources` 与正文引用编号一致",
            "summary 写明：草稿绝对路径 + 实测汉字数 + 引用来源条数",
        ],
    ),
    dict(
        name="blog-review-contract",
        stage=4,
        assignee="reviewer",
        body_const="REVIEW_BODY",
        skill_title="博客审校检查清单（reviewer · 4/5 段）",
        description="Use when reviewing the pipeline draft before publish.",
        intro=(
            "把博客流水线第 4/5 段的审校标准固化为可复用检查清单：语言与事实核查、"
            "SEO 元数据、确定性引用门禁。其中门禁是**硬约束**——不通过不许完成卡。"
        ),
        triggers=[
            "要对已完成的草稿做发布前审校并产出终稿",
            "需要跑 SEO 检查与引用门禁，并把原始 JSON 留档",
            "Don't use for: 写初稿（blog-writing-contract）、发布（blog-publish-contract）",
        ],
        inputs=[
            "`03-draft.md`（待审稿）、`02-research.md` + `citations.json`（对照素材）",
            "本 run 的 workspace（门禁按 `HERMES_KANBAN_WORKSPACE` 定位本 run 产物）",
        ],
        outputs=["04-review.md", "final.md"],
        output_notes={
            "04-review.md": "逐条 `位置 → 原文 → 改法 → 理由`；检查过没问题的项也要写「已核查」；附 check_seo / check_citations 原始 JSON",
            "final.md": "修订后的终稿（保留/修正 frontmatter，date 用今天）",
        },
        tools=["check_seo", "check_citations"],
        forbidden=[
            "只改语言和事实错误，**不得**重写作者论点结构、不得偷偷增删小节",
            "标题长度类 issue 只记录并建议，**不许改标题**（slug 由标题生成，改标题连带改 URL）",
            "门禁 `ok=false` 时必须 `kanban_block(kind=\"needs_input\", reason=<issues 原文>)`，**不许完成本卡**",
            "不得删掉 `citations.json` 或 `final.md` 绕过门禁 —— 一旦 `03-draft.md` 存在，两件产物都成为必交件，缺一即 block",
        ],
        accept=[
            "`04-review.md` + `final.md` 都写到绝对路径",
            "check_seo 的 `score`/`issues` 与 check_citations 的原始 JSON 已贴入 review",
            "SE(「正文含 H1」「图片缺 alt」「关键字从未出现在正文」) 三类 issue 已修掉",
            "终稿汉字数也实测并写进 summary（<2000 标「未达门槛」但仍交当前最好终稿）",
        ],
    ),
    dict(
        name="blog-publish-contract",
        stage=5,
        assignee="publisher",
        body_const="PUBLISH_BODY",
        skill_title="博客发布检查清单（publisher · 5/5 段）",
        description="Use when publishing the pipeline final post to GitHub.",
        intro=(
            "把博客流水线第 5/5 段的发布动作与红线固化为可复用清单：仓库同步、"
            "slug 生成、frontmatter 校验、提交与复核。发布 = 推到 `main`，"
            "由 GitHub Actions 构建部署。"
        ),
        triggers=[
            "要把终稿发布到 Hugo 站点仓库（GitHub Pages）",
            "需要确认这次提交只包含目标文章、并留下可复核的 commit SHA",
            "Don't use for: 审校改稿（blog-review-contract）",
        ],
        inputs=["`final.md`（终稿，来自 4/5 段）", "本地站点仓库工作区（`D:\\AI_BLOG`）"],
        outputs=["content/posts/<slug>.md"],
        output_notes={
            "content/posts/<slug>.md": "终稿按 slug 命名落到站点仓库；push 到 main 由 Actions 部署。提交后须记录 commit SHA（写进 summary，不是产物文件）",
        },
        tools=["（本段只需 file / terminal：git 操作）"],
        forbidden=[
            "不许 `push --force`",
            "不许改动仓库里其它文章",
            "不许提交临时文件 / 运行目录",
            "网络或鉴权失败：`kanban_block(kind=\"needs_input\", reason=\"<原始报错>\")`，**不许谎报成功**",
            "**推送前先 `git fetch`**：用户会直接改 GitHub 网页；直接 push 会静默复活已删内容、覆盖网页编辑",
        ],
        accept=[
            "`content/posts/<slug>.md` 已在仓库中，frontmatter 与仓库其它文章格式一致",
            "`git log -1 --stat` 复核：这次提交只包含该文章文件",
            "summary 写明：文章文件路径 + commit SHA + push 是否成功 +（如适用）站点骨架/构建链路缺口",
        ],
    ),
]

FRONTMATTER_AUTHOR = "彭梓坚 (Peng2505), Hermes Agent"


# ---------------------------------------------------------------- 取模板
def load_bodies() -> dict[str, str]:
    """用 ast 取 *_BODY 字符串常量 —— 不 import 模块，避免任何副作用。"""
    tree = ast.parse(SWARM.read_text(encoding="utf-8"))
    bodies: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.endswith("_BODY"):
                bodies[target.id] = ast.literal_eval(node.value)
    return bodies


def validate(bodies: dict[str, str]) -> None:
    """构建期校验：契约与模板必须互相对得上，否则 Fail loud。"""
    problems: list[str] = []
    for role in ROLES:
        const = role["body_const"]
        body = bodies.get(const)
        if not body or not body.strip():
            problems.append(f"{role['name']}: blog_swarm.py 里找不到非空的 {const}")
            continue
        marker = f"第 {role['stage']}/5 段"
        if marker not in body:
            problems.append(f"{role['name']}: {const} 里没有「{marker}」，段号与声明不符")
        if role["assignee"] not in body:
            problems.append(f"{role['name']}: {const} 里没有出现角色名 {role['assignee']}")
        for out in role["outputs"]:
            # 按「文件名」核对，容忍模板里的目录占位（如 {run_dir}\evidence\web-*.md）。
            if "<" in out:  # 形如 content/posts/<slug>.md，按前缀目录核对
                probe = out.split("<")[0]
            else:
                probe = Path(out.replace("/", "\\")).name
            if probe not in body:
                problems.append(f"{role['name']}: {const} 里没有 {probe}，与声明的交付物不符")
    if problems:
        raise SystemExit("构建失败（契约与模板不一致）：\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------- 渲染
def render(role: dict, body: str) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items)

    io_rows = [
        "| 方向 | 产物 | 说明 |",
        "|---|---|---|",
    ]
    for src in role["inputs"]:
        io_rows.append(f"| 输入 | {src} | — |")
    for out in role["outputs"]:
        io_rows.append(f"| 输出 | `{out}` | {role['output_notes'].get(out, '—')} |")

    return f"""---
name: {role['name']}
description: "{role['description']}"
version: 0.1.0
author: {FRONTMATTER_AUTHOR}
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [blog, pipeline, kanban, role-contract, {role['assignee']}]
    related_skills: [blog-swarm-pipeline]
---

# {role['skill_title']}

{role['intro']}

> **本文件由 `tools/build_role_skills.py` 生成，请勿手改** —— 单一事实来源是
> `blog_swarm.py::{role['body_const']}`。改口径请改模板，然后重新生成。

## 触发条件

{bullets(role['triggers'])}

## I/O 契约

{chr(10).join(io_rows)}

机器可读版本：`references/io-contract.json`（同一份声明，供脚本消费）。

## 权限与边界

- **本角色工具**：{'、'.join(f'`{t}`' for t in role['tools'])}
- **禁止**：
{chr(10).join(f'  - {f}' for f in role['forbidden'])}

## 模板正文（随卡下发，权威版本）

下面是卡正文模板原文。卡下发时会替换其中的 `{{topic}}` / `{{run_dir}}` /
`{{repo_dir}}` 等占位符；**占位符之外一个字都不许改**。

```text
{body.rstrip()}
```

## 验收

{bullets(role['accept'])}

## 防漂移

```bash
python D:\\blog-swarm\\tools\\build_role_skills.py           # 重新生成
python D:\\blog-swarm\\tools\\build_role_skills.py --check   # 校验磁盘与模板一致（不一致 exit 2）
python -m pytest D:\\blog-swarm\\tests\\test_role_skills_sync.py -q
```
"""


def render_json(role: dict, body: str) -> str:
    payload = {
        "skill": role["name"],
        "generated_from": f"blog_swarm.py::{role['body_const']}",
        "stage": f"{role['stage']}/5",
        "assignee": role["assignee"],
        "inputs": role["inputs"],
        "outputs": role["outputs"],
        "tools": role["tools"],
        "forbidden": role["forbidden"],
        "acceptance": role["accept"],
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _nonblank_chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


# 口径类小节：纯规则、与内容类型无关，抽进 Skill 后可跨场景复用。
# 半通用的小节（「检查项」「检索与取证顺序」等）**保守地算进场景侧** ——
# 宁可把可复用比例报小，也不把它报大。
GENERIC_HEADS = ("## 交付物", "## 硬性要求", "## 红线", "## 完成时")


def section_stats(body: str) -> tuple[int, int]:
    """按 `## ` 小节切分，返还 (口径类字数, 全文非空白字数)。"""
    total = _nonblank_chars(body)
    generic = 0
    current_is_generic = False
    for line in body.splitlines():
        if line.startswith("## "):
            current_is_generic = any(line.startswith(h) for h in GENERIC_HEADS)
            generic += _nonblank_chars(line)
            continue
        if current_is_generic:
            generic += _nonblank_chars(line)
    return generic, total


def stats(bodies: dict[str, str]) -> None:
    """打印「口径类 / 全文」占比 —— 这是「新内容类型接入成本」的可复算口径。"""
    print(f"{'角色':<12}{'口径类':>8}{'全文':>8}{'占比':>9}")
    g_total = t_total = 0
    for role in ROLES:
        body = bodies[role["body_const"]]
        generic, total = section_stats(body)
        g_total += generic
        t_total += total
        print(f"{role['assignee']:<12}{generic:>8}{total:>8}{generic / total:>8.1%}")
    print("-" * 37)
    print(f"{'合计':<12}{g_total:>8}{t_total:>8}{g_total / t_total:>8.1%}")
    print()
    print(f"口径：口径类小节 = {' / '.join(GENERIC_HEADS)}（纯规则，无场景依赖）；")
    print("      半通用小节（检查项 / 检索取证顺序等）保守算进场景侧。")
    print(f"      换内容类型时需重写的场景类字数 = {t_total - g_total}（现在 {t_total}）。")


# ---------------------------------------------------------------- 主流程
def build() -> dict[str, str]:
    bodies = load_bodies()
    validate(bodies)
    artifacts: dict[str, str] = {}
    for role in ROLES:
        body = bodies[role["body_const"]]
        # 键一律用 POSIX 分隔符，免得 write() 写盘用 \ 而 check() 比对用 /
        base = f"{CATEGORY}/{role['name']}"
        artifacts[f"{base}/SKILL.md"] = render(role, body)
        artifacts[f"{base}/references/io-contract.json"] = render_json(role, body)
    return artifacts


def _read_text(path: Path) -> str:
    """按内容读，不做换行翻译 —— 但把 CRLF 归一成 LF。

    为什么必须归一：本仓 `core.autocrlf=true` 且没有 .gitattributes，clone 后
    checkout 写出来的是 CRLF，而生成器产出的是 LF。逐字节比对若把换行也当内容，
    测试与 `--check` 会在**别人 clone 后**误报漂移 —— 那是换行风格差异，不是内容漂移。
    """
    return path.open(encoding="utf-8", newline="").read().replace("\r\n", "\n")


def check(artifacts: dict[str, str]) -> int:
    stale: list[str] = []
    for rel, content in artifacts.items():
        path = OUT_ROOT / rel
        if not path.is_file():
            stale.append(f"缺失: {rel}")
        elif _read_text(path) != content.replace("\r\n", "\n"):
            stale.append(f"与模板不一致: {rel}")
    extra = []
    for path in OUT_ROOT.rglob("SKILL.md"):
        rel = str(path.relative_to(OUT_ROOT)).replace("\\", "/")
        if rel not in artifacts:
            extra.append(rel)
    if stale or extra:
        print("角色 Skill 已漂移（重新跑 build_role_skills.py）：")
        for item in stale + extra:
            print("  -", item)
        return 2
    print(f"OK：{len(artifacts)} 个产物与 blog_swarm.py 模板一致")
    return 0


def write(artifacts: dict[str, str]) -> None:
    for rel, content in artifacts.items():
        path = OUT_ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # 显式 LF：产物要能逐字节比对，不能被 Windows 的 CRLF 翻译污染
        path.write_text(content, encoding="utf-8", newline="\n")
        print("wrote", path.relative_to(PROJECT))


def install(artifacts: dict[str, str]) -> None:
    home = Path.home() / "AppData" / "Local" / "hermes"
    if not home.is_dir():
        raise SystemExit(f"找不到 Hermes 目录: {home}")
    for role in ROLES:
        profile = role["assignee"]
        dest = home / "profiles" / profile / "skills" / CATEGORY / role["name"]
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(OUT_ROOT / CATEGORY / role["name"], dest)
        print(f"installed -> profiles/{profile}/skills/{CATEGORY}/{role['name']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 blog_swarm.py 模板生成角色契约 Skill")
    parser.add_argument("--check", action="store_true", help="只校验，不写盘；不一致 exit 2")
    parser.add_argument("--install", action="store_true", help="生成后拷进各 worker profile")
    parser.add_argument("--stats", action="store_true", help="打印口径类 / 场景类字数占比（可复算）")
    args = parser.parse_args(argv)

    if args.stats:
        stats(load_bodies())
        return 0

    artifacts = build()
    if args.check:
        return check(artifacts)
    write(artifacts)
    if args.install:
        install(artifacts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
