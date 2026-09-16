#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""博客流水线一键启动 —— Kanban 5 段式角色链（board: blog）

拓扑（每张卡由对应 profile 的 worker 执行，父卡完成后子卡自动解锁）:

    1/5 orchestrator 拆解选题与大纲
        2/5 researcher 调研与素材收集
            3/5 writer 写作正文（正文汉字数 >= 2000）
                4/5 reviewer 审校语法/错别字/事实一致性 -> 终稿 final.md
                    5/5 publisher 发布到 GitHub (Peng2505/AI_BLOG)

用法:
    python blog_swarm.py "主题"              # 建卡并交给 dispatcher
    python blog_swarm.py "主题" --park       # 只建卡、不调度（测试用）
    python blog_swarm.py "主题" --dry-run    # 只打印将执行的命令
    python blog_swarm.py "主题" --json       # 机器可读输出

注意: 卡片的 workspace 用 `dir:` 指向真实目录，父卡 -> 子卡的交接走
kanban 的 parent 链接（父卡 summary 会原样出现在子卡上下文里），文件本身
落在共享 run 目录，不依赖 scratch 工作区（scratch 完成即删）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOARD = "blog"
CREATED_BY = "blog-swarm"
RUN_ROOT = Path(r"D:\blog-runs")
REPO_DIR = Path(r"D:\AI_BLOG")
REPO_URL = "https://github.com/Peng2505/AI_BLOG.git"
REPO_SLUG = "Peng2505/AI_BLOG"

# ---------------------------------------------------------------- 卡片正文模板

ORCH_BODY = """## 你的角色
博客流水线第 1/5 段：**拆解者（orchestrator）**。你只做拆解，不做调研、不写正文。

## 选题
{topic}

## 交付物（写到绝对路径）
`{run_dir}\\01-outline.md`，内容必须包含：
1. **目标读者与定位**：一句话说清"写给谁、读完能得到什么"。
2. **文章大纲**：5–7 个小节，每节 2–4 条要点（不要只写小标题）。
3. **待调研问题清单**：3–6 条，每条写清"要回答什么 + 为什么必须回答"。
4. **技术边界**：哪些结论必须有可核实的来源（版本号、参数、对比数据），
   哪些可以只讲原理和思路。

## 硬性要求
- 禁止编造事实、数据、版本号、论文名。不确定的一律写"待调研"。
- 不做调研本身（那是下游第 2 段的事），只产出问题清单和结构。
- 中文，Markdown，不要套话开场。

## 完成时
调用 `kanban_complete`，summary 写：大纲文件绝对路径 + 小节数 + 待调研问题条数。
"""

RESEARCH_BODY = """## 你的角色
博客流水线第 2/5 段：**调研员（researcher）**。上一步的拆解结果见父卡片 handoff。

## 交付物（写到绝对路径）
1. `{run_dir}\\02-research.md`，结构：
1. 按 `01-outline.md` 的待调研问题逐条回答：**结论 → 依据 → 来源**。
2. 可引用的具体事实清单 3–6 条（含来源 URL / 文档名 / 版本号）。
3. 明确区分三档可信度：`已核实`（有来源）/ `未核实`（找不到可靠来源）/ `推断`（你的推理）。
2. `{run_dir}\\citations.json`：稳定引用编号与逐字证据；私有来源为 `Ixx`，
   可公开验证的 Web 来源为 `Wxx`。
3. Web 页面原文快照写入 `{run_dir}\\evidence\\web-*.md`，禁止只引用搜索摘要。

## 检索与取证顺序
1. 对每个待调研问题先调用 `retrieve_private_knowledge`，参数必须带
   `ledger_path={run_dir}\\citations.json`；命中只表示相关，不等于事实已核实。
2. API、版本号、发布日期、性能、安全等时效性事实，即使私有库命中，也必须再查
   官方文档或本机实测；私有资料与官方资料冲突时，以当前官方资料/实测为准并记录冲突。
3. Web 来源先抓取正文并保存快照，再调用 `record_public_source` 登记 URL、逐字证据、
   快照路径与定位；返回的 `Wxx` 才能作为公开技术断言的引用。
4. 只有私有来源 `Ixx` 支撑的内容，只能写成"本项目实践/作者经验"，不能冒充官方结论。

## 硬性要求
- 每个断言都要能指到来源；找不到来源就写"未找到可靠来源"，**绝对不许编造**。
- 不写正文（正文是第 3 段的事），只交素材。
- 不要复制大段原文，用要点概括并保留链接。

## 完成时
调用 `kanban_complete`，summary 写：文件绝对路径 + 已核实条数 / 未核实条数 + 关键来源。
"""

WRITER_BODY = """## 你的角色
博客流水线第 3/5 段：**写作者（writer）**。素材：`{run_dir}\\01-outline.md`
与 `{run_dir}\\02-research.md`、`{run_dir}\\citations.json`（也可读父卡片 handoff）。

## 交付物（写到绝对路径）
`{run_dir}\\03-draft.md`：一篇中文技术博客，Markdown。

## 结构要求
- frontmatter 固定四项：`title` / `date`（用今天日期 YYYY-MM-DD）/ `tags` / `author`（彭梓坚）。
- 正文按大纲 5–7 节展开，每节有独立小标题，逻辑递进（现状 → 原理 → 实现 → 取舍 → 结论）。
- 有代码示例时给出可运行片段；不能跑的要显式标注"伪代码"。

## 字数硬门槛（必须实测，不许估）
正文汉字数 **>= 2000**（不含 frontmatter、代码块、参考文献）。写完自己跑一遍统计：

```
python -c "import re,io;t=io.open(r'{run_dir}\\03-draft.md',encoding='utf-8').read();t=re.sub(r'```.*?```','',t,flags=re.S);print(len(re.findall(r'[\\u4e00-\\u9fff]',t)))"
```

把实测数字写进 summary。不足 2000 就继续写，**不许写"约 2000 字"蒙混**。

## 硬性要求
- 事实只能用 `02-research.md` 里标记为 `已核实` 的内容；
  `未核实`/`推断` 的内容要么删掉，要么在文中明确写"这一点尚无公开来源"。
- 所有外部事实使用句末内联引用，如 `[W01]`；作者实践可用 `[I01]`，但必须明确写成
  本项目经验，不得把私有资料包装成公开事实。
- 文章末尾写 `## Sources`，逐条列出正文实际引用的编号、标题、公开 URL；
  `Ixx` 只写可公开的文档标题，不暴露本机绝对路径。引用编号必须来自
  `citations.json`，禁止自行编号。
- 不许留 TODO、不许留占位段落、不许大段空话凑字数。

## 完成时
调用 `kanban_complete`，summary 写：草稿绝对路径 + 实测正文汉字数 + 引用来源条数。
"""

REVIEW_BODY = """## 你的角色
博客流水线第 4/5 段：**审校员（reviewer）**。待审稿：`{run_dir}\\03-draft.md`
（素材对照：`{run_dir}\\02-research.md` 与 `{run_dir}\\citations.json`）。

## 检查项（逐项过，不要跳）
1. 错别字、语法错误、标点误用、中英文混排空格。
2. 前后矛盾：术语是否一致、数字是否前后对得上、结论是否被前文支持。
3. 与素材冲突：文中断言是否与 `02-research.md` 的"已核实"事实一致；
   有没有把"未核实"的东西写成了断言。
4. 结构：小标题是否匹配内容、有没有断裂或重复的段落。
5. AI 味套话（"在当今快速发展的时代""总而言之"式空洞总结）——删。
6. SEO 元数据：审完语言后调用 `check_seo`（toolset 已在本 profile 启用），
   参数：`title` / `description`（没有就留空）/ `content`=终稿正文全文（不含 frontmatter）/
   `tags` / `slug`。把返回 JSON 的 `score` 与 `issues` 原样贴进 `04-review.md`。
   - `issues` 里的「正文含 H1」「图片缺 alt」「关键字从未出现在正文」——必须修掉。
   - 缺 `description`：自己写一条 60–120 字的中文 meta description，补进 `final.md`
     的 frontmatter（这是新增字段，不算改论点；head.html 有它才会输出这篇自己的
     meta description，否则全站共用站点描述）。
   - 标题长度类 issue 只在 review 里记录并给出建议，**不许改标题**：slug 由标题生成，
     改标题会连带改 URL。
7. 引用溯源：对修订后的 `final.md` 调用 `check_citations`，参数为
   `draft_path={run_dir}\\final.md`、`ledger_path={run_dir}\\citations.json`；把原始 JSON
   贴入 `04-review.md`。若 `ok=false`，必须调用
   `kanban_block(kind="needs_input", reason=<issues 原文>)`，不许完成本卡。

## 交付物（两个文件，都写到绝对路径）
1. `{run_dir}\\04-review.md`：逐条列出问题，格式 `位置 → 原文 → 改法 → 理由`；
   检查过但没问题的项也要写明"已核查"。
2. `{run_dir}\\final.md`：**修订后的终稿**（保留/修正 frontmatter，date 用今天）。

## 硬性要求
- 只改语言和事实错误，不要重写作者的论点结构，不要偷偷增删小节。
- 终稿汉字数也要实测（命令同上游），写进 summary；若 < 2000，在 review 里
  标为"未达门槛"，但仍要交出当前最好的终稿。

## 完成时
调用 `kanban_complete`，summary 写：终稿绝对路径 + 修改条数 + 终稿实测汉字数 + 仍存疑的点。
"""

PUBLISH_BODY = """## 你的角色
博客流水线第 5/5 段：**发布者（publisher）**。终稿：`{run_dir}\\final.md`。

## 目标
仓库 `{repo_slug}`（Hugo 站点，Pages 走 GitHub Actions；发布 = 推到 `main`）。
本地工作区：`{repo_dir}`

## 步骤
1. `cd {repo_dir}`；若目录不存在或不完整：`git clone {repo_url} {repo_dir}`；
   已存在则 `git pull --ff-only`。
2. 生成 slug（标题转小写连字符），把 `final.md` 复制为 `content/posts/<slug>.md`。
3. 校验 frontmatter：`title`/`date`/`tags`/`author` 齐全，格式和仓库里其它文章一致。
4. `git add content/posts/<slug>.md` → `git commit -m "post: <标题>"` → `git push origin main`。
5. 记录 commit SHA，并用 `git log -1 --stat` 复核这次提交只包含该文章文件。

## 红线
- 不许 `push --force`；不许改动仓库里其它文章；不许提交临时文件/运行目录。
- 网络或鉴权失败：调用 `kanban_block(kind="needs_input", reason="<原始报错>")`，
  把原始报错贴进 reason，**不许谎报成功**。
- 若站点骨架或构建 workflow 缺失，仍照常提交文章文件，但必须在 summary 里写明这一点。

## 完成时
调用 `kanban_complete`，summary 写：文章文件路径 + commit SHA + push 是否成功 +
（如适用）站点骨架/构建链路的缺口。
"""

STAGES = [
    dict(
        key="1-decompose",
        assignee="orchestrator",
        title="1/5 拆解选题与大纲",
        body=ORCH_BODY,
        workspace=None,  # 用 run_dir
    ),
    dict(
        key="2-research",
        assignee="researcher",
        title="2/5 调研与素材收集",
        body=RESEARCH_BODY,
        workspace=None,
    ),
    dict(
        key="3-write",
        assignee="writer",
        title="3/5 写作正文（≥2000 汉字）",
        body=WRITER_BODY,
        workspace=None,
    ),
    dict(
        key="4-review",
        assignee="reviewer",
        title="4/5 审校语法与事实，出终稿",
        body=REVIEW_BODY,
        workspace=None,
    ),
    dict(
        key="5-publish",
        assignee="publisher",
        title="5/5 发布到 GitHub",
        body=PUBLISH_BODY,
        workspace=REPO_DIR,
    ),
]


# ---------------------------------------------------------------- 工具函数

def slugify_topic(topic: str) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    ascii_part = ascii_part[:24].strip("-")
    digest = hashlib.md5(topic.encode("utf-8")).hexdigest()[:6]
    return f"{ascii_part}-{digest}" if ascii_part else digest


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    kwargs = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, **kwargs)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def ensure_repo(dry_run: bool) -> tuple[bool, str]:
    """确保本地仓库存在。返回 (ok, note)。"""
    if (REPO_DIR / ".git").exists():
        return True, f"仓库已存在: {REPO_DIR}"
    if dry_run:
        return True, f"[dry-run] 将 clone {REPO_URL} -> {REPO_DIR}"
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    code, out, err = run_cmd(["git", "clone", REPO_URL, str(REPO_DIR)])
    if code == 0:
        return True, f"已 clone 到 {REPO_DIR}"
    return False, f"clone 失败 (exit {code}): {(err or out).strip()[:300]}"


# ---------------------------------------------------------------- 主流程

def build_plan(topic: str, run_id: str) -> tuple[Path, list[dict]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise ValueError("run_id 必须是 1–128 位字母、数字、短横线或下划线")
    root = RUN_ROOT.resolve()
    run_dir = (root / run_id).resolve()
    try:
        run_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("run_id 解析后逃逸 blog-runs 根目录") from exc
    plan = []
    parent_key = None
    for stage in STAGES:
        ws = stage["workspace"] or run_dir
        plan.append({
            "key": stage["key"],
            "parent_key": parent_key,
            "assignee": stage["assignee"],
            "title": f"[{run_id}] {stage['title']}",
            "body": stage["body"].format(
                topic=topic,
                run_dir=run_dir,
                repo_dir=REPO_DIR,
                repo_url=REPO_URL,
                repo_slug=REPO_SLUG,
            ),
            "workspace": f"dir:{ws}",
            "idempotency_key": f"blog:{run_id}:{stage['key']}",
        })
        parent_key = stage["key"]
    return run_dir, plan


def main() -> int:
    ap = argparse.ArgumentParser(description="启动博客流水线（Kanban 5 段角色链）")
    ap.add_argument("topic", help="博客主题")
    ap.add_argument("--run-id", default=None, help="运行 id（默认 日期-主题hash）")
    ap.add_argument("--board", default=BOARD)
    ap.add_argument("--park", action="store_true",
                    help="建卡但保持 blocked（不触发调度，测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-clone", action="store_true", help="不检查/不克隆仓库")
    args = ap.parse_args()

    topic = args.topic.strip()
    if not topic:
        print("topic 不能为空", file=sys.stderr)
        return 2

    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d')}-{slugify_topic(topic)}"
    run_dir, plan = build_plan(topic, run_id)

    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
    repo_ok, repo_note = (True, "跳过仓库检查") if args.no_clone else ensure_repo(args.dry_run)

    created: dict[str, str] = {}
    results = []
    for stage in plan:
        cmd = [
            "hermes", "kanban", "--board", args.board, "create", stage["title"],
            "--assignee", stage["assignee"],
            "--body", stage["body"],
            "--workspace", stage["workspace"],
            "--idempotency-key", stage["idempotency_key"],
            "--created-by", CREATED_BY,
            "--json",
        ]
        if stage["parent_key"] and stage["parent_key"] in created:
            cmd += ["--parent", created[stage["parent_key"]]]

        if args.dry_run:
            results.append({**{k: stage[k] for k in ("key", "assignee", "title")},
                            "id": None, "status": "dry-run", "cmd": cmd})
            continue

        code, out, err = run_cmd(cmd)
        task_id = None
        status = f"exit {code}"
        if code == 0:
            m = re.search(r"\{.*\}", out, re.S)
            if m:
                try:
                    data = json.loads(m.group(0))
                    task_id = data.get("id")
                    status = data.get("status", "?")
                except json.JSONDecodeError:
                    status = "JSON 解析失败"
            else:
                status = "无 JSON 输出"
        if not task_id:
            print(f"✗ {stage['key']} 建卡失败: {status}\n{(err or out).strip()[:500]}",
                  file=sys.stderr)
            return 3

        # --park: 建卡后立刻 block 住。注意 `--initial-status blocked` 不会阻止
        # dispatcher 认领（无原因的 blocked 会被提升），必须显式 block 才安全。
        if args.park:
            run_cmd(["hermes", "kanban", "--board", args.board, "block", task_id,
                     "parked by blog_swarm --park (topology test)"])
            status = "parked"

        created[stage["key"]] = task_id
        results.append({**{k: stage[k] for k in ("key", "assignee", "title")},
                        "id": task_id, "status": status})

    payload = {
        "ok": True,
        "board": args.board,
        "topic": topic,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "repo_dir": str(REPO_DIR),
        "repo_ok": repo_ok,
        "repo_note": repo_note,
        "parked": bool(args.park),
        "dry_run": bool(args.dry_run),
        "cards": results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"博客流水线已创建  board={args.board}  run_id={run_id}")
    print(f"  主题      : {topic}")
    print(f"  运行目录  : {run_dir}")
    print(f"  仓库      : {REPO_DIR}  ({repo_note})")
    print()
    for r in results:
        print(f"  {r['id'] or '-':<12} {r['assignee']:<13} {r['status']:<8} {r['title']}")
    if args.park:
        print("\n  ⚠ --park: 卡片保持 blocked，不会被 dispatcher 执行。")
        print(f"  解除: hermes kanban --board {args.board} unblock <id>...")
    elif args.dry_run:
        print("\n  (dry-run，未执行任何命令)")
    else:
        print(f"\n  跟随进度: hermes kanban --board {args.board} watch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
