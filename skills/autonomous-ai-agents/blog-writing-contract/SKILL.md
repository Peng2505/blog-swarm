---
name: blog-writing-contract
description: "Use when writing the pipeline draft: structure and rules."
version: 0.1.0
author: 彭梓坚 (Peng2505), Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [blog, pipeline, kanban, role-contract, writer]
    related_skills: [blog-swarm-pipeline]
---

# 博客写作风格指南（writer · 3/5 段）

把博客流水线第 3/5 段的写作标准固化为可复用风格指南：结构递进、字数硬门槛、引用格式、事实来源限制。换内容类型（如短图文）时只改篇幅与结构要求，引用/事实这套规则直接复用。

> **本文件由 `tools/build_role_skills.py` 生成，请勿手改** —— 单一事实来源是
> `blog_swarm.py::WRITER_BODY`。改口径请改模板，然后重新生成。

## 触发条件

- 要按已核实的素材写一篇技术正文（素材已由 2/5 段产出）
- 需要确保字数、引用格式、frontmatter 符合发布要求
- Don't use for: 调研取数（blog-research-contract）、审校改稿（blog-review-contract）

## I/O 契约

| 方向 | 产物 | 说明 |
|---|---|---|
| 输入 | `01-outline.md`（结构）、`02-research.md`（**只能用作事实来源**）、`citations.json`（引用编号） | — |
| 输入 | 父卡片 handoff | — |
| 输出 | `03-draft.md` | 中文 Markdown 技术博客；frontmatter 固定 title/date/tags/author 四项；正文汉字 ≥2000（实测） |

机器可读版本：`references/io-contract.json`（同一份声明，供脚本消费）。

## 权限与边界

- **本角色工具**：`（本段不需要私有库/门禁工具，只读素材 + 写文件）`
- **禁止**：
  - 事实只能用 `02-research.md` 里标为 `已核实` 的内容；`未核实`/`推断` 要么删、要么在文中显式声明
  - **禁止自行编号**：引用编号必须来自 `citations.json`
  - 不许留 TODO、占位段落、大段空话凑字数
  - 不许写「约 2000 字」蒙混 —— 必须跑统计命令拿实测数字
  - `## Sources` 里 Ixx 只写可公开的文档标题，**不暴露本机绝对路径**

## 模板正文（随卡下发，权威版本）

下面是卡正文模板原文。卡下发时会替换其中的 `{topic}` / `{run_dir}` /
`{repo_dir}` 等占位符；**占位符之外一个字都不许改**。

```text
## 你的角色
博客流水线第 3/5 段：**写作者（writer）**。素材：`{run_dir}\01-outline.md`
与 `{run_dir}\02-research.md`、`{run_dir}\citations.json`（也可读父卡片 handoff）。

## 交付物（写到绝对路径）
`{run_dir}\03-draft.md`：一篇中文技术博客，Markdown。

## 结构要求
- frontmatter 固定四项：`title` / `date`（用今天日期 YYYY-MM-DD）/ `tags` / `author`（彭梓坚）。
- 正文按大纲 5–7 节展开，每节有独立小标题，逻辑递进（现状 → 原理 → 实现 → 取舍 → 结论）。
- 有代码示例时给出可运行片段；不能跑的要显式标注"伪代码"。

## 字数硬门槛（必须实测，不许估）
正文汉字数 **>= 2000**（不含 frontmatter、代码块、参考文献）。写完自己跑一遍统计：

```
python -c "import re,io;t=io.open(r'{run_dir}\03-draft.md',encoding='utf-8').read();t=re.sub(r'```.*?```','',t,flags=re.S);print(len(re.findall(r'[\u4e00-\u9fff]',t)))"
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
```

## 验收

- `03-draft.md` 存在，frontmatter 四项齐全
- 正文汉字数 **实测** ≥2000（命令见模板正文），数字写进 summary
- 所有外部事实句末带内联引用；`## Sources` 与正文引用编号一致
- summary 写明：草稿绝对路径 + 实测汉字数 + 引用来源条数

## 防漂移

```bash
python D:\blog-swarm\tools\build_role_skills.py           # 重新生成
python D:\blog-swarm\tools\build_role_skills.py --check   # 校验磁盘与模板一致（不一致 exit 2）
python -m pytest D:\blog-swarm\tests\test_role_skills_sync.py -q
```
