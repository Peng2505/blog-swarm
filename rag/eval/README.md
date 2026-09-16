# 检索评测（私有知识库）

先用尺子量，再谈优化。当前语料：8 篇 arXiv 论文 + Hermes 官方文档汇编（`hermes-docs-full.txt`）
+ 本机流水线说明，共 **7539 个 chunk**、11 个源文件。

```bash
cd D:/blog-swarm/rag
PY=../.venv-rag3/Scripts/python.exe

# 基线：词法哈希（384 维）
$PY eval/run_eval.py --db D:/blog-knowledge/index --embedding hashing \
    --json eval/result-hashing.json

# 语义：BGE-M3（1024 维，本地权重）
$PY eval/run_eval.py --db D:/blog-knowledge/index-bge-m3 \
    --embedding semantic --model D:/blog-knowledge/models/bge-m3 \
    --json eval/result-bge-m3.json

# 前后对比（逐题翻转 + 分面拆分）
$PY eval/compare.py --before eval/result-hashing.json --after eval/result-bge-m3.json
```

## 结果：hashing-384 → BGE-M3

**同一套 30 题（27 可回答 + 3 无答案）、同一份语料、同一套分块**，只换 embedding。

### 总体

| 指标 | hashing-384 | BGE-M3（1024 维） | 变化 |
|---|---|---|---|
| Hit@1 | 0.111 | **0.481** | +0.370（4.3×） |
| Hit@3 | 0.296 | 0.593 | +0.296（2.0×） |
| Hit@5 | 0.370 | **0.815** | +0.444（2.2×） |
| MRR | 0.200 | 0.577 | +0.377（2.9×） |

### 逐题翻转 —— 涨得多，但不是全胜

| 级别 | 新增命中 | **回退** | 翻转总数 | 符号检验 |
|---|---|---|---|---|
| Hit@1 | 11 | **1** | 12 | p ≈ 0.006 |
| Hit@3 | 11 | 3 | 14 | p ≈ 0.057 |
| Hit@5 | 14 | **2** | 16 | p ≈ 0.004 |

Hit@1 与 Hit@5 的提升在统计上站得住（p<0.01）；**Hit@3 处于边缘（p≈0.057），不宣称显著**。

**回退的题要单独交代**：

- **q25**「How do I write a plugin manifest?」——三个级别全丢。hashing 用 0.377 的**低分精确命中**
  `Build a Hermes Plugin > Step 2: Write the manifest`；BGE-M3 用 0.664 的**高分命中**
  `Extending the Dashboard > Plugins > Quick start`。语义模型把主题相近的段落排到了前面，
  而词法模型吃到了 `manifest` 这个字面词。
- **q10 / q23**：只在 Hit@3 掉出，Hit@5 仍在。

→ 正确的结论不是"语义全面碾压"，而是：**语义模型赢在跨语言、同义改写、易混区分；
当查询依赖专有名词精确命中时，词法匹配反而更准。**

### 按类别

| 类别 | n | hashing Hit@1 | BGE-M3 Hit@1 | hashing MRR | BGE-M3 MRR |
|---|---|---|---|---|---|
| confusable（易混） | 4 | **0.000** | **0.750** | 0.250 | 0.750 |
| crosslingual（跨语言） | 5 | 0.200 | 0.800 | 0.240 | 0.867 |
| direct（直问） | 10 | 0.100 | 0.400 | 0.153 | 0.528 |
| paraphrase（改写） | 8 | 0.125 | **0.250** | 0.208 | 0.369 |

四个类别全部改善。`confusable` 从 0.000 到 0.750 是最直接的证据 —— 词法哈希在那类上**一条都没中**。

**但 `paraphrase` 仍是四类里最弱的（0.250）**，留作后续瓶颈方向。

### 按语料家族

| 家族 | n | hashing Hit@1 | BGE-M3 Hit@1 | hashing Hit@5 | BGE-M3 Hit@5 |
|---|---|---|---|---|---|
| 官方文档 | 10 | 0.100 | 0.300 | 0.300 | **0.900** |
| 论文 | 16 | 0.062 | **0.562** | 0.375 | 0.750 |
| 本机 | 1 | 1.000 | 1.000 | 1.000 | 1.000 |

（「本机」n=1，无统计意义，列出只为完整。）

### 无答案查询：分数仍然不能当判据

| embedding | 无答案均分 | 有答案均分 | 无答案分数落在有答案区间内的条数 |
|---|---|---|---|
| hashing-384 | 0.461 | 0.452 | 3/3 |
| BGE-M3 | 0.611 | **0.663** | 2/3 |

换语义模型后**方向对了**（无答案均分开始低于有答案），但仍有 2/3 条无答案查询的分数
高于有答案查询的最低分。**结论不变：不能用相似度阈值判断"知识库里没有"。**
`retrieve_private_knowledge` 的返回里没有"拒答"信号，这是设计而非缺陷 ——
必须让模型看到命中内容后自己判断。

### 两边都没命中的 3 条 —— 瓶颈可能不在 embedding

| id | 查询 | 两边 top1 去向 |
|---|---|---|
| q06 | Why does the position of the relevant passage inside a long context change accuracy? | 都是 `2307.03172 Page 5`（正是 Lost in the Middle 那篇） |
| q08 | What statistical tools does the paper recommend so you can tell a real improvement from noise? | 都落到 Hermes 文档 |
| q17 | What lets an agent keep working beyond its context window by managing its own memory? | 都落到 Hermes 文档 |

q06 尤其值得注意：**两个模型都检索到了正确的那篇论文、落在正确页码区间，但不在标注的相关集里** ——
说明标注粒度比"找到这篇论文"窄。这指向**标注粒度或分块**，不是 embedding。
q17 的标注相关集只有 1 块，同样偏窄。

## 尺子本身怎么保证准

- **对比前必须校验两边题集一致**：`compare.py` 内置这个守卫，题集不一致直接 exit 2。
  实测踩过 —— 磁盘上的 `result-hashing.json` 一度是 **20 题时代**的遗留文件，而新结果是 30 题；
  直接对比会得出"提升 4 倍"这种假结论，本质是拿旧尺子量新东西。
  （旧文件留档为 `result-hashing-20q-old.json`，标注为不可比。）
- **标注锚在内容上，不冻结 chunk_id**：改成边界感知切分后所有 chunk_id 都变了，
  这份评测集**依然可用**，前后数字才可比。
- **两种标注方式**：
  - `anchor`：正文子串 —— 适合论文（措辞独特）。实测 `U-shaped`、`virtual context management` 都能精确定位。
  - `locator`：章节路径前缀 —— 官方文档汇编里同一个术语会出现在几十个章节
    （实测 `HERMES_KANBAN_TASK` 命中 17 块 / 11 节、`opt-in` 命中 134 块 / 110 节），
    用子串标注会让相关集虚大到几百块、Hit@k 虚高，所以文档类必须用章节定位。
  - 两者必须且只能给一个，否则 `resolve_relevant` 直接报错。
- **标注失效会 fail loud**：解析不到任何块就退出（exit=3），不出指标。
  真跑出过一次 —— `五段流水线` 是标题，而标题文字不进入 chunk 正文。
- 尺子自身测试：`tests/test_eval_harness.py`；全部 rag 测试 **134 条**。

## 这些数字不能拿来吹的地方

1. **相关集是下界**：只算标注本身命中的块。同一事实在别处出现但未计入 →
   Hit@k 偏保守（对系统不利），不是偏宽松。
2. **样本 27+3 条**：一条翻转约 ±3.7 个百分点。所以**不要比两个汇总数字的大小**，
   要靠逐题翻转 + 符号检验支撑结论。也正因如此，本页写「提升 4.3 倍」时必须同时给出
   涨幅/回退条数和 p 值。
3. **只看检索到正确的块，不看答案对不对**：能否支撑断言是引用门禁和 reviewer 的事。
4. **查询分布 vs 语料分布仍不相等**：语料 91% 文档（`hermes-docs-full.txt` 一项就占 88% 的 token），
   查询 37% 文档。所以要用家族拆分看，不能只看总分。
5. **「4.3 倍」只在本次语料 + 本次评测集上成立**，不是通用结论；换语料要重量。
6. **索引是用 CPU 编码建的**（实测 2.6 块/秒，7539 块约 49 分钟）。本机有 RTX 4060，
   试过切 CUDA（`torch 2.14.0+cu130`，编码 30 块/秒）但**在连续编码约 4 分钟时 GPU 触发
   TDR 崩溃**（`GPU is lost, reboot the system to recover`）。所以交付走 CPU 编码；
   流水线插件默认强制 CPU（显式 `BLOG_RAG_DEVICE=cuda` 才放开）。若日后 GPU 稳定并重建索引，
   浮点内核不同、向量末位会变，本页数字必须重跑才自洽。

## 历史记录（不可与上表直接比较）

| 版本 | 语料 | embedding | Hit@1 | Hit@3 | Hit@5 | MRR | 备注 |
|---|---|---|---|---|---|---|---|
| v1 | 687 块（无文档汇编、旧切分） | hashing-384 | 0.118 | 0.471 | 0.588 | 0.304 | 语料与切分均已变，**仅作历史** |
| v3 | 7539 块 | hashing-384 | 0.111 | 0.296 | 0.370 | 0.200 | 当前基线 |
| **v4** | 7539 块 | **BGE-M3（1024 维）** | **0.481** | 0.593 | **0.815** | 0.577 | **当前** |
| — | —— | hashing-384 | — | — | — | — | `result-hashing-20q-old.json`：20 题时代遗留，**题集不同不可比** |

v1 → v3 数字下降**不是回归**：语料多了 4.7MB 官方文档，而查询当时几乎只问论文。
这暴露了评测集本身的设计要求：**查询分布要覆盖语料的主要家族**，v3 已补入 10 条文档类查询。

## 下一步

1. ~~把语义 embedding 接成流水线默认~~ **已完成**：插件 v1.4.0 默认 semantic + 本地 BGE-M3 +
   `index-bge-m3`，并默认强制 CPU（显式 `BLOG_RAG_DEVICE=cuda` 才放开 GPU）。
2. ~~改用 CUDA 重建索引~~ **暂缓**：本机 RTX 4060 在连续编码约 4 分钟时触发 TDR 崩溃
   （`GPU is lost`，需重启恢复）。重启后若 GPU 稳定再试；**重建后本页数字需全部重跑**。
3. **瓶颈方向**：`paraphrase` 仍只有 0.250；q06/q08/q17 两边都失败，指向标注粒度与分块，
   而非 embedding。要动的话先改这两处、再重量。
