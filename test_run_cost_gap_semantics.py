#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_cost.py 的告警语义回归测试（用假看板库，不碰真库）。

验三件事：
  A 卡 done 但没有 done/running run  → 必须告警（真缺口，不能被静默掉）
  B 卡 ran 过但窗口内没有 worker 会话 → 必须告警
  C 卡还没跑（todo）                → 不得告警（记 not_run_yet）
  D 段序：4-review 不得排在 3-write 前面
"""
import importlib.util, os, sqlite3, sys, tempfile, time
from pathlib import Path

SCHEMA = """
create table tasks(id text primary key, title text, assignee text, status text,
  created_at integer, started_at integer, completed_at integer, idempotency_key text,
  workspace_path text);
create table task_runs(id integer primary key, task_id text, profile text, status text,
  started_at integer, ended_at integer, outcome text);
create table task_links(parent_id text, child_id text);
create table task_comments(id integer primary key, task_id text, author text,
  body text, created_at integer);
"""


def load_module():
    spec = importlib.util.spec_from_file_location("rc", r"D:\blog-swarm\run_cost.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(tmp: Path, cards):
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.executemany("insert into tasks values(?,?,?,?,?,?,?,?,?)", cards)
    con.commit()
    con.close()
    os.environ["HERMES_KANBAN_DB"] = str(tmp)
    return load_module().build_report("TESTRUN", "blog", None, 7.1)


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "fake.db"
    now = int(time.time())
    wx = r"D:\blog-runs\TESTRUN"
    # 同一秒创建，专门用来暴露「同秒退化成按 id 排序」的问题
    cards = [
        # A: done 但无任何 run → 真缺口
        ("t_fakeA", "[TESTRUN] 3/5 写作", "writer", "done", now, now - 500, now - 100,
         "blog:TESTRUN:3-write", wx),
        # B: todo → 未跑，不得告警
        ("t_fake1", "[TESTRUN] 4/5 审校", "reviewer", "todo", now, None, None,
         "blog:TESTRUN:4-review", wx),
    ]
    rep = build(tmp, cards)
    warns = rep["warnings"]
    stages = [s["stage"] for s in rep["stages"]]

    ok = True
    def check(name, cond, detail=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
        ok = ok and cond

    print("warnings:", warns)
    print("stage order:", stages)
    check("A 卡 done 无 run → 告警", any("3-write" in w for w in warns), str(warns))
    check("C 卡 todo 未跑 → 静默且标记 not_run_yet",
          not any("4-review" in w for w in warns)
          and any(s.get("not_run_yet") for s in rep["stages"] if s["stage"] == "4-review"))
    check("D 段序 3-write 在 4-review 之前", stages.index("3-write") < stages.index("4-review"),
          str(stages))

    # B: 卡真的跑过（有 done run），但对应 profile 库里没有该窗口的 kanban 会话
    tmp2 = Path(tempfile.mkdtemp()) / "fake2.db"
    con = sqlite3.connect(tmp2)
    con.executescript(SCHEMA)
    con.execute("insert into tasks values(?,?,?,?,?,?,?,?,?)",
                ("t_fakeC", "[TESTRUN] 2/5 调研", "researcher", "done", now, now - 900,
                 now - 600, "blog:TESTRUN:2-research", wx))
    con.execute("insert into task_runs values(?,?,?,?,?,?,?)",
                (1, "t_fakeC", "researcher", "done", now - 900, now - 600, "completed"))
    con.commit()
    con.close()
    os.environ["HERMES_KANBAN_DB"] = str(tmp2)
    rep2 = load_module().build_report("TESTRUN", "blog", None, 7.1)
    print("warnings(B):", rep2["warnings"])
    check("B 卡跑过但无 worker 会话 → 告警",
          any("2-research" in w for w in rep2["warnings"]), str(rep2["warnings"]))

    print("\n结果:", "全部通过" if ok else "有失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
