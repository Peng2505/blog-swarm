---
name: blog-research-contract
description: "Use when researching a blog topic for the pipeline."
version: 0.1.0
author: 彭梓坚 (Peng2505), Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [blog, pipeline, kanban, role-contract, researcher]
    related_skills: [blog-swarm-pipeline]
---

# 博客调研契约（researcher · 2/5 段）

把博客流水线第 2/5 段的调研交付标准固化为可复用契约：取证顺序、可信度分档、公开来源快照规则，以及引用编号（Ixx 私有 / Wxx 公开）的产生方式。新内容类型接入时只需换卡正文里的场景描述，这段口径不用重写。

> **本文件由 `tools/build_role_skills.py` 生成，请勿手改** —— 单一事实来源是
> `blog_swarm.py::RESEARCH_BODY`。改口径请改模板，然后重新生成。

## 触发条件

- 要把一个选题做成「可核实的素材包」（结论 → 依据 → 来源）
- 需要用私有知识库 + 公开网页两条来源取数，并保留逐字证据
- 需要产出供下游写作/审校使用的引用账本 citations.json
- Don't use for: 写正文（那是 blog-writing-contract）、审校（blog-review-contract）

## I/O 契约

| 方向 | 产物 | 说明 |
|---|---|---|
| 输入 | 父卡片 handoff：上游 `01-outline.md` 的「待调研问题清单」 | — |
| 输入 | 本 run 的 workspace 目录（`HERMES_KANBAN_WORKSPACE`，由卡下发） | — |
| 输入 | 本机私有知识库索引（默认语义库 `index-bge-m3`） | — |
| 输出 | `02-research.md` | 逐条回答待调研问题；可引用事实清单 3–6 条；三档可信度标注 |
| 输出 | `citations.json` | 稳定引用编号 + 逐字证据；私有 Ixx / 公开 Wxx |
| 输出 | `evidence/web-*.md` | 网页**原文快照**，禁止只引用搜索摘要 |

机器可读版本：`references/io-contract.json`（同一份声明，供脚本消费）。

## 权限与边界

- **本角色工具**：`retrieve_private_knowledge`、`record_public_source`
- **禁止**：
  - **绝对不许编造**：找不到来源就写「未找到可靠来源」，不要写貌似合理的数字
  - 不得写正文（正文是第 3 段的事），只交素材
  - 不得复制大段原文 —— 用要点概括并保留链接
  - 私有来源（Ixx）不得包装成公开事实
  - 跨 run 读写会被拒：文件访问收口在本卡 workspace

## 模板正文（随卡下发，权威版本）

下面是卡正文模板原文。卡下发时会替换其中的 `{topic}` / `{run_dir}` /
`{repo_dir}` 等占位符；**占位符之外一个字都不许改**。

```text
## 你的角色
博客流水线第 2/5 段：**调研员（researcher）**。上一步的拆解结果见父卡片 handoff。

## 交付物（写到绝对路径）
1. `{run_dir}\02-research.md`，结构：
1. 按 `01-outline.md` 的待调研问题逐条回答：**结论 → 依据 → 来源**。
2. 可引用的具体事实清单 3–6 条（含来源 URL / 文档名 / 版本号）。
3. 明确区分三档可信度：`已核实`（有来源）/ `未核实`（找不到可靠来源）/ `推断`（你的推理）。
2. `{run_dir}\citations.json`：稳定引用编号与逐字证据；私有来源为 `Ixx`，
   可公开验证的 Web 来源为 `Wxx`。
3. Web 页面原文快照写入 `{run_dir}\evidence\web-*.md`，禁止只引用搜索摘要。

## 检索与取证顺序
1. 对每个待调研问题先调用 `retrieve_private_knowledge`，参数必须带
   `ledger_path={run_dir}\citations.json`；命中只表示相关，不等于事实已核实。
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
```

## 验收

- `02-research.md` 存在，且每条待调研问题都有「结论 → 依据 → 来源」
- 每条断言都能指到来源，或明确标为「未找到可靠来源」
- `citations.json` 中每个 Ixx 都绑定了知识库 chunk、每个 Wxx 都有落盘快照
- summary 写明：文件绝对路径 + 已核实条数 / 未核实条数 + 关键来源

## 防漂移

```bash
python D:\blog-swarm\tools\build_role_skills.py           # 重新生成
python D:\blog-swarm\tools\build_role_skills.py --check   # 校验磁盘与模板一致（不一致 exit 2）
python -m pytest D:\blog-swarm\tests\test_role_skills_sync.py -q
```
