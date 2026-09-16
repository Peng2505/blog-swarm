"""检索评测：把「相关块」锚在内容子串上，而不是冻结的 chunk_id。

为什么不用 chunk_id 做标注：第 ④ 步一旦改分块参数，所有 chunk_id 都会变，
标注就整体失效。锚在内容上，重新分块后评测集依然可用，跑出来的数字才可比。

用法（在 D:\\blog-swarm\\rag 下）：
    ../.venv-rag/Scripts/python.exe eval/run_eval.py
    ../.venv-rag/Scripts/python.exe eval/run_eval.py --top-k 5 --json eval/result-hashing.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from private_rag.embeddings import HashingEmbedding, SentenceTransformerEmbedding  # noqa: E402
from private_rag.store import KnowledgeBase  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_QUERIES = EVAL_DIR / "queries.jsonl"
DEFAULT_DB = Path(r"D:\blog-knowledge\index")
DEFAULT_SOURCE = Path(r"D:\blog-knowledge\documents")


def load_queries(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def resolve_relevant(
    db_dir: Path,
    file_name: str,
    anchor: str | None = None,
    locator: str | None = None,
) -> list[str]:
    """标注 -> 当前索引里的相关 chunk_id 列表（内容来源，与排序无关）。

    两种标注方式：
    - `anchor`：正文子串。适合论文这类措辞独特的语料。
    - `locator`：章节路径前缀。官方文档汇编里同一个术语会在几十个章节出现，
      用子串标注会让相关集虚大到几百块、Hit@k 虚高，所以文档类必须用章节定位。
    """
    if (anchor is None) == (locator is None):
        raise ValueError("exactly one of anchor/locator must be provided")
    sqlite_files = sorted(Path(db_dir).glob("*.sqlite3"))
    if not sqlite_files:
        raise FileNotFoundError(f"no index found under {db_dir}")
    connection = sqlite3.connect(sqlite_files[0])
    try:
        if anchor is not None:
            rows = connection.execute(
                "SELECT chunk_id FROM chunks WHERE relative_path = ? AND text LIKE ?",
                (file_name, f"%{anchor}%"),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT chunk_id FROM chunks WHERE relative_path = ? AND locator LIKE ?",
                (file_name, f"{locator}%"),
            ).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def family_of(file_name: str) -> str:
    name = (file_name or "").lower()
    if name.endswith(".pdf"):
        return "论文"
    if "hermes" in name:
        return "官方文档"
    return "本机"


def hit_at_k(ranked: list[str], relevant: set[str], k: int) -> bool:
    return any(chunk_id in relevant for chunk_id in ranked[:k])


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for index, chunk_id in enumerate(ranked, start=1):
        if chunk_id in relevant:
            return 1.0 / index
    return 0.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="私有知识库检索评测")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--embedding", choices=["hashing", "semantic"], default="hashing")
    parser.add_argument("--model")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.embedding == "hashing":
        embedding = HashingEmbedding()
    else:
        embedding = SentenceTransformerEmbedding(args.model or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    kb = KnowledgeBase(args.db, embedding=embedding)
    queries = load_queries(args.queries)

    rows = []
    label_errors = []
    for item in queries:
        hits = kb.query(item["query"], top_k=args.top_k)
        ranked = [hit.chunk_id for hit in hits]
        top1_score = hits[0].score if hits else 0.0
        label = item.get("anchor") or item.get("locator")
        if label and item.get("file"):
            relevant = set(
                resolve_relevant(
                    args.db,
                    item["file"],
                    anchor=item.get("anchor"),
                    locator=item.get("locator"),
                )
            )
            if not relevant:
                kind = "锚点" if item.get("anchor") else "章节"
                label_errors.append(
                    f"{item['id']}: {kind} {label!r} 在 {item['file']} 中找不到，"
                    "标注失效（索引或文件已变）"
                )
                continue
            rows.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "family": family_of(item["file"]),
                    "query": item["query"],
                    "relevant_count": len(relevant),
                    "hit@1": hit_at_k(ranked, relevant, 1),
                    "hit@3": hit_at_k(ranked, relevant, 3),
                    f"hit@{args.top_k}": hit_at_k(ranked, relevant, args.top_k),
                    "rr": reciprocal_rank(ranked, relevant),
                    "top1_score": top1_score,
                    "top1_file": hits[0].relative_path if hits else None,
                    "top1_locator": hits[0].locator if hits else None,
                    "top1_is_relevant": bool(ranked and ranked[0] in relevant),
                }
            )
        else:
            rows.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "family": family_of(item.get("file") or ""),
                    "query": item["query"],
                    "relevant_count": 0,
                    "top1_score": top1_score,
                    "top1_file": hits[0].relative_path if hits else None,
                    "top1_locator": hits[0].locator if hits else None,
                }
            )

    if label_errors:
        print("标注校验失败，未出指标：", file=sys.stderr)
        for error in label_errors:
            print("  - " + error, file=sys.stderr)
        return 3

    answerable = [r for r in rows if r["relevant_count"]]
    no_answer = [r for r in rows if not r["relevant_count"]]

    print(f"embedding={embedding.name}  top_k={args.top_k}  查询数={len(rows)}")
    print()
    print(f"{'id':<5}{'类别':<13}{'hit@1':<7}{'hit@3':<7}{f'hit@{args.top_k}':<8}{'RR':<7}{'top1分':<9}{'top1命中':<9}来源")
    for r in answerable:
        print(
            f"{r['id']:<5}{r['category']:<13}"
            f"{str(r['hit@1']):<7}{str(r['hit@3']):<7}{str(r[f'hit@{args.top_k}']):<8}"
            f"{r['rr']:.2f}   {r['top1_score']:.3f}    "
            f"{str(r['top1_is_relevant']):<9}{r['top1_file']}:{r['top1_locator']}"
        )

    print()
    print("== 汇总（可回答查询）==")
    print(f"  数量            {len(answerable)}")
    for k in (1, 3, args.top_k):
        value = statistics.mean(1.0 if r[f"hit@{k}"] else 0.0 for r in answerable)
        print(f"  Hit@{k:<12}{value:.3f}")
    print(f"  MRR             {statistics.mean(r['rr'] for r in answerable):.3f}")
    by_cat = {}
    for r in answerable:
        by_cat.setdefault(r["category"], []).append(r)
    for category, items in sorted(by_cat.items()):
        h1 = statistics.mean(1.0 if r["hit@1"] else 0.0 for r in items)
        mrr = statistics.mean(r["rr"] for r in items)
        print(f"    {category:<12} n={len(items):<3} Hit@1={h1:.3f} MRR={mrr:.3f}")

    by_family: dict[str, list] = {}
    for r in answerable:
        by_family.setdefault(r["family"], []).append(r)
    print()
    print("== 按语料家族拆分（语料构成不均衡时，总分掩盖某一族的表现）==")
    for fam, items in sorted(by_family.items()):
        h1 = statistics.mean(1.0 if r["hit@1"] else 0.0 for r in items)
        h5 = statistics.mean(1.0 if r[f"hit@{args.top_k}"] else 0.0 for r in items)
        mrr = statistics.mean(r["rr"] for r in items)
        print(
            f"    {fam:<10} n={len(items):<3} Hit@1={h1:.3f} "
            f"Hit@{args.top_k}={h5:.3f} MRR={mrr:.3f}"
        )

    print()
    print("== 无答案查询（考察误命中）==")
    for r in no_answer:
        print(
            f"  {r['id']}  top1={r['top1_score']:.3f}  {r['top1_file']}:{r['top1_locator']}"
        )
    if no_answer and answerable:
        na = statistics.mean(r["top1_score"] for r in no_answer)
        an = statistics.mean(r["top1_score"] for r in answerable)
        print()
        print(f"  无答案平均 top1 分 {na:.3f} vs 有答案 {an:.3f}")
        overlap = sum(1 for r in no_answer if r["top1_score"] >= min(x["top1_score"] for x in answerable))
        print(f"  有 {overlap}/{len(no_answer)} 条无答案查询的分数 ≥ 有答案查询的最低分 —— 分数无法单独用来判断'库里没有'")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {"embedding": embedding.name, "top_k": args.top_k, "rows": rows},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n明细已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
