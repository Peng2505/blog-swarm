#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看板看门狗：只读检测「有没有卡在等人，但没人知道」。

背景（为什么需要它）：
  * 流水线的 HITL 出口是 `kanban_block(kind="needs_input")` —— 卡住等人工。
  * 但**没有任何东西会通知人**：现有 cron 只有成本汇总（每 2h、静默）和选题简报，
    都不看卡状态。于是 worker 认真地把卡 block 了，卡在看板上停着，
    除非人主动 `hermes kanban --board blog list`，否则永远不会被发现。
  * 门禁没有告警等于没有门禁。这个脚本补的就是「叫人」这一环。

设计原则：
  * **只读**。绝不改卡、不改库（只开 sqlite3 只读连接）。
  * **无输出 = 无消息**（配 no_agent cron：空 stdout 不投递，不打扰）。
  * 有告警时输出可执行的下一步命令，而不只是"有问题"。
  * 阻塞可能是**有意的**（例如 `blog_swarm --park` 会故意停卡做拓扑测试），
    所以告警必须带上 block 原因和 summary 让人自己判断，不能只报"卡住了"。

用法：
    python kanban_watchdog.py                    # 静默模式，有问题才打印
    python kanban_watchdog.py --board blog
    python kanban_watchdog.py --stale-minutes 45 --json
    python kanban_watchdog.py --db <path>        # 覆盖看板库路径（测试用）

退出码：0 = 检测完成（无论有无告警）；1 = 检测自身失败（由 cron 投递诊断）
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# 与 hermes_cli/kanban_db.py 的 VALID_STATUSES 对齐
TERMINAL_STATUSES = {"done", "archived"}
# 这两个状态本身就是"等人"：blocked = worker 主动叫停，review = 等人复核
WAITING_STATUSES = {"blocked", "review"}
# 其余非终态都算"在飞"，超时未动才告警
DEFAULT_STALE_MINUTES = 45


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "hermes"


def board_db(board: str) -> Path:
    override = os.environ.get("HERMES_KANBAN_DB")
    if override:
        return Path(override)
    return hermes_home() / "kanban" / "boards" / board / "kanban.db"


@dataclass(frozen=True)
class Alert:
    task_id: str
    status: str
    assignee: str
    title: str
    idle_minutes: int
    reason: str
    detail: str = ""


def _latest_activity(task: dict) -> int | None:
    """最近一次活动时间。

    按信号具体程度取**第一个可用值**，而不是取最大值：
    `created_at` 是卡被创建的时间，永远是最早的，把它拉进比较会掩盖真实停顿时长
    （实测把"心跳停在 90 分钟前"算成了 60 分钟，因为 created_at 更晚）。
    """
    for key in ("last_heartbeat_at", "started_at", "created_at"):
        value = task.get(key)
        if value:
            return int(value)
    return None


def find_alerts(
    tasks: list[dict],
    *,
    now: int,
    stale_minutes: int = DEFAULT_STALE_MINUTES,
) -> list[Alert]:
    """纯函数：给定卡列表，返回需要人工关注的卡。不发 IO，便于单测。"""
    alerts: list[Alert] = []
    for task in tasks:
        status = str(task.get("status") or "")
        if status in TERMINAL_STATUSES:
            continue

        activity = _latest_activity(task)
        idle = int((now - activity) // 60) if activity else -1
        base = {
            "task_id": str(task.get("id") or ""),
            "status": status,
            "assignee": str(task.get("assignee") or ""),
            "title": str(task.get("title") or ""),
            "idle_minutes": idle,
        }

        if status in WAITING_STATUSES:
            kind = str(task.get("block_kind") or "").strip()
            summary = str(task.get("result") or "").strip()[:200]
            detail = f"kind={kind}" if kind else ""
            if summary:
                detail = (detail + " | " if detail else "") + f"summary={summary}"
            alerts.append(
                Alert(
                    **base,
                    reason="等待人工介入" if status == "blocked" else "等待人工复核",
                    detail=detail,
                )
            )
            continue

        max_retries = task.get("max_retries")
        failures = int(task.get("consecutive_failures") or 0)
        if failures > 0 and max_retries is not None and failures >= int(max_retries):
            alerts.append(
                Alert(
                    **base,
                    reason="重试已耗尽",
                    detail=str(task.get("last_failure_error") or "").strip()[:200],
                )
            )
            continue

        if activity is None:
            # 非终态却没有任何活动时间戳：正是"没人知道"的形态，不能静默跳过
            alerts.append(
                Alert(**base, reason="状态不明（记录里没有活动时间戳）", detail="")
            )
            continue

        if idle >= stale_minutes:
            alerts.append(
                Alert(
                    **base,
                    reason=f"疑似卡住（{idle} 分钟无活动，阈值 {stale_minutes}）",
                    detail="",
                )
            )
    return alerts


def load_tasks(db_path: Path) -> list[dict]:
    """只读打开看板库。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, title, status, assignee, created_at, started_at,
                   last_heartbeat_at, block_kind, consecutive_failures,
                   max_retries, last_failure_error, result
            FROM tasks
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def render(alerts: list[Alert], *, board: str) -> str:
    if not alerts:
        return ""
    lines = [f"[blog-watchdog] {len(alerts)} 张卡需要人工关注（看板 {board}）：", ""]
    for a in alerts:
        age = f"{a.idle_minutes} 分钟" if a.idle_minutes >= 0 else "未知"
        lines.append(f"• {a.task_id}  [{a.status}]  assignee={a.assignee or '-'}  停 {age}")
        lines.append(f"  标题：{a.title}")
        lines.append(f"  原因：{a.reason}")
        if a.detail:
            lines.append(f"  细节：{a.detail}")
        lines.append(f"  查看：hermes kanban --board {board} show {a.task_id}")
        if a.status == "blocked":
            lines.append(f"  解除：hermes kanban --board {board} unblock {a.task_id}")
        lines.append("")
    lines.append("（阻塞可能是有意的，例如 --park 拓扑测试；先 show 看清再决定）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="看板看门狗（只读）")
    parser.add_argument("--board", default=os.environ.get("BLOG_BOARD", "blog"))
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = args.db or board_db(args.board)
    if not path.exists():
        print(f"[blog-watchdog] 看板库不存在：{path}")
        return 1

    try:
        tasks = load_tasks(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[blog-watchdog] 读库失败：{type(exc).__name__}: {exc}")
        return 1

    alerts = find_alerts(
        tasks, now=int(datetime.now().timestamp()), stale_minutes=args.stale_minutes
    )

    if args.json:
        print(json.dumps([asdict(a) for a in alerts], ensure_ascii=False, indent=2))
        return 0

    text = render(alerts, board=args.board)
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
