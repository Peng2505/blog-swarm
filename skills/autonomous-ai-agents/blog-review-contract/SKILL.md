---
name: blog-review-contract
description: "Use when reviewing the pipeline draft before publish."
version: 0.1.0
author: 彭梓坚 (Peng2505), Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [blog, pipeline, kanban, role-contract, reviewer]
    related_skills: [blog-swarm-pipeline]
---

# 博客审校检查清单（reviewer · 4/5 段）

把博客流水线第 4/5 段的审校标准固化为可复用检查清单：语言与事实核查、SEO 元数据、确定性引用门禁。其中门禁是**硬约束**——不通过不许完成卡。

> **本文件由 `tools/build_role_skills.py` 生成，请勿手改** —— 单一事实来源是
> `blog_swarm.py::REVIEW_BODY`。改口径请改模板，然后重新生成。

## 触发条件

- 要对已完成的草稿做发布前审校并产出终稿
- 需要跑 SEO 检查与引用门禁，并把原始 JSON 留档
- Don't use for: 写初稿（blog-writing-contract）、发布（blog-publish-contract）

## I/O 契约

| 方向 | 产物 | 说明 |
|---|---|---|
| 输入 | `03-draft.md`（待审稿）、`02-research.md` + `citations.json`（对照素材） | — |
| 输入 | 本 run 的 workspace（门禁按 `HERMES_KANBAN_WORKSPACE` 定位本 run 产物） | — |
| 输出 | `04-review.md` | 逐条 `位置 → 原文 → 改法 → 理由`；检查过没问题的项也要写「已核查」；附 check_seo / check_citations 原始 JSON |
| 输出 | `final.md` | 修订后的终稿（保留/修正 frontmatter，date 用今天） |

机器可读版本：`references/io-contract.json`（同一份声明，供脚本消费）。

## 权限与边界

- **本角色工具**：`check_seo`、`check_citations`
- **禁止**：
  - 只改语言和事实错误，**不得**重写作者论点结构、不得偷偷增删小节
  - 标题长度类 issue 只记录并建议，**不许改标题**（slug 由标题生成，改标题连带改 URL）
  - 门禁 `ok=false` 时必须 `kanban_block(kind="needs_input", reason=<issues 原文>)`，**不许完成本卡**
  - 不得删掉 `citations.json` 或 `final.md` 绕过门禁 —— 一旦 `03-draft.md` 存在，两件产物都成为必交件，缺一即 block

## 模板正文（随卡下发，权威版本）

下面是卡正文模板原文。卡下发时会替换其中的 `{topic}` / `{run_dir}` /
`{repo_dir}` 等占位符；**占位符之外一个字都不许改**。

```text
## 你的角色
博客流水线第 4/5 段：**审校员（reviewer）**。待审稿：`{run_dir}\03-draft.md`
（素材对照：`{run_dir}\02-research.md` 与 `{run_dir}\citations.json`）。

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
   `draft_path={run_dir}\final.md`、`ledger_path={run_dir}\citations.json`；把原始 JSON
   贴入 `04-review.md`。若 `ok=false`，必须调用
   `kanban_block(kind="needs_input", reason=<issues 原文>)`，不许完成本卡。

## 交付物（两个文件，都写到绝对路径）
1. `{run_dir}\04-review.md`：逐条列出问题，格式 `位置 → 原文 → 改法 → 理由`；
   检查过但没问题的项也要写明"已核查"。
2. `{run_dir}\final.md`：**修订后的终稿**（保留/修正 frontmatter，date 用今天）。

## 硬性要求
- 只改语言和事实错误，不要重写作者的论点结构，不要偷偷增删小节。
- 终稿汉字数也要实测（命令同上游），写进 summary；若 < 2000，在 review 里
  标为"未达门槛"，但仍要交出当前最好的终稿。

## 完成时
调用 `kanban_complete`，summary 写：终稿绝对路径 + 修改条数 + 终稿实测汉字数 + 仍存疑的点。
```

## 验收

- `04-review.md` + `final.md` 都写到绝对路径
- check_seo 的 `score`/`issues` 与 check_citations 的原始 JSON 已贴入 review
- SE(「正文含 H1」「图片缺 alt」「关键字从未出现在正文」) 三类 issue 已修掉
- 终稿汉字数也实测并写进 summary（<2000 标「未达门槛」但仍交当前最好终稿）

## 防漂移

```bash
python D:\blog-swarm\tools\build_role_skills.py           # 重新生成
python D:\blog-swarm\tools\build_role_skills.py --check   # 校验磁盘与模板一致（不一致 exit 2）
python -m pytest D:\blog-swarm\tests\test_role_skills_sync.py -q
```
