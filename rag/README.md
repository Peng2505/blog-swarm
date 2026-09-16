# Blog Private RAG

博客流水线第 2/5 调研段使用的私有知识库与引用溯源组件。

## 数据流

```text
D:\blog-knowledge\documents
  -> loader (.md/.txt/.pdf)
  -> heading/page-aware chunks
  -> embeddings
  -> SQLite persistent cosine index + manifest.json
  -> retrieve_private_knowledge
  -> D:\blog-runs\<run_id>\citations.json
```

索引是增量的：文档内容未变化时跳过；修改时替换该文档的旧 chunks；删除文档时同步删除向量。`source_id` 和 `chunk_id` 是确定性哈希，不使用随机 UUID。当前后端用 Python 标准库 SQLite 保存向量、在进程内算余弦相似度，适合几百到几千个 chunk 的本机私有库；规模明显增大后再替换为 Chroma/FAISS，不改变上层数据契约。

## 安装

在 Git Bash 中：

```bash
cd D:/blog-swarm
uv venv .venv-rag --python 3.11
uv pip install --python .venv-rag/Scripts/python.exe -e 'rag[dev]'
```

默认 `hashing` embedding 是零模型下载的确定性词法向量，保证离线可运行，适合测试和小型技术词库。需要中英语义检索时安装可选依赖：

```bash
uv pip install --python .venv-rag/Scripts/python.exe -e 'rag[semantic]'
```

然后在命令中使用 `--embedding semantic`。切换 embedding 会改变向量空间；必须删除 `D:\blog-knowledge\index` 后重建，程序会拒绝把两个模型写入同一索引。

## 命令

```bash
# 增量索引
D:/blog-swarm/.venv-rag/Scripts/python.exe -m private_rag.cli index \
  --source D:/blog-knowledge/documents \
  --db D:/blog-knowledge/index \
  --embedding hashing

# 检索并把命中登记为 Ixx 引用
D:/blog-swarm/.venv-rag/Scripts/python.exe -m private_rag.cli query \
  'Kanban 父子卡如何解锁' \
  --source D:/blog-knowledge/documents \
  --db D:/blog-knowledge/index \
  --ledger D:/blog-runs/<run_id>/citations.json

# 登记公开来源：evidence 必须逐字存在于已保存的网页快照
D:/blog-swarm/.venv-rag/Scripts/python.exe -m private_rag.cli add-public \
  --ledger D:/blog-runs/<run_id>/citations.json \
  --title '官方文档' --url 'https://example.com/docs' \
  --evidence '逐字证据' \
  --snapshot D:/blog-runs/<run_id>/evidence/web-docs.md

# 审核引用；失败返回非零退出码
D:/blog-swarm/.venv-rag/Scripts/python.exe -m private_rag.cli verify \
  --draft D:/blog-runs/<run_id>/final.md \
  --ledger D:/blog-runs/<run_id>/citations.json
```

所有命令 stdout 都是 JSON，便于 Hermes 插件稳定解析。

## 引用规则

- `Ixx`：私有资料，只能支撑“本项目实践/作者经验”，不能冒充公开事实。
- `Wxx`：公开 URL + 本轮抓取快照 + 逐字证据，可用于公共技术断言。
- 检索命中不等于已核实；API、版本、日期、性能、安全结论必须再查官方资料或本机实测。
- 引用编号只由 `citations.json` 分配，禁止模型自行编号。

## 门禁查什么（`check_citations` / `verify`）

失败即 `ok=false` 且退出码 2：

1. 正文引用编号必须存在于账本（未知编号 → 失败）。
2. 账本自身必须合法：编号不重复、`Ixx` 只能配 `kind=private`、`Wxx` 只能配 `kind=public`。
3. 每条被引用的来源必须带证据原文，且证据必须逐字存在于快照（私有按 run 内快照校验）。
4. 正文引用了来源就必须有 `## Sources`（也接受 `## 参考资料` / `## 参考文献`）。
5. Sources 里列出的编号必须在正文内联出现过（只堆在文末不算）。
6. 量化断言必须有引用。判定用的是 `QUANTITY_RE`：版本号、百分比、`N 倍/×`、金额、
   `N ms/秒/分钟`、以及 `N workers/threads/tokens/requests/users/qps/rps/并发/条/次/字符`。
   **裸整数（如「5 段」「2000 字」）不触发**，否则门禁会把每篇正常文章都判死。
7. 正文为空（只剩代码块/标题）→ 失败。

### 这个门禁证明不了什么（重要）

它证明的是**溯源存在、自洽、且绑定到真实检索语料**：

- 编号由账本分配、正文与 Sources 双向一致、证据逐字可回溯；
- `Ixx` 必须指向当前索引里真实存在的 chunk，且 chunk 内容哈希要匹配
  （手写 `chunk_id` / 改写证据都会失败）；
- 审校阶段（run 里已有 `03-draft.md`）**必须**同时产出 `citations.json` 与 `final.md`，
  少了任何一个都 block —— 删掉自己的交付物无法绕过门禁。

它**仍然不能**证明：

- 被引用的证据在语义上真的支撑那句话（「不相关断言 [I01]」仍能通过）——
  这需要语义判断，本组件不做，也不假装做；
- `Wxx` 所指网页的真实内容。它只校验「快照文件里逐字存在该证据」，
  而快照本身是被门禁的 agent 抓取并保存的；要封死需要抓取时记录正文哈希并由外部复核。

因此 reviewer 卡片仍必须人工/模型复核相关性与网页真实性，门禁负责把
「编编号、丢引用、伪造证据、无出处数字、删产物逃检」这类可确定性发现的问题挡死。

判定用的量化断言正则（`QUANTITY_RE`）**只认测量形态**：`v` 前缀版本号、百分比、
`N 倍`、金额、`N ms/秒/分钟`、`N workers/threads/tokens/requests/users/qps/rps/并发/条/字符`。
裸整数（`5 段`）、裸小数（`温度 0.2`）、无前缀版本（`langgraph 1.2.11`）**不触发**；
参考文献区（`## Sources` / `## 参考资料` / `## 参考文献` / `## 说明与参考` 等）整段跳过；
软换行的句子会先拼接再判定，避免引用落在下一行时误报。

已知边界（不要当成 bug 修，也不要当成能力吹）：

- 只有 **reviewer** profile 装了插件，所以硬门禁覆盖到「reviewer 卡收工」为止。
  reviewer 通过后 publisher 卡仍可能推错文件；要再加一道，需把插件也装到 publisher 并在
  `pre_tool_call` 里拦 `terminal` 的 `git push`（当前未做）。
- `citations.json` 的写入没有文件锁；同一个 run 不会有两个 researcher 并发，跨 run 无冲突，
  但并发调用同一账本会丢更新。
- 检索后端是 SQLite 全表扫描 + 进程内余弦，适合几百到几千 chunk；默认 embedding 是词法哈希。

## 测试

```bash
D:/blog-swarm/.venv-rag/Scripts/python.exe -m pytest D:/blog-swarm/rag/tests -q
D:/blog-swarm/.venv-rag/Scripts/python.exe -m pytest D:/blog-swarm/tests/test_private_rag_plugin.py -q
```

`manifest.json` 记录 `index_version`；索引格式升级后旧索引会拒绝同步，删掉
`D:\blog-knowledge\index` 重建即可（`source_id`/`chunk_id` 是内容哈希，重建后不变）。
