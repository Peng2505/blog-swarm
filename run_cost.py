#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 run 汇总博客流水线的 token / 耗时 / 成本（成本可观测）。

背景（为什么必须这么写）：
  * 每个 worker 由 dispatcher 用 `HERMES_HOME=<profiles/<name>>` 拉起，
    它的会话与用量记在**该 profile 自己的 state.db** 里，不在 default 的 state.db。
    → 单篇成本 = 跨 6 个库（default + 5 个 worker profile）求和。
  * `publisher` profile 的库里还混着定时任务的会话（技术博客选题流水线 cron），
    所以**必须按卡的时间窗过滤**，不能整库求和。
  * worker 跑的是 `deepseek-v4-flash-vision-exp`，Hermes 价目表里没有这个 model
    （`agent/usage_pricing.py`），落库时 estimated_cost_usd=0 / cost_status='unknown'。
    → 这部分用别名价单独标为「估算」，不与实测值相加成假精确。

用法：
    python run_cost.py 20260916-kanban-swarm [--write] [--comment] [--json]
    python run_cost.py --all [--write]
    python run_cost.py <run_id> --root t_ea151969      # 手建根卡（无 [run_id] 前缀）时补上

输出：
    终端 markdown 表；--write 落 <run_dir>/05-cost.json + 05-cost.md；
    --comment 把每段一行成本评论到对应卡上（`hermes kanban show <id>` 可见）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 路径解析

def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "hermes"


HOME = hermes_home()
RUN_ROOT = Path(os.environ.get("BLOG_RUN_ROOT") or r"D:\blog-swarm\runs")
if not RUN_ROOT.exists() and Path(r"D:\blog-runs").exists():
    RUN_ROOT = Path(r"D:\blog-runs")

# Hermes 自己的价目/计费模块（复用，避免自造价目表）
PRICING_DIR = HOME / "hermes-agent"
if str(PRICING_DIR) not in sys.path:
    sys.path.insert(0, str(PRICING_DIR))
try:
    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost  # type: ignore
except Exception as exc:  # noqa: BLE001
    CanonicalUsage = None  # type: ignore
    estimate_usage_cost = None  # type: ignore
    PRICING_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    PRICING_IMPORT_ERROR = ""

# 价目表里没有的 model → 官方/服务端别名，用于「估算」档
MODEL_ALIAS = {
    "deepseek-v4-flash-vision-exp": "deepseek-v4-flash",
    "deepseek-flash": "deepseek-v4-flash",
}
ALIAS_NOTE = (
    "别名估算：{raw} 不在 Hermes 价目表内，按 {alias} 官方价目计算"
    "（api-docs.deepseek.com/quick_start/pricing，deepseek-pricing-2026-07）"
)

# 会话匹配容差：卡开始前 / 结束后，允许 worker 会话落在多宽的窗口里
SLACK_BEFORE = 180
SLACK_AFTER = 3600


def board_db(board: str) -> Path:
    env = os.environ.get("HERMES_KANBAN_DB")
    if env and Path(env).exists():
        return Path(env)
    return HOME / "kanban" / "boards" / board / "kanban.db"


def profile_db(profile: str) -> Path | None:
    if profile in ("", "default"):
        p = HOME / "state.db"
    else:
        p = HOME / "profiles" / profile / "state.db"
    return p if p.exists() else None


def ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def fmt_ts(x) -> str:
    return datetime.fromtimestamp(x).strftime("%H:%M:%S") if x else "-"


# ---------------------------------------------------------------- 卡与段

def load_cards(db: Path, run_id: str, root: str | None) -> list[dict]:
    con = ro(db)
    rows = list(con.execute(
        "select id,title,assignee,status,idempotency_key,created_at,started_at,"
        "completed_at,workspace_path from tasks where idempotency_key like ? "
        "or title like ? order by created_at",
        (f"blog:{run_id}:%", f"[{run_id}] %"),
    ))
    cards = [dict(r) for r in rows]
    if root:
        for r in con.execute("select id,title,assignee,status,idempotency_key,created_at,"
                             "started_at,completed_at,workspace_path from tasks where id=?", (root,)):
            if all(c["id"] != r["id"] for c in cards):
                cards.append(dict(r))
    # 手建根卡（无 [run_id] 前缀 / 无幂等键）不在上面的匹配里 —— 顺着 task_links
    # 找最早一段的父卡补进来，否则会漏掉 orchestrator 段的成本。
    if cards and not any((c["idempotency_key"] or "").endswith(":1-decompose") for c in cards):
        first = min(cards, key=lambda c: c["created_at"])
        for r in con.execute(
                "select t.id,t.title,t.assignee,t.status,t.idempotency_key,t.created_at,"
                "t.started_at,t.completed_at,t.workspace_path from task_links l "
                "join tasks t on t.id=l.parent_id where l.child_id=?", (first["id"],)):
            if all(c["id"] != r["id"] for c in cards):
                cards.append(dict(r))
    for c in cards:
        runs = list(con.execute(
            "select id,profile,status,started_at,ended_at,outcome from task_runs "
            "where task_id=? order by started_at desc", (c["id"],)))
        c["_runs"] = [dict(r) for r in runs]
        key = (c["idempotency_key"] or "")
        stage = key.rsplit(":", 1)[-1] if key.startswith(f"blog:{run_id}:") else ""
        if not stage:
            m = re.search(r"(\d/5)", c["title"] or "")
            stage = m.group(1) if m else ("root(手建)" if not key else key.rsplit(":", 1)[-1])
        c["_stage"] = stage
    con.close()
    cards.sort(key=lambda c: (c["created_at"], c["id"]))
    return cards


def stage_window(card: dict) -> tuple[float | None, float | None, str | None]:
    """段的墙钟窗口 = task_runs 里**真正执行过**的那次（done / running）。

    刻意不把 blocked/parked/released 的 run 当窗口：`--park` 出来的 run 时间戳
    落在几十分钟的真空里，会误匹配到**别的卡**的 worker 会话，把成本算到自己头上。
    """
    runs = [r for r in card["_runs"] if r["status"] in ("done", "running")]
    profile = card["assignee"] or "default"
    if not runs:
        return None, None, profile
    r = runs[0]
    return r["started_at"], r["ended_at"], r["profile"] or profile


def match_session(profile: str, w0: float | None, w1: float | None) -> dict:
    """在该 profile 的 state.db 里按时间窗找 source='kanban' 的 worker 会话。"""
    out = {"profile": profile, "session_id": None, "candidates": 0, "model": None,
           "started_at": None, "last_activity_at": None, "note": ""}
    db = profile_db(profile)
    if not db:
        out["note"] = f"找不到 profile 库：{profile}"
        return out
    if not w0:
        out["note"] = "卡没有执行时间窗（未真正跑过：blocked/parked/未领取）"
        return out
    lo, hi = w0 - SLACK_BEFORE, (w1 or w0) + SLACK_AFTER
    con = ro(db)
    rows = [dict(r) for r in con.execute(
        "select id,model,started_at,last_activity_at,message_count from sessions "
        "where source='kanban' and started_at between ? and ? order by started_at",
        (lo, hi))]
    con.close()
    out["candidates"] = len(rows)
    if not rows:
        out["note"] = f"窗口内没有 source='kanban' 会话（{profile}）"
        return out
    if len(rows) > 1:
        out["note"] = f"窗口内有 {len(rows)} 个 kanban 会话，取最接近卡开始时间的那个"
    best = min(rows, key=lambda r: abs(r["started_at"] - w0))
    out.update({"session_id": best["id"], "model": best["model"],
                "started_at": best["started_at"], "last_activity_at": best["last_activity_at"]})
    return out


# ---------------------------------------------------------------- 成本

def price_rows(rows: list[dict]) -> dict:
    """一行 = session_model_usage 的一条记录。实测值优先，别名估算单独累计。"""
    agg = {"measured_usd": 0.0, "alias_usd": 0.0, "measured_rows": 0,
           "alias_rows": 0, "unpriced_rows": 0, "notes": [], "by_model": {}}
    for r in rows:
        model = r["model"] or "?"
        tokens = CanonicalUsage(
            input_tokens=r["input_tokens"] or 0,
            output_tokens=r["output_tokens"] or 0,
            cache_read_tokens=r["cache_read_tokens"] or 0,
            cache_write_tokens=r["cache_write_tokens"] or 0,
            request_count=r["api_call_count"] or 1,
        ) if CanonicalUsage else None
        slot = agg["by_model"].setdefault(model, {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "measured_usd": 0.0, "alias_usd": 0.0, "pricing": ""})
        slot["calls"] += r["api_call_count"] or 0
        slot["input_tokens"] += r["input_tokens"] or 0
        slot["output_tokens"] += r["output_tokens"] or 0
        slot["cache_read_tokens"] += r["cache_read_tokens"] or 0

        status = r["cost_status"]
        stored = r["estimated_cost_usd"] or 0.0
        if status in ("estimated", "included"):
            slot["measured_usd"] += stored
            slot["pricing"] = "measured"
            agg["measured_usd"] += stored
            agg["measured_rows"] += 1
            continue
        # 未计价：先用别名重算
        alias = MODEL_ALIAS.get(model)
        amount = None
        if tokens and estimate_usage_cost and alias:
            res = estimate_usage_cost(alias, tokens, provider=r["billing_provider"] or "deepseek")
            if res.amount_usd is not None:
                amount = float(res.amount_usd)
        if amount is None:
            agg["unpriced_rows"] += 1
            slot["pricing"] = slot["pricing"] or "unpriced"
            continue
        slot["alias_usd"] += amount
        slot["pricing"] = "alias"
        agg["alias_usd"] += amount
        agg["alias_rows"] += 1
        note = ALIAS_NOTE.format(raw=model, alias=alias)
        if note not in agg["notes"]:
            agg["notes"].append(note)
    return agg


def usage_for(session_id: str, profile: str) -> list[dict]:
    db = profile_db(profile)
    if not db or not session_id:
        return []
    con = ro(db)
    rows = [dict(r) for r in con.execute(
        "select model,task,billing_provider,api_call_count,input_tokens,output_tokens,"
        "cache_read_tokens,cache_write_tokens,estimated_cost_usd,cost_status,cost_source "
        "from session_model_usage where session_id=?", (session_id,))]
    con.close()
    return rows


# ---------------------------------------------------------------- 组装

def build_report(run_id: str, board: str, root: str | None, fx: float) -> dict:
    db = board_db(board)
    if not db.exists():
        raise SystemExit(f"找不到看板库：{db}")
    cards = load_cards(db, run_id, root)
    if not cards:
        raise SystemExit(f"run_id={run_id} 没有匹配到任何卡（board={board}）")

    stages, warnings = [], []
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
           "measured_usd": 0.0, "alias_usd": 0.0, "wall_seconds": 0.0}
    for c in cards:
        w0, w1, prof = stage_window(c)
        prof = prof or c["assignee"] or "default"
        sess = match_session(prof, w0, w1)
        rows = usage_for(sess["session_id"], prof)
        price = price_rows(rows)
        wall = max((w1 or w0 or 0) - (w0 or 0), 0)
        stage = {
            "stage": c["_stage"], "card": c["id"], "title": c["title"],
            "profile": prof, "status": c["status"],
            "window": [fmt_ts(w0), fmt_ts(w1)], "wall_seconds": round(wall, 1),
            "session_id": sess["session_id"], "model": sess["model"],
            "session_candidates": sess["candidates"],
            "calls": sum(r["api_call_count"] or 0 for r in rows),
            "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
            "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
            "cache_read_tokens": sum(r["cache_read_tokens"] or 0 for r in rows),
            "measured_usd": round(price["measured_usd"], 6),
            "alias_usd": round(price["alias_usd"], 6),
            "by_model": price["by_model"],
            "notes": ([sess["note"]] if sess["note"] else []) + price["notes"],
        }
        stage["cost_usd"] = round(stage["measured_usd"] + stage["alias_usd"], 6)
        if not sess["session_id"]:
            warnings.append(f"{stage['stage']}（{prof}）未匹配到 worker 会话 → 该段成本缺失")
        for k in ("calls", "input_tokens", "output_tokens", "cache_read_tokens"):
            tot[k] += stage[k]
        tot["measured_usd"] += stage["measured_usd"]
        tot["alias_usd"] += stage["alias_usd"]
        tot["wall_seconds"] += stage["wall_seconds"]
        stages.append(stage)

    total_cost = tot["measured_usd"] + tot["alias_usd"]
    report = {
        "run_id": run_id, "board": board, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(RUN_ROOT / run_id),
        "stages": stages,
        "totals": dict(tot, cost_usd=round(total_cost, 6),
                       cost_cny=round(total_cost * fx, 4), fx=fx,
                       unpriced_stages=sum(1 for s in stages if not s["session_id"])),
        "warnings": warnings,
        "caveats": [
            "cost_usd = Hermes 实测(measured) + 别名估算(alias)；后者来自价目表别名，"
            "与供应商账单可能不一致，报数时分开说。",
            "最后一段（publisher）若在跑完后立即汇总，其自身会话用量可能尚未完全落库 → 该段偏低。",
            "wall_seconds 是卡的墙钟窗口（含模型延迟与排队），不等于模型计算时间。",
        ],
    }
    return report


def render_md(rep: dict) -> str:
    t = rep["totals"]
    lines = [f"# 成本与耗时 · run {rep['run_id']}", "",
             f"生成时间：{rep['generated_at']}　运行目录：`{rep['run_dir']}`", "",
             "| 段 | profile | 模型 | 墙钟 | API 调用 | 输入 tok | 输出 tok | 缓存读 tok | 成本 USD |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in rep["stages"]:
        w = s["wall_seconds"]
        lines.append(
            f"| {s['stage']} | {s['profile']} | {s['model'] or '-'} | {w/60:.1f} min | {s['calls']} | "
            f"{s['input_tokens']:,} | {s['output_tokens']:,} | {s['cache_read_tokens']:,} | "
            f"{s['cost_usd']:.4f} |")
    lines += ["",
              f"**合计**：{t['wall_seconds']/60:.1f} min　{t['calls']} 次调用　"
              f"输入 {t['input_tokens']:,} / 输出 {t['output_tokens']:,} / 缓存读 {t['cache_read_tokens']:,} tok　"
              f"**${t['cost_usd']:.4f}**（≈¥{t['cost_cny']:.2f}，按 1USD={t['fx']}CNY 粗略换算）",
              "",
              f"其中实测 ${t['measured_usd']:.4f} + 别名估算 ${t['alias_usd']:.4f}"
              f"（估算部分占 {t['alias_usd']/t['cost_usd']*100 if t['cost_usd'] else 0:.0f}%）", ""]
    notes = []
    for s in rep["stages"]:
        for n in s["notes"]:
            if n not in notes:
                notes.append(n)
    if notes:
        lines += ["## 口径说明"] + [f"- {n}" for n in notes] + [""]
    if rep["warnings"]:
        lines += ["## 缺口"] + [f"- {w}" for w in rep["warnings"]] + [""]
    lines += ["## 报数须知"] + [f"- {c}" for c in rep["caveats"]]
    return "\n".join(lines) + "\n"


def render_console(rep: dict) -> str:
    t = rep["totals"]
    out = [f"run {rep['run_id']}　{t['wall_seconds']/60:.1f} min　"
           f"${t['cost_usd']:.4f}（实测 ${t['measured_usd']:.4f} + 估算 ${t['alias_usd']:.4f}）"]
    out.append(f"{'段':<22}{'profile':<13}{'分钟':>7}{'调用':>6}{'输入':>10}{'输出':>9}{'USD':>10}")
    for s in rep["stages"]:
        out.append(f"{s['stage']:<22}{s['profile']:<13}{s['wall_seconds']/60:>7.1f}"
                   f"{s['calls']:>6}{s['input_tokens']:>10,}{s['output_tokens']:>9,}"
                   f"{s['cost_usd']:>10.4f}")
    for w in rep["warnings"]:
        out.append(f"⚠ {w}")
    return "\n".join(out)


def comment_cards(rep: dict, board: str) -> None:
    """把每段成本评论到卡上；已有同样评论则跳过（幂等，供 cron 反复调用）。"""
    import subprocess
    db = board_db(board)
    con = ro(db)
    for s in rep["stages"]:
        if s["status"] != "done":
            print(f"  · comment {s['card']}: 卡状态={s['status']}，跑完再评论（避免写进半截数字）")
            continue
        already = con.execute(
            "select 1 from task_comments where task_id=? and body like '成本：本段%' limit 1",
            (s["card"],)).fetchone()
        if already:
            print(f"  · comment {s['card']}: 已有成本评论，跳过")
            continue
        line = (f"成本：本段 {s['wall_seconds']/60:.1f} min / {s['calls']} 次调用 / "
                f"in {s['input_tokens']:,} out {s['output_tokens']:,} / ${s['cost_usd']:.4f}"
                f"（模型 {s['model'] or '-'}；run 合计 ${rep['totals']['cost_usd']:.4f}，"
                f"明细 {rep['run_dir']}\\05-cost.json）")
        p = subprocess.run(["hermes", "kanban", "--board", board, "comment", s["card"], line],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(("  ✓ " if p.returncode == 0 else "  ✗ ") + f"comment {s['card']}: "
              + ((p.stdout or p.stderr).strip()[:90]))
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="按 run 汇总流水线成本")
    ap.add_argument("run_id", nargs="?", help="run id，例如 20260916-kanban-swarm")
    ap.add_argument("--all", action="store_true", help="汇总所有 run")
    ap.add_argument("--include-archived", action="store_true",
                    help="--all 时也纳入卡已归档的历史 run（测试残留）")
    ap.add_argument("--board", default="blog")
    ap.add_argument("--root", default=None, help="额外纳入的卡 id（手建根卡）")
    ap.add_argument("--write", action="store_true", help="写 05-cost.json / 05-cost.md 到运行目录")
    ap.add_argument("--comment", action="store_true", help="把每段成本评论到卡上")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--fx", type=float, default=7.1, help="USD→CNY 粗略换算率（默认 7.1）")
    args = ap.parse_args()

    if not args.all and not args.run_id:
        ap.error("要么给 run_id，要么 --all")

    if PRICING_IMPORT_ERROR:
        print(f"⚠ 未能加载 Hermes 价目模块（{PRICING_IMPORT_ERROR}）→ 只报实测成本", file=sys.stderr)

    run_ids = [args.run_id]
    if args.all:
        db = board_db(args.board)
        con = ro(db)
        ids = set()
        for (key,) in con.execute(
                "select distinct idempotency_key from tasks where idempotency_key like 'blog:%'"):
            rid = key.split(":")[1]
            live = con.execute(
                "select count(*) from tasks where idempotency_key like ? and status != 'archived'",
                (f"blog:{rid}:%",)).fetchone()[0]
            if live or args.include_archived:
                ids.add(rid)
        con.close()
        run_ids = sorted(ids)

    reports = []
    for rid in run_ids:
        rep = build_report(rid, args.board, args.root, args.fx)
        reports.append(rep)
        if not args.json:
            print(render_console(rep)); print()
        if args.write:
            d = Path(rep["run_dir"])
            d.mkdir(parents=True, exist_ok=True)
            (d / "05-cost.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "05-cost.md").write_text(render_md(rep), encoding="utf-8")
            if not args.json:
                print(f"  已写 {d / '05-cost.json'} 与 05-cost.md")
        if args.comment:
            comment_cards(rep, args.board)

    if args.json:
        print(json.dumps(reports if args.all else reports[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
