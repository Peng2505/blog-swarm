"""看门狗告警判定的守门测试。

这个脚本的价值全在「该响的时候响、不该响的时候绝对安静」：
误报会让人不再看它的消息，漏报等于没做。
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "kanban_watchdog.py"
NOW = 1_800_000_000  # 固定"现在"，避免测试依赖真实时间


def load_module():
    """按文件路径加载被测脚本。

    必须先注册进 sys.modules：脚本用了 `from __future__ import annotations`，
    dataclasses 解析字符串注解时要靠 `sys.modules[cls.__module__]` 找到自己，
    没注册就是 `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    """
    name = "kanban_watchdog"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def task(**overrides) -> dict:
    base = {
        "id": "t_demo",
        "title": "示例卡",
        "status": "running",
        "assignee": "writer",
        "created_at": NOW - 3600,
        "started_at": NOW - 1800,
        "last_heartbeat_at": NOW - 10,
        "block_kind": None,
        "consecutive_failures": 0,
        "max_retries": None,
        "last_failure_error": None,
        "result": None,
    }
    base.update(overrides)
    return base


def test_healthy_running_card_is_silent() -> None:
    module = load_module()

    assert module.find_alerts([task()], now=NOW) == []


def test_done_and_archived_are_never_alerted() -> None:
    module = load_module()
    tasks = [task(status="done"), task(status="archived")]

    assert module.find_alerts(tasks, now=NOW) == []


def test_blocked_card_always_alerted_with_reason() -> None:
    module = load_module()

    alerts = module.find_alerts(
        [task(status="blocked", block_kind="needs_input", result="引用门禁不通过")],
        now=NOW,
    )

    assert len(alerts) == 1
    assert alerts[0].reason == "等待人工介入"
    assert "needs_input" in alerts[0].detail
    assert "引用门禁不通过" in alerts[0].detail


def test_review_card_is_alerted() -> None:
    module = load_module()

    alerts = module.find_alerts([task(status="review")], now=NOW)

    assert [a.reason for a in alerts] == ["等待人工复核"]


def test_stale_running_card_is_alerted() -> None:
    module = load_module()
    stale = task(last_heartbeat_at=NOW - 90 * 60, started_at=NOW - 90 * 60)

    alerts = module.find_alerts([stale], now=NOW, stale_minutes=45)

    assert len(alerts) == 1
    assert "疑似卡住" in alerts[0].reason
    assert alerts[0].idle_minutes >= 90


def test_fresh_heartbeat_beats_old_start_time() -> None:
    """worker 跑了很久但心跳是新的 = 在正常工作，不能报警。"""
    module = load_module()
    long_but_alive = task(started_at=NOW - 3 * 3600, last_heartbeat_at=NOW - 5)

    assert module.find_alerts([long_but_alive], now=NOW, stale_minutes=45) == []


def test_ready_card_waiting_too_long_is_alerted() -> None:
    module = load_module()
    waiting = task(
        status="ready",
        started_at=None,
        last_heartbeat_at=None,
        created_at=NOW - 120 * 60,
    )

    alerts = module.find_alerts([waiting], now=NOW, stale_minutes=45)

    assert len(alerts) == 1
    assert alerts[0].status == "ready"


def test_exhausted_retries_are_alerted() -> None:
    module = load_module()
    dead = task(
        status="ready",
        last_heartbeat_at=NOW - 5,
        consecutive_failures=3,
        max_retries=3,
        last_failure_error="rate_limit",
    )

    alerts = module.find_alerts([dead], now=NOW)

    assert [a.reason for a in alerts] == ["重试已耗尽"]
    assert "rate_limit" in alerts[0].detail


def test_failures_below_max_retries_are_not_alerted() -> None:
    """还有重试次数就不该叫人 —— 系统会自己再试。"""
    module = load_module()
    retrying = task(
        status="ready", last_heartbeat_at=NOW - 5, consecutive_failures=1, max_retries=3
    )

    assert module.find_alerts([retrying], now=NOW) == []


def test_blocked_wins_over_staleness_dedup() -> None:
    """一张卡只报一次，不能既报阻塞又报卡住。"""
    module = load_module()
    both = task(status="blocked", last_heartbeat_at=NOW - 10 * 3600)

    alerts = module.find_alerts([both], now=NOW, stale_minutes=45)

    assert len(alerts) == 1
    assert alerts[0].reason == "等待人工介入"


def test_clean_board_renders_nothing() -> None:
    module = load_module()

    assert module.render([], board="blog") == ""


def test_render_is_actionable() -> None:
    module = load_module()
    alerts = module.find_alerts([task(status="blocked")], now=NOW)

    text = module.render(alerts, board="blog")

    assert "hermes kanban --board blog show t_demo" in text
    assert "hermes kanban --board blog unblock t_demo" in text
    assert "阻塞可能是有意的" in text


def test_missing_activity_timestamps_are_surfaced_not_hidden() -> None:
    """非终态却没有活动记录 = 看门狗无法判断，这种情况要报出来而不是静默跳过。"""
    module = load_module()
    empty = task(status="ready", created_at=None, started_at=None, last_heartbeat_at=None)

    alerts = module.find_alerts([empty], now=NOW)

    assert len(alerts) == 1
    assert alerts[0].reason == "状态不明（记录里没有活动时间戳）"
    assert alerts[0].idle_minutes == -1
    assert "未知" in module.render(alerts, board="blog")


def test_created_at_does_not_mask_a_stale_card() -> None:
    """created_at 永远最早，不能参与"最近活动"比较，否则停顿时长被算短。"""
    module = load_module()
    card = task(
        created_at=NOW - 10,
        started_at=NOW - 3 * 3600,
        last_heartbeat_at=NOW - 3 * 3600,
    )

    alerts = module.find_alerts([card], now=NOW, stale_minutes=45)

    assert alerts[0].idle_minutes >= 179
