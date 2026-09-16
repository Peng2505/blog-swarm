#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre_llm_call shell hook —— 收到「写博客：<主题>」时一键启动博客流水线。

Hermes 的 shell hook 协议：payload 走 stdin(JSON)，stdout 回 JSON。
本脚本返回 `{"context": "..."}`，Hermes 会把这段文字注入本轮 user message，
于是模型回复时自带卡片 id，而不是瞎编一句"好的我来安排"。

触发词（仅在 weixin / email 平台生效）：
    /blog <主题>      写博客：<主题>      发起博客 <主题>      博客流水线：<主题>

任何失败都以 {"context": "..."} 说明原因返回，绝不静默吞掉。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BOARD = "blog"
BUILDER = Path(r"D:\blog-swarm\blog_swarm.py")
PYTHON = Path(r"C:\Users\peng\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")
ACTIVE_PLATFORMS = {"weixin", "email"}
TRIGGER_RE = re.compile(
    r"^\s*(?:/blog|写博客|发起博客|写一篇博客|博客流水线)\s*[:：]?\s*(?P<topic>.+?)\s*$",
    re.IGNORECASE,
)
MIN_TOPIC_LEN = 4
TIMEOUT_S = 150


def emit(context: str) -> None:
    print(json.dumps({"context": context}, ensure_ascii=False))


def payload_field(payload: dict, key: str):
    """事件参数可能在顶层，也可能在 extra 里 —— 两边都找。"""
    if payload.get(key) is not None:
        return payload[key]
    extra = payload.get("extra") or {}
    if isinstance(extra, dict):
        return extra.get(key)
    return None


def run(cmd: list[str], timeout: int = TIMEOUT_S) -> tuple[int, str, str]:
    kwargs = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        p = subprocess.run(cmd, timeout=timeout, **kwargs)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"超时 (>{timeout}s)"
    except Exception as exc:  # noqa: BLE001 - hook 不能抛
        return 125, "", f"{type(exc).__name__}: {exc}"


def subscribe_to_weixin(cards: list[dict], sender_id: str) -> str:
    """把流水线的终态事件推回微信私聊（尽力而为，失败不影响启动）。"""
    ok, failed = 0, []
    for card in cards:
        code, out, err = run([
            "hermes", "kanban", "--board", BOARD, "notify-subscribe", card["id"],
            "--platform", "weixin", "--chat-id", sender_id,
            "--chat-type", "dm", "--delivery-mode", "notify",
        ], timeout=30)
        if code == 0:
            ok += 1
        else:
            failed.append(f"{card['id']}:{(err or out).strip()[:80]}")
    if failed:
        return f"（订阅微信通知 {ok}/{len(cards)} 成功，失败：{'; '.join(failed[:2])}）"
    return f"（已订阅微信通知：{len(cards)} 张卡的完成/失败事件会推给你）"


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # 非法 payload：静默放行，不干扰对话
    if not isinstance(payload, dict):
        return 0

    platform = str(payload_field(payload, "platform") or "").lower()
    if platform not in ACTIVE_PLATFORMS:
        return 0

    message = payload_field(payload, "user_message") or ""
    if not isinstance(message, str) or not message.strip():
        return 0

    first_line = next((ln for ln in message.splitlines() if ln.strip()), "")
    m = TRIGGER_RE.match(first_line)
    if not m:
        return 0

    topic = m.group("topic").strip().strip("。.")
    if len(topic) < MIN_TOPIC_LEN:
        emit("检测到写博客指令，但主题太短（<4 字），没有启动流水线。请用「写博客：<具体主题>」。")
        return 0

    if not BUILDER.exists():
        emit(f"博客流水线启动失败：找不到启动脚本 {BUILDER}")
        return 0

    py = str(PYTHON) if PYTHON.exists() else sys.executable
    code, out, err = run([py, str(BUILDER), topic, "--board", BOARD, "--json"])
    if code != 0:
        emit("博客流水线启动失败：\n" + (err or out).strip()[:600])
        return 0

    try:
        data = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        emit("博客流水线启动失败：脚本输出无法解析。\n" + out.strip()[:400])
        return 0

    cards = data.get("cards", [])
    lines = [
        "【系统注入】博客流水线已启动，无需你再做任何调度动作。",
        f"主题：{data.get('topic')}",
        f"运行 id：{data.get('run_id')}",
        f"运行目录：{data.get('run_dir')}",
        f"看板：{data.get('board')}",
        "卡片（按顺序自动接力，每张卡由对应 profile 的 worker 执行）：",
    ]
    for c in cards:
        lines.append(f"  {c.get('id')}  {c.get('assignee')}  {c.get('title')}")
    lines.append(
        "请用两三句话回复：确认主题已进入流水线，列出卡片 id 说明五段流程"
        "（拆解 → 调研 → 写作 ≥2000 字 → 审校 → 发布到 GitHub），"
        "并说明整条链路通常需要 30–60 分钟、每完成一段会在微信收到通知。"
        "不要说自己正在调研/写作，也不要再创建任何卡片。"
    )
    sub_note = subscribe_to_weixin(cards, str(payload_field(payload, "sender_id") or ""))
    lines.append(sub_note)
    emit("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
