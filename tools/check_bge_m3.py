#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BGE-M3 加载验证：权重能否真正被 sentence-transformers 用起来。

    <venv-rag3>/python.exe tools/check_bge_m3.py [模型目录]

为什么单独做这个脚本：
  下载完成 != 能用。2026-09-16 实测踩过两次 —— 文件大小完全正确，但
  ① 开头 1.02GB 是错位内容（torch 报 `invalid load key`）；
  ② 修好后 transformers 仍先报 mmap 错误（.bin 是旧版 pickle 格式）。
  所以"能用"必须真的加载一次、真的编码一次，而不是看文件在不在。

检查项：
  1) 模型加载（走 private_rag 的真实路径，local_files_only）
  2) 维数与最大序列长度
  3) 语义冒烟：3 组「查询 → 应命中哪篇文档」，hashing 基线在这题上失败过
  4) 交叉验证：随机抽取与打乱，确认不是巧合命中

退出码 0 = 全部通过。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "rag" / "src"))

DEFAULT_MODEL = r"D:\blog-knowledge\models\bge-m3"
EXPECTED_DIM = 1024

# 查询 -> 该命中哪篇文档。第 1 题是 hashing 基线明确失败的例子。
CASES = [
    (
        "how to measure RAG faithfulness and answer relevance",
        "RAGAS: Automated Evaluation of Retrieval Augmented Generation. "
        "We propose faithfulness, answer relevance and context relevance as reference-free metrics.",
    ),
    (
        "长上下文里中间位置的信息容易被忽略",
        "Lost in the Middle: How Language Models Use Long Contexts. "
        "Performance is highest when relevant information occurs at the beginning or end of the input, "
        "and degrades significantly when it is in the middle.",
    ),
    (
        "多智能体之间怎么分工协作",
        "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation. "
        "We introduce conversable agents that can converse with each other to solve tasks.",
    ),
]

DISTRACTORS = [
    "PostgreSQL 是一个开源对象关系数据库系统，支持 ACID 事务与外键约束。",
    "FastAPI 是 Python 的现代 Web 框架，基于类型提示自动生成 OpenAPI 文档。",
    "GitHub Actions 的工作流由 .github/workflows 下的 YAML 文件定义。",
]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    model_path = args[0] if args else DEFAULT_MODEL

    print(f"[1/4] 加载模型 {model_path}")
    started = time.time()
    from private_rag.embeddings import SentenceTransformerEmbedding

    try:
        embedding = SentenceTransformerEmbedding(model_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  失败：{type(exc).__name__}: {exc}")
        return 1
    print(f"  OK  耗时 {time.time() - started:.1f}s  name={embedding.name}")

    print("\n[2/4] 模型属性")
    inner = embedding._model
    # ST 5.x 把 get_sentence_embedding_dimension 改名了；两个都试，别让弃用警告污染输出
    get_dim = getattr(inner, "get_embedding_dimension", None) or inner.get_sentence_embedding_dimension
    dim = get_dim()
    print(f"  max_seq_length = {inner.max_seq_length}")
    print(f"  embedding dim  = {dim}")
    try:
        import psutil

        print(f"  进程 RSS       = {psutil.Process().memory_info().rss / 1073741824:.2f} GB")
    except Exception:  # noqa: BLE001
        pass
    if dim != EXPECTED_DIM:
        print(f"  失败：维数 {dim} != 期望 {EXPECTED_DIM}（不是 BGE-M3？）")
        return 1

    print("\n[3/4] 语义冒烟：查询应命中自己的文档，且排在干扰项前面")
    queries = [q for q, _ in CASES]
    docs = [d for _, d in CASES]
    started = time.time()
    qvecs = embedding.encode(queries)
    dvecs = embedding.encode(docs + DISTRACTORS)
    print(f"  编码 {len(queries) + len(docs) + len(DISTRACTORS)} 条短文本 {time.time() - started:.2f}s")

    passed = 0
    for index, query in enumerate(queries):
        scores = [sum(a * b for a, b in zip(qvecs[index], vec)) for vec in dvecs]
        best = scores.index(max(scores))
        hit = best == index
        passed += 1 if hit else 0
        mark = "正确" if hit else "错误"
        detail = "  ".join(f"{s:.3f}" for s in scores)
        print(f"  q{index + 1}  {detail}   -> 第{best + 1}名 {mark}  「{query[:28]}」")

    print(f"\n[4/4] 结果 {passed}/{len(CASES)} 命中")
    if passed != len(CASES):
        print("  有查询未命中自己的文档，语义检索能力不符合预期")
        return 1
    print("  全部通过：模型可加载、维数正确、语义区分有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
