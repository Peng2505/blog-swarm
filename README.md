# 博客流水线运行手册（Kanban 5 段式角色链）

> 首次全链路跑通：2026-09-16。本文件是这套流水线的**唯一权威说明**，
> 记录拓扑、启动方式、每段验收标准、查看/干预命令、已踩过的坑。
> 改脚本或改卡片模板后请同步更新本文件。

## 一、这套东西由什么组成

```
选题目（人给）→ 1/5 拆解 → 2/5 调研 → 3/5 写作 → 4/5 审校 → 5/5 发布
                orchestrator  researcher   writer     reviewer   publisher
                父卡完成 → 子卡自动解锁（kanban parent→child 链接）
```

| 组件 | 位置 | 说明 |
|---|---|---|
| 看板 | `%LOCALAPPDATA%\hermes\kanban\boards\blog\kanban.db` | 名称「技术博客工作板」，slug=`blog`；看板是整个 Hermes 根目录共享的，不属于任何单个 profile |
| 启动脚本 | `D:\blog-swarm\blog_swarm.py` | 一次建 5 张卡并串好父子链 |
| 触发 hook | `D:\blog-swarm\trigger.py` | 注册在主 config 的 `hooks.pre_llm_call`；微信/邮件收到「写博客：<主题>」自动启动 |
| 运行目录 | `D:\blog-runs\<run_id>\` | `01-outline.md` → `02-research.md` → `03-draft.md` → `04-review.md` → `final.md` + `evidence/` |
| 发布仓库 | `D:\AI_BLOG` → `github.com/Peng2505/AI_BLOG` | Hugo 站点，Pages 源 = GitHub Actions；**推 main 即上线** |
| 线上地址 | `https://peng2505.github.io/AI_BLOG/posts/<slug>/` | 项目页部署在 `/AI_BLOG/` 子路径下 |

`run_id` 默认 `YYYYMMDD-<主题 ascii 前 24 字>-<md5(主题)[:6]>`，例如 `20260916-kanban-swarm`。

## 二、前置条件（跑之前逐条确认）

1. **gateway 必须在跑** —— kanban dispatcher 现在内嵌在 gateway 里（60s 一跳），
   `hermes kanban daemon` 已废弃。
   ```bash
   grep "kanban dispatcher" "$LOCALAPPDATA/hermes/logs/gateway.log" | tail -3
   # 期望看到：kanban dispatcher: embedded in gateway (interval=60.0s)
   ```
   ⚠️ `hermes gateway status` 在别的 profile 的 shell 里可能误报 "not running"——
   以 gateway.log 为准，不要以 status 为准。
2. **5 个 worker profile 的工具面**：worker 会话的工具清单**完全由被派发 profile 的
   `platform_toolsets.cli` 决定**（dispatcher 在 `kanban_db.py::_resolve_worker_cli_toolsets`
   里解析后以显式 `--toolsets` 传给 worker）。必须在列表里有
   `file` / `terminal` / `web` / `kanban`，否则 worker 写不出交付物。
   2026-09-16 已核对 5 个 profile 全部齐全。
3. 仓库存在且干净：`cd D:/AI_BLOG && git status`（发布卡自己会 `pull --ff-only`）。
4. **reviewer profile 的 SEO 插件**（2026-09-16 装）：插件本体在
   `%LOCALAPPDATA%\hermes\profiles\reviewer\plugins\seo-checker\`，
   且必须同时满足两件事，否则 `check_seo` 会**静默缺席**（不报错、worker 也不会说）：
   ```bash
   hermes -p reviewer plugins list   | grep seo-checker   # 需 enabled（plugins.enabled）
   hermes -p reviewer tools list    | grep blog_tools    # 需 ✓ enabled（platform_toolsets.cli）
   ```
   两个开关分开：插件启用 ≠ 工具面启用。worker 的工具面由 `platform_toolsets.cli`
   经 `_resolve_worker_cli_toolsets` 解析后以 `--toolsets` 显式下发，插件工具集
   （`blog_tools`）不在里面就不会进上下文。
5. hugo 不在 PATH 时的真实位置：`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Hugo.Hugo.Extended_*`
   （0.166.0，与 CI 里 pin 的版本一致）。

## 三、两种启动方式

### A. 脚本一键（5 张卡模板化）
```bash
python D:/blog-swarm/blog_swarm.py "主题"                 # 建卡并交给 dispatcher
python D:/blog-swarm/blog_swarm.py "主题" --park          # 只建卡不调度（测试拓扑用）
python D:/blog-swarm/blog_swarm.py "主题" --dry-run       # 只打印将执行的命令
python D:/blog-swarm/blog_swarm.py "主题" --json          # 机器可读输出
```
卡片的 `--workspace` 用 `dir:<run_dir>`（不要 scratch：scratch 完成即删），
幂等键 `blog:<run_id>:<stage>`，`--created-by blog-swarm`。

### B. 单张选题卡交给 orchestrator（**推荐，提示词更贴合主题**）
在 `blog` 板上建一张卡，**标题就是选题**，assignee=`orchestrator`，body 留空。
orchestrator worker 会把它当 1/5，产出大纲后**自己**写 2/5–5/5 的正文并串好父子链。
2026-09-16 那篇 Kanban Swarm 实测就是这么跑的（下游卡 body 里带着逐条取证路线、
版本口径红线、字数实测命令，比脚本模板强）。

### C. 微信/邮件触发（hook）
消息首行匹配 `/blog <主题>`、`写博客：<主题>`、`发起博客 <主题>`、`博客流水线：<主题>`
（`trigger.py`，仅 weixin/email 生效）→ 调 `blog_swarm.py` → 把卡片 id 注入本轮上下文，
并 `notify-subscribe` 到该私聊，每段完成/失败推微信。

## 四、每段验收标准（审校和验收都按这张表过）

| 段 | 交付物 | 硬性验收 |
|---|---|---|
| 1/5 orchestrator | `01-outline.md` | 目标读者与定位 / 5–7 小节大纲（每节 2–4 要点）/ 3–6 条待调研问题 / 技术边界；**不许编造**，不确定的写「待调研」 |
| 2/5 researcher | `02-research.md` | 逐问「结论→依据→来源」/ 3–6 条可引用事实 / **三档可信度 `已核实` `未核实` `推断`** |
| 3/5 writer | `03-draft.md` | frontmatter 四项（title/date/tags/author=彭梓坚）/ 正文汉字数 **实测 ≥2000**（去代码块）/ 只用 `已核实` 事实 |
| 4/5 reviewer | `04-review.md` + `final.md` | 8 项检查逐项过 + **可复现性抽查：实跑正文里 ≥2 条命令**、不符要标「实测不符」/ 版本口径统一 / 无「提升 X%」这类无基准数字 / 只改语言与事实，不动论点结构 / **`check_seo` 实测跑过且 score+issues 写进 `04-review.md`**（插件没装或工具集没开时，这一条会静默通过——所以必须看到 review 里贴出的 JSON） |
| 5/5 publisher | `content/posts/<slug>.md` | slug 固定（已存在就更新，不另起名）/ `git pull --ff-only` → add → commit → push main / **`git log -1 --stat` 只含该文章** + **`git ls-remote origin main` 复核 SHA** / 不许 force push / 失败即 `kanban_block(kind="needs_input", reason="<原始报错>")`，不许谎报成功 |

**发布者可以再开一张卡**：线上复核发现站点缺陷时，新建一张卡（assignee 用 `default`，
父卡指向发布卡），把 curl 证据、根因、修法写进去。2026-09-16 的 Hugo 子路径链接
404 就是这么修的（commit `47724c8`）。

## 五、查看进度与人工干预

```bash
hermes kanban --board blog stats                    # 各状态/各 assignee 计数
hermes kanban --board blog list                     # 卡片列表
hermes kanban --board blog show <task_id>           # 卡 + 评论 + 事件
hermes kanban --board blog runs <task_id>           # 每次尝试的 profile/outcome/summary
hermes kanban --board blog log <task_id>            # worker 原始日志
hermes kanban --board blog context <task_id>        # worker 实际看到的完整上下文
hermes kanban --board blog watch                    # 实时事件流
hermes kanban --board blog tail <task_id>           # 跟随单卡事件
```
被派发后 `worker_pid` 在库里可见；卡住先用 `show`/`runs` 看是不是 claim 过期，
`reclaim` 释放、`promote` 手动提升、`unblock` 解除阻塞。

## 六、已踩过的坑

1. **`HERMES_KANBAN_DB` 会静默覆盖 `--board`**（实测）：设了它之后
   `hermes kanban --board blog stats` 读的是被钉住的库，`--board` 无效。
   做实验前 `unset HERMES_KANBAN_DB`，且**任何建卡实验都不许落在 `blog` 板上**。
2. **官方文档与 CLI 定义不符**：`website/docs/user-guide/features/kanban.md` 里的
   `--workers researcher,architect,sre` 在本机直接 `unrecognized arguments`（exit 2）。
   真实定义是 `hermes kanban swarm "<goal>" --worker profile:title[:skill,skill]`（可重复）
   + 必填 `--verifier` / `--synthesizer`。
3. **`hermes kanban swarm` ≠ 本流水线**：swarm 是「并行 worker → verifier → synthesizer」，
   它的 `metadata {"gate": "pass"}` 只是卡片正文里的 prompt 约定，**内核不检查**
   （实测：不带 gate 照样放行 synthesizer）。本流水线是**串行父子卡链**，别混用。
4. **worker 没文件工具 = 那条边是断的**：worker 会自己找绕路（实测：delegate 到子代理
   + 开 cron 会话，白烧 7 分钟，还触发了一次无人应答的 computer_use 审批）。开工前
   先拿一张探针卡验证。
5. **`--initial-status blocked` 拦不住 dispatcher**（无原因的 blocked 会被提升），
   要测试拓扑必须显式 `hermes kanban block <id> "<理由>"`（`blog_swarm.py --park` 就是干这个）。
   而且探针链跑完记得拆掉，否则它会一路跑到「发布」。
6. **Hugo 子路径部署**：`{{ "/x/" | relURL }}` 对以 `/` 开头的入参原样返回，
   **不拼 baseURL 的路径部分** → 项目页上所有手写站内链接全 404。
   正解：`.Site.Home.RelPermalink`、`(site.GetPage "/posts").RelPermalink`。
   注意同一页里 CSS/og:url/RSS 是对的，只有模板里手写的 relURL 坏了。
7. **summary 里的假卡 id 会被内核抓**：发布卡 summary 引用了不存在的 `t_7662b20f`，
   内核写了一条 `suspected_hallucinated_references` 事件。引用卡 id 前先确认它真存在。
8. **卡片 summary 是自述，不是事实**：验收一律回到文件（路径/字节/sha256/汉字数）、
   远端（`git ls-remote`）和线上（curl 状态码）。发布卡就靠这三样自证。


## 七、私有知识库与引用溯源

第 2/5 调研段接入 `rag/`（本机私有知识库），第 4/5 审校段接入确定性引用门禁。

- 私有资料目录：`D:\blog-knowledge\documents`；SQLite 向量索引：`D:\blog-knowledge\index-bge-m3`
  （1024 维 BGE-M3 语义向量；另保留 `index` 作为 384 维词法哈希基线）。
- **语义检索评测（同 30 题 / 同语料 / 同分块，只换 embedding）**：Hit@1 0.111→**0.481**、
  Hit@5 0.370→**0.815**、MRR 0.200→0.577；易混类 0.000→0.750、跨语言 0.200→0.800。
  **注意不是全胜**：有 2 条回退、Hit@3 的 p≈0.057 不显著——完整口径与逐题翻转见 `rag/eval/README.md`。
- 增量索引支持 Markdown、TXT、文本型 PDF；稳定 `source_id/chunk_id`，删除文件会同步删向量。
- researcher 工具：`retrieve_private_knowledge`、`record_public_source`；reviewer 工具：
  `check_citations`。插件源码在 `plugins/private-rag`（默认 semantic + 本地 BGE-M3 + 强制 CPU）。
- 每个 run 新增 `citations.json`：`Ixx` 为私有来源，`Wxx` 为公开 URL + 本轮网页快照 +
  逐字证据。检索命中不等于已核实；版本/API/日期/性能/安全事实仍须官方来源或实测。
- writer 使用句末内联引用；reviewer 对 `final.md` 运行确定性引用检查，失败必须 block。
- **硬门禁**：插件注册 `pre_tool_call` hook，reviewer 卡 complete 时若本 run 同时存在
  `citations.json` 与 `final.md` 就自己跑校验，失败返回 `{"action":"block"}`（fail-closed）。

注意：门禁证明「溯源存在且自洽」，**不证明**被引证据在语义上支撑该断言——相关性仍由
reviewer 复核。详见 `rag/README.md` 的「这个门禁证明不了什么」。

安装、命令、引用规则和测试方法见 `rag/README.md`。
测试：`D:\blog-swarm\.venv-rag3\Scripts\python.exe -m pytest D:\blog-swarm\rag\tests D:\blog-swarm\tests -q`
