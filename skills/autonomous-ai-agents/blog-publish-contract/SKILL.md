---
name: blog-publish-contract
description: "Use when publishing the pipeline final post to GitHub."
version: 0.1.0
author: 彭梓坚 (Peng2505), Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [blog, pipeline, kanban, role-contract, publisher]
    related_skills: [blog-swarm-pipeline]
---

# 博客发布检查清单（publisher · 5/5 段）

把博客流水线第 5/5 段的发布动作与红线固化为可复用清单：仓库同步、slug 生成、frontmatter 校验、提交与复核。发布 = 推到 `main`，由 GitHub Actions 构建部署。

> **本文件由 `tools/build_role_skills.py` 生成，请勿手改** —— 单一事实来源是
> `blog_swarm.py::PUBLISH_BODY`。改口径请改模板，然后重新生成。

## 触发条件

- 要把终稿发布到 Hugo 站点仓库（GitHub Pages）
- 需要确认这次提交只包含目标文章、并留下可复核的 commit SHA
- Don't use for: 审校改稿（blog-review-contract）

## I/O 契约

| 方向 | 产物 | 说明 |
|---|---|---|
| 输入 | `final.md`（终稿，来自 4/5 段） | — |
| 输入 | 本地站点仓库工作区（`D:\AI_BLOG`） | — |
| 输出 | `content/posts/<slug>.md` | 终稿按 slug 命名落到站点仓库；push 到 main 由 Actions 部署。提交后须记录 commit SHA（写进 summary，不是产物文件） |

机器可读版本：`references/io-contract.json`（同一份声明，供脚本消费）。

## 权限与边界

- **本角色工具**：`（本段只需 file / terminal：git 操作）`
- **禁止**：
  - 不许 `push --force`
  - 不许改动仓库里其它文章
  - 不许提交临时文件 / 运行目录
  - 网络或鉴权失败：`kanban_block(kind="needs_input", reason="<原始报错>")`，**不许谎报成功**
  - **推送前先 `git fetch`**：用户会直接改 GitHub 网页；直接 push 会静默复活已删内容、覆盖网页编辑

## 模板正文（随卡下发，权威版本）

下面是卡正文模板原文。卡下发时会替换其中的 `{topic}` / `{run_dir}` /
`{repo_dir}` 等占位符；**占位符之外一个字都不许改**。

```text
## 你的角色
博客流水线第 5/5 段：**发布者（publisher）**。终稿：`{run_dir}\final.md`。

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
```

## 验收

- `content/posts/<slug>.md` 已在仓库中，frontmatter 与仓库其它文章格式一致
- `git log -1 --stat` 复核：这次提交只包含该文章文件
- summary 写明：文章文件路径 + commit SHA + push 是否成功 +（如适用）站点骨架/构建链路缺口

## 防漂移

```bash
python D:\blog-swarm\tools\build_role_skills.py           # 重新生成
python D:\blog-swarm\tools\build_role_skills.py --check   # 校验磁盘与模板一致（不一致 exit 2）
python -m pytest D:\blog-swarm\tests\test_role_skills_sync.py -q
```
