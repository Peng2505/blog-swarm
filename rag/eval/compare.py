#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两个评测结果的前后对比（逐题翻转 + 分面拆分）。

    <venv-rag3>/python.exe rag/eval/compare.py \
        --before eval/result-hashing.json --after eval/result-bge-m3.json

为什么单独写一个脚本，而不是直接看两份汇总：
  1. **汇总数字会掩盖翻转**。Hit@1 从 0.111 涨到 0.481 可能意味着"10 条新增命中、
     0 条回退"，也可能意味着"15 条新增、5 条回退"——后者说明模型在某些题上变差了，
     是要单独交代的。所以要逐题对齐算 gained / lost。
  2. **必须先校验两边题集一致**。实测踩过：`result-hashing.json` 曾是 20 题时代
     的遗留文件，而新结果是 30 题；直接对比会得出"提升 4 倍"这种假结论。
     所以这里题集不一致就 fail loud，不做静默 join。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ANSWERABLE_KEY = "hit@1"


def load_rows(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows")
    if not rows:
        raise SystemExit(f"{path} 里没有 rows")
    return str(data.get("embedding", "?")), rows


def answerable(rows: list[dict]) -> list[dict]:
    return [row for row in rows if ANSWERABLE_KEY in row]


def noanswer(rows: list[dict]) -> list[dict]:
    return [row for row in rows if ANSWERABLE_KEY not in row]


def metrics(rows: list[dict], k: int) -> dict:
    if not rows:
        return {"n": 0}
    count = len(rows)
    return {
        "n": count,
        "hit@1": sum(1 for r in rows if r["hit@1"]) / count,
        "hit@3": sum(1 for r in rows if r["hit@3"]) / count,
        f"hit@{k}": sum(1 for r in rows if r[f"hit@{k}"]) / count,
        "mrr": sum(r["rr"] for r in rows) / count,
    }


def flip_table(before: list[dict], after: list[dict], k: int) -> dict:
    left = {r["id"]: r for r in before}
    right = {r["id"]: r for r in after}
    gained, lost, kept_hit, kept_miss = [], [], [], []
    for ident in sorted(left):
        was = bool(left[ident][f"hit@{k}"])
        now = bool(right[ident][f"hit@{k}"])
        if not was and now:
            gained.append(ident)
        elif was and not now:
            lost.append(ident)
        elif was and now:
            kept_hit.append(ident)
        else:
            kept_miss.append(ident)
    return {"gained": gained, "lost": lost, "kept_hit": kept_hit, "kept_miss": kept_miss}


def by_field(rows: list[dict], field: str, k: int) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(field, "?")), []).append(row)
    return {name: metrics(items, k) for name, items in sorted(groups.items())}


def fmt(value: float) -> str:
    return f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测结果前后对比")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    k = args.top_k

    name_before, rows_before = load_rows(args.before)
    name_after, rows_after = load_rows(args.after)
    a_before, a_after = answerable(rows_before), answerable(rows_after)

    ids_before = {r["id"] for r in a_before}
    ids_after = {r["id"] for r in a_after}
    if ids_before != ids_after:
        print("题集不一致，拒绝对比（这会让结论失真）：")
        print(f"  仅 {args.before.name} 有: {sorted(ids_before - ids_after)}")
        print(f"  仅 {args.after.name} 有 : {sorted(ids_after - ids_before)}")
        return 2

    m_before, m_after = metrics(a_before, k), metrics(a_after, k)

    print(f"# 检索评测前后对比")
    print(f"\n- before: `{name_before}`（{args.before.name}）")
    print(f"- after : `{name_after}`（{args.after.name}）")
    print(f"- 可回答查询 {m_after['n']} 条（两边一致，已校验）\n")

    print("## 总体\n")
    print(f"| 指标 | {name_before} | {name_after} | 变化 |")
    print("|---|---|---|---|")
    for key, label in [("hit@1", "Hit@1"), ("hit@3", "Hit@3"), (f"hit@{k}", f"Hit@{k}"), ("mrr", "MRR")]:
        delta = m_after[key] - m_before[key]
        ratio = f"{m_after[key] / m_before[key]:.1f}×" if m_before[key] else "—"
        print(f"| {label} | {fmt(m_before[key])} | {fmt(m_after[key])} | {delta:+.3f}（{ratio}） |")

    print(f"\n## 逐题翻转（Hit@{k}）\n")
    table = flip_table(a_before, a_after, k)
    print(f"| | 条数 |")
    print("|---|---|")
    print(f"| 新增命中（miss→hit） | {len(table['gained'])} |")
    print(f"| **回退（hit→miss）** | **{len(table['lost'])}** |")
    print(f"| 一直命中 | {len(table['kept_hit'])} |")
    print(f"| 一直未命中 | {len(table['kept_miss'])} |")
    if table["gained"]:
        print(f"\n新增：{', '.join(table['gained'])}")
    if table["lost"]:
        print(f"\n**回退：{', '.join(table['lost'])}**  ← 这几条要单独交代")
    if table["kept_miss"]:
        print(f"\n两边都没命中（瓶颈可能不在 embedding）：{', '.join(table['kept_miss'])}")

    print("\n## 按类别\n")
    before_cat = by_field(a_before, "category", k)
    after_cat = by_field(a_after, "category", k)
    print(f"| 类别 | n | {name_before} Hit@1 | {name_after} Hit@1 | {name_before} MRR | {name_after} MRR |")
    print("|---|---|---|---|---|---|")
    for cat in sorted(set(before_cat) | set(after_cat)):
        b, a = before_cat.get(cat, {"n": 0}), after_cat.get(cat, {"n": 0})
        print(f"| {cat} | {a.get('n', b.get('n', 0))} | {fmt(b.get('hit@1', 0))} | {fmt(a.get('hit@1', 0))} "
              f"| {fmt(b.get('mrr', 0))} | {fmt(a.get('mrr', 0))} |")

    print("\n## 按语料家族\n")
    before_fam = by_field(a_before, "family", k)
    after_fam = by_field(a_after, "family", k)
    print(f"| 家族 | n | {name_before} Hit@1 | {name_after} Hit@1 | {name_before} Hit@5 | {name_after} Hit@5 |")
    print("|---|---|---|---|---|---|")
    for fam in sorted(set(before_fam) | set(after_fam)):
        b, a = before_fam.get(fam, {"n": 0}), after_fam.get(fam, {"n": 0})
        print(f"| {fam} | {a.get('n', b.get('n', 0))} | {fmt(b.get('hit@1', 0))} | {fmt(a.get('hit@1', 0))} "
              f"| {fmt(b.get(f'hit@{k}', 0))} | {fmt(a.get(f'hit@{k}', 0))} |")

    print("\n## 无答案查询：分数能否作为判据\n")
    for label, rows in ((name_before, rows_before), (name_after, rows_after)):
        noans = noanswer(rows)
        ans = answerable(rows)
        if not noans or not ans:
            continue
        noans_avg = sum(r["top1_score"] for r in noans) / len(noans)
        ans_avg = sum(r["top1_score"] for r in ans) / len(ans)
        floor = min(r["top1_score"] for r in ans)
        overlap = sum(1 for r in noans if r["top1_score"] >= floor)
        print(f"- **{label}**：无答案均分 {noans_avg:.3f} vs 有答案均分 {ans_avg:.3f}；"
              f"有 {overlap}/{len(noans)} 条无答案的分数 ≥ 有答案的最低分（{floor:.3f}）")
    print("\n→ 只要还有无答案查询分数落在有答案区间内，**就不能用相似度阈值判断\"库里没有\"**。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
