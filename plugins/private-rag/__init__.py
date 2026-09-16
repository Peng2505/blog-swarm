from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _run_root() -> Path:
    return Path(os.getenv("BLOG_RAG_RUN_ROOT", r"D:\blog-runs")).resolve()


def _allowed_roots() -> list[Path]:
    """Scope file access to the CURRENT kanban task run, not the whole run root.

    The dispatcher exports HERMES_KANBAN_WORKSPACE for cards 1-4 (the run dir),
    so a worker can only touch its own run. Outside a worker (manual CLI runs)
    we fall back to BLOG_RAG_RUN_ROOT.
    """
    workspace = (os.environ.get("HERMES_KANBAN_WORKSPACE") or "").strip()
    if workspace:
        return [Path(workspace).resolve()]
    return [_run_root()]


def _resolve_in_scope(raw: object, label: str) -> Path:
    path = Path(str(raw or "")).resolve()
    roots = _allowed_roots()
    if not any(_inside(path, root) for root in roots):
        raise ValueError(
            f"{label} must be inside the current run directory "
            f"({', '.join(str(root) for root in roots)})"
        )
    return path


def _project() -> Path:
    return Path(os.getenv("BLOG_RAG_PROJECT", r"D:\blog-swarm\rag")).resolve()


def _python() -> Path:
    return Path(
        os.getenv("BLOG_RAG_PYTHON", r"D:\blog-swarm\.venv-rag3\Scripts\python.exe")
    ).resolve()


# 语义检索的默认落地位置。
#
# 为什么默认值写成 semantic 而不是 hashing：流水线是 dispatcher 用
# `HERMES_HOME=<profile>` 拉起 worker 的，**环境变量能否传进 worker 并不可靠**。
# 把这套配置写进默认值，流水线的行为就是确定的（代码说什么就是什么），
# 环境变量仍可覆盖，用于临时实验。
DEFAULT_EMBEDDING = "semantic"
DEFAULT_MODEL = r"D:\blog-knowledge\models\bge-m3"
DEFAULT_DB = r"D:\blog-knowledge\index-bge-m3"
DEFAULT_SOURCE = r"D:\blog-knowledge\documents"


def _source() -> Path:
    return Path(os.getenv("BLOG_RAG_SOURCE", DEFAULT_SOURCE)).resolve()


def _db() -> Path:
    return Path(os.getenv("BLOG_RAG_DB", DEFAULT_DB)).resolve()


def _embedding_args() -> list[str]:
    """embedding 参数。

    semantic 模式必须同时给本地模型目录：本机 huggingface.co 不可达，
    不给 `--model` 时 CLI 会退到 hub 模型 id 并尝试联网，直接卡住。
    所以这里宁可报错，也不让它悄悄联网 —— 且**目录不存在也要报错**，
    而不是等到子进程里才失败（那样错误信息会淹没在 CLI 输出里）。
    """
    name = os.getenv("BLOG_RAG_EMBEDDING", DEFAULT_EMBEDDING)
    args = ["--embedding", name]
    if name == "semantic":
        # 区分「未设置」和「被显式设成空」：前者用默认值，后者是配置错误，必须响。
        raw = os.environ.get("BLOG_RAG_MODEL")
        model = (DEFAULT_MODEL if raw is None else raw).strip()
        if not model:
            raise ValueError(
                "BLOG_RAG_EMBEDDING=semantic 但 BLOG_RAG_MODEL 被设为空"
            )
        if not Path(model).is_dir():
            raise ValueError(
                f"本地模型目录不存在：{model}（本机无法访问 HuggingFace，不能退到联网）"
            )
        args += ["--model", Path(model).as_posix()]
        # 用 as_posix() 是为了和索引 manifest 里记录的模型路径逐字一致：
        # manifest 记的是 "D:/blog-knowledge/models/bge-m3"，而 store._load_manifest()
        # 是**字符串直接比较**（数据不同才报 “embedding model changed”）。插件默认值里
        # 写的是反斜杠 D:\blog-knowledge\models\bge-m3，直接传下去会误报「换了 embedding 模型」
        # 并让所有 worker 的检索全部失败（2026-09-17 实测）。规范化放在插件这边，
        # 不动私有知识库包的源码。
    return args


def _run_cli(arguments: list[str], *, timeout: int = 120) -> tuple[int, dict]:
    env = dict(os.environ)
    # 子进程是**独立的 venv**（默认 .venv-rag3，Python 3.12），父进程（Hermes 桌面/网关）
    # 会把 hermes-agent venv 的 site-packages 塞进 PYTHONPATH（见 hermes-agent 的
    # apps/desktop/electron/backend-env.ts）。那个 venv 里的 numpy 是给 3.11 编译的，
    # 3.12 解释器一 import 就崩：`numpy._core._multiarray_umath`（cp311 vs cpython-312）。
    # 2026-09-17 实测：worker 里 retrieve_private_knowledge / check_citations /
    # record_public_source 三个工具全被这一个环境变量打挂（复现见
    # D:\blog-runs\20260917-rag-ai-bc8ac5\evidence\probe_plugin_tool_BEFORE.txt）。
    # venv 子进程必须环境自洽，所以这里把 PYTHONPATH/PYTHONHOME 摘掉。
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    # 默认强制 CPU：本机 RTX 4060 在连续 fp32 推理时发生过 TDR 崩溃（GPU is lost，
    # 需重启恢复），而 torch 对"半死"的 GPU 会 hang 而不是报错 —— worker 会卡死。
    # 所以除非显式 BLOG_RAG_DEVICE=cuda，否则不让子进程碰 GPU。
    if os.getenv("BLOG_RAG_DEVICE", "cpu") == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""
    completed = subprocess.run(
        [str(_python()), "-m", "private_rag.cli", *arguments],
        cwd=str(_project()),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    payload: dict
    if stdout:
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            payload = {"ok": False, "error": "CLI returned non-JSON output"}
    else:
        payload = {"ok": False, "error": (completed.stderr or "")[-2000:]}
    return completed.returncode, payload


def handle(args: dict, **kwargs) -> str:
    try:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("query must not be empty")
        top_k = int(args.get("top_k", 5))
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

        source = _source()
        database = _db()

        command = [
            "query",
            query,
            "--source",
            str(source),
            "--db",
            str(database),
            *_embedding_args(),
            "--top-k",
            str(top_k),
        ]
        ledger_path = args.get("ledger_path")
        if ledger_path:
            ledger = _resolve_in_scope(ledger_path, "ledger_path")
            command.extend(["--ledger", str(ledger)])

        code, payload = _run_cli(command)
        if code != 0 or not payload.get("ok"):
            payload.setdefault("error", "private RAG CLI failed")
            payload["exit_code"] = code
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def handle_check_citations(args: dict, **kwargs) -> str:
    try:
        draft = _resolve_in_scope(args.get("draft_path"), "draft_path")
        ledger = _resolve_in_scope(args.get("ledger_path"), "ledger_path")
        code, payload = _run_cli(
            [
                "verify",
                "--draft",
                str(draft),
                "--ledger",
                str(ledger),
                "--db",
                str(_db()),
            ],
            timeout=60,
        )
        payload["exit_code"] = code
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def handle_record_public_source(args: dict, **kwargs) -> str:
    try:
        ledger = _resolve_in_scope(args.get("ledger_path"), "ledger_path")
        snapshot = _resolve_in_scope(args.get("snapshot_path"), "snapshot_path")
        if snapshot.parent != ledger.parent and ledger.parent not in snapshot.parents:
            raise ValueError(
                "snapshot_path must live in the same run directory as ledger_path"
            )
        title = str(args.get("title", "")).strip()
        url = str(args.get("url", "")).strip()
        evidence = str(args.get("evidence", "")).strip()
        if not title or not url or not evidence:
            raise ValueError("title, url and evidence are required")
        command = [
            "add-public",
            "--ledger",
            str(ledger),
            "--title",
            title,
            "--url",
            url,
            "--evidence",
            evidence,
            "--snapshot",
            str(snapshot),
        ]
        locator = str(args.get("locator", "")).strip()
        if locator:
            command.extend(["--locator", locator])
        code, payload = _run_cli(command, timeout=60)
        payload["exit_code"] = code
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def gate_kanban_complete(**kwargs) -> dict | None:
    """Hard gate: a review-stage card cannot complete without valid citations.

    The prompt asks the model to call ``check_citations`` and to block on
    failure, but a model can skip the tool, ignore ``ok=false``, or delete its
    own artefacts. This hook makes the check unavoidable.

    Scope is decided by the run's own products, not by the presence of the
    gated files: once the writer's ``03-draft.md`` exists the run is in the
    review/publish stage, so ``citations.json`` and ``final.md`` are BOTH
    required and the ledger must verify. Deleting an artefact therefore blocks
    instead of waving the card through.
    """
    if str(kwargs.get("tool_name") or "") != "kanban_complete":
        return None
    workspace = (os.environ.get("HERMES_KANBAN_WORKSPACE") or "").strip()
    if not workspace:
        return None
    run_dir = Path(workspace).resolve()
    try:
        run_dir.relative_to(_run_root())
    except ValueError:
        return {
            "action": "block",
            "message": (
                f"HERMES_KANBAN_WORKSPACE={run_dir} 不在 BLOG_RAG_RUN_ROOT 之下，"
                "按 fail-closed 处理，禁止完成本卡。"
            ),
        }
    if not (run_dir / "03-draft.md").is_file() and not (run_dir / "final.md").is_file():
        # Not a review-stage run (e.g. the researcher card): nothing to gate.
        return None

    required = {
        "citations.json": run_dir / "citations.json",
        "final.md": run_dir / "final.md",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        return {
            "action": "block",
            "message": (
                "审校阶段缺少交付物，禁止完成本卡："
                + "、".join(missing)
                + "。请补齐后重跑 check_citations；若确实无法补齐，"
                "用 kanban_block(kind=\"needs_input\", reason=...) 显式报错，"
                "不要直接完成本卡。"
            ),
        }
    try:
        code, payload = _run_cli(
            [
                "verify",
                "--draft",
                str(required["final.md"]),
                "--ledger",
                str(required["citations.json"]),
                "--db",
                str(_db()),
            ],
            timeout=25,
        )
    except Exception as exc:
        return {
            "action": "block",
            "message": (
                "引用门禁无法运行，按 fail-closed 处理，禁止完成本卡。"
                f"错误：{exc}"
            ),
        }
    if code == 0 and payload.get("ok"):
        return None
    issues = payload.get("issues") or [str(payload.get("error") or "未知错误")]
    return {
        "action": "block",
        "message": (
            "引用门禁不通过，禁止完成本卡（exit="
            f"{code}）。请修复后重跑 check_citations；问题清单："
            + "；".join(str(issue) for issue in issues)
        ),
    }


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", gate_kanban_complete)
    ctx.register_tool(
        name="retrieve_private_knowledge",
        toolset="rag_tools",
        schema={
            "name": "retrieve_private_knowledge",
            "description": (
                "检索博客私有知识库并返回带稳定 chunk/source/locator 的证据；"
                "可把命中项登记到当前 run 的 citations.json。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "具体调研问题"},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                    },
                    "ledger_path": {
                        "type": "string",
                        "description": "D:\\blog-runs\\<run_id>\\citations.json",
                    },
                },
                "required": ["query"],
            },
        },
        handler=handle,
        description="检索博客私有知识库并保留引用溯源",
    )
    ctx.register_tool(
        name="check_citations",
        toolset="rag_tools",
        schema={
            "name": "check_citations",
            "description": "确定性检查文章引用编号、来源映射、数字断言与证据原文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_path": {"type": "string"},
                    "ledger_path": {"type": "string"},
                },
                "required": ["draft_path", "ledger_path"],
            },
        },
        handler=handle_check_citations,
        description="检查博客引用完整性与证据溯源",
    )
    ctx.register_tool(
        name="record_public_source",
        toolset="rag_tools",
        schema={
            "name": "record_public_source",
            "description": "把已抓取的官方网页原文登记为可公开验证的 Wxx 引用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ledger_path": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "evidence": {"type": "string"},
                    "snapshot_path": {"type": "string"},
                    "locator": {"type": "string"},
                },
                "required": [
                    "ledger_path",
                    "title",
                    "url",
                    "evidence",
                    "snapshot_path",
                ],
            },
        },
        handler=handle_record_public_source,
        description="登记公开来源及逐字证据",
    )
