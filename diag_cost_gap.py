#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 blog 成本汇总的「未匹配到 worker 会话」缺口。"""
import sqlite3, os
from datetime import datetime as dt
from pathlib import Path

HOME = Path(os.environ["LOCALAPPDATA"]) / "hermes"
BOARD = HOME / "kanban" / "boards" / "blog" / "kanban.db"
SLACK_BEFORE, SLACK_AFTER = 180, 3600

def ro(p):
    con = sqlite3.connect("file:///" + str(p).replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    return con

def f(t):
    return dt.fromtimestamp(t).strftime("%m-%d %H:%M:%S") if t else "-"

def profile_db(profile):
    p = HOME / "state.db" if profile in ("", "default") else HOME / "profiles" / profile / "state.db"
    return p if p.exists() else None

def diag(run_id):
    con = ro(BOARD)
    cur = con.cursor()
    cards = list(cur.execute(
        "select id,title,assignee,status,idempotency_key,created_at,started_at,completed_at "
        "from tasks where idempotency_key like ? or title like ? order by created_at",
        (f"blog:{run_id}:%", f"[{run_id}] %")))
    print(f"### run {run_id} — {len(cards)} 张卡\n")
    for c in cards:
        runs = list(cur.execute(
            "select id,profile,status,started_at,ended_at,outcome from task_runs "
            "where task_id=? order by started_at", (c["id"],)))
        prof = c["assignee"] or "default"
        print(f"{c['id']}  [{c['status']}]  {c['assignee']}  {c['title'][:50]}")
        print(f"    key={c['idempotency_key']}")
        print(f"    created={f(c['created_at'])} started={f(c['started_at'])} done={f(c['completed_at'])}")
        if not runs:
            print("    !! task_runs 为空 → stage_window=None → 必然未匹配")
        for r in runs:
            print(f"    run#{r['id']} profile={r['profile']} status={r['status']} "
                  f"{f(r['started_at'])} -> {f(r['ended_at'])} outcome={r['outcome']}")
        # 复现 stage_window 逻辑
        ok = [r for r in runs if r["status"] in ("done", "running")]
        if not ok:
            print("    stage_window: 无 done/running run → w0=None → 未匹配")
            print()
            continue
        r = ok[0]
        w0, w1 = r["started_at"], r["ended_at"]
        prof2 = r["profile"] or prof
        lo, hi = w0 - SLACK_BEFORE, (w1 or w0) + SLACK_AFTER
        db = profile_db(prof2)
        if not db:
            print(f"    !! profile 库不存在: {prof2}")
            print()
            continue
        pcon = ro(db)
        sess = list(pcon.execute(
            "select id,model,source,started_at,last_activity_at from sessions "
            "where source='kanban' and started_at between ? and ? order by started_at", (lo, hi)))
        allsess = list(pcon.execute(
            "select id,model,source,started_at,last_activity_at from sessions "
            "where started_at between ? and ? order by started_at", (lo, hi)))
        print(f"    窗口 {f(lo)} → {f(hi)} on {prof2}: kanban会话={len(sess)} 任意会话={len(allsess)}")
        for s in allsess:
            mark = "KANBAN" if s["source"] == "kanban" else s["source"]
            print(f"        [{mark}] {s['id']} model={s['model']} started={f(s['started_at'])}")
        pcon.close()
        print()
    # profile 目录清单
    print("### profiles 目录")
    pdir = HOME / "profiles"
    if pdir.exists():
        for d in sorted(pdir.iterdir()):
            sd = d / "state.db"
            print(f"  {d.name:<20} state.db={'有' if sd.exists() else '缺失'}")
    con.close()

if __name__ == "__main__":
    import sys
    for rid in (sys.argv[1:] or ["20260916-langgraph-84652f"]):
        diag(rid)
