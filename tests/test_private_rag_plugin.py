import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).parents[1] / "plugins" / "private-rag" / "__init__.py"


@pytest.fixture(autouse=True)
def _isolate_worker_scope(monkeypatch) -> None:
    """Scope resolution reads the ambient env; tests must not inherit a real one."""
    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    monkeypatch.delenv("BLOG_RAG_RUN_ROOT", raising=False)


def load_plugin():
    spec = importlib.util.spec_from_file_location("private_rag_plugin", PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_handler_calls_cli_without_shell_and_returns_json(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    ledger = run_root / "run-1" / "citations.json"
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("BLOG_RAG_PYTHON", str(tmp_path / "python.exe"))
    monkeypatch.setenv("BLOG_RAG_PROJECT", str(tmp_path / "rag"))
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, '{"ok": true, "hits": []}', "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = json.loads(
        module.handle(
            {"query": "父子卡如何解锁", "top_k": 3, "ledger_path": str(ledger)}
        )
    )

    assert result["ok"] is True
    assert seen["kwargs"]["shell"] is False
    assert "--ledger" in seen["command"]
    assert seen["command"][-1] == str(ledger.resolve())


def test_handler_rejects_ledger_outside_run_root(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))

    result = json.loads(
        module.handle(
            {"query": "测试", "ledger_path": str(tmp_path / "outside.json")}
        )
    )

    assert result["ok"] is False
    assert "ledger_path" in result["error"]


def test_register_exposes_retrieval_tool() -> None:
    module = load_plugin()

    class Context:
        def __init__(self) -> None:
            self.tools = []
            self.hooks = []

        def register_tool(self, **kwargs) -> None:
            self.tools.append(kwargs)

        def register_hook(self, event, fn) -> None:
            self.hooks.append((event, fn))

    context = Context()
    module.register(context)

    assert context.tools[0]["name"] == "retrieve_private_knowledge"
    assert context.tools[0]["toolset"] == "rag_tools"
    assert context.tools[1]["name"] == "check_citations"
    assert context.tools[2]["name"] == "record_public_source"
    assert context.hooks == [("pre_tool_call", module.gate_kanban_complete)]


def test_handler_scopes_ledger_to_current_kanban_run(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_root / "my-run"))

    other_run = run_root / "other-run" / "citations.json"
    blocked = json.loads(module.handle({"query": "测试", "ledger_path": str(other_run)}))

    assert blocked["ok"] is False
    assert "current run directory" in blocked["error"]


def test_gate_ignores_unrelated_tools_and_workerless_runs(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_root / "run-1"))

    assert module.gate_kanban_complete(tool_name="write_file") is None

    monkeypatch.delenv("HERMES_KANBAN_WORKSPACE", raising=False)
    assert module.gate_kanban_complete(tool_name="kanban_complete") is None


def test_gate_ignores_runs_that_never_reached_review(tmp_path: Path, monkeypatch) -> None:
    """The researcher card has no 03-draft.md, so there is nothing to gate."""
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "02-research.md").write_text("调研", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))

    assert module.gate_kanban_complete(tool_name="kanban_complete") is None


@pytest.mark.parametrize("missing", ["final.md", "citations.json"])
def test_gate_blocks_when_a_review_artifact_is_deleted(
    tmp_path: Path, monkeypatch, missing: str
) -> None:
    """Deleting your own deliverable must block, not bypass the gate."""
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "03-draft.md").write_text("草稿", encoding="utf-8")
    if missing != "final.md":
        (run_dir / "final.md").write_text("正文。[I01]", encoding="utf-8")
    if missing != "citations.json":
        (run_dir / "citations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))
    monkeypatch.setattr(
        module,
        "_run_cli",
        lambda arguments, timeout=60: (0, {"ok": True, "issues": []}),
    )

    directive = module.gate_kanban_complete(tool_name="kanban_complete")

    assert directive["action"] == "block"
    assert missing in directive["message"]


def test_gate_blocks_when_workspace_escapes_run_root(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(tmp_path / "elsewhere"))

    directive = module.gate_kanban_complete(tool_name="kanban_complete")

    assert directive["action"] == "block"
    assert "fail-closed" in directive["message"]


def test_gate_blocks_completion_when_citations_fail(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "citations.json").write_text("{}", encoding="utf-8")
    (run_dir / "final.md").write_text("正文。[I99]", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))
    monkeypatch.setattr(
        module,
        "_run_cli",
        lambda arguments, timeout=60: (2, {"ok": False, "issues": ["未知引用 I99"]}),
    )

    directive = module.gate_kanban_complete(tool_name="kanban_complete")

    assert directive["action"] == "block"
    assert "未知引用 I99" in directive["message"]


def test_gate_allows_completion_when_citations_pass(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "citations.json").write_text("{}", encoding="utf-8")
    (run_dir / "final.md").write_text("正文。[I01]", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))
    monkeypatch.setattr(
        module,
        "_run_cli",
        lambda arguments, timeout=60: (0, {"ok": True, "issues": []}),
    )

    assert module.gate_kanban_complete(tool_name="kanban_complete") is None


def test_gate_fails_closed_when_verification_cannot_run(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "citations.json").write_text("{}", encoding="utf-8")
    (run_dir / "final.md").write_text("正文。[I01]", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))

    def boom(arguments, timeout=60):
        raise RuntimeError("venv missing")

    monkeypatch.setattr(module, "_run_cli", boom)

    directive = module.gate_kanban_complete(tool_name="kanban_complete")

    assert directive["action"] == "block"
    assert "fail-closed" in directive["message"]


def test_citation_handler_rejects_files_outside_run_root(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))

    result = json.loads(
        module.handle_check_citations(
            {
                "draft_path": str(tmp_path / "draft.md"),
                "ledger_path": str(tmp_path / "citations.json"),
            }
        )
    )

    assert result["ok"] is False
    assert "current run directory" in result["error"]


def test_public_source_handler_requires_snapshot_inside_run_root(tmp_path: Path, monkeypatch) -> None:
    module = load_plugin()
    run_root = tmp_path / "runs"
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))

    result = json.loads(
        module.handle_record_public_source(
            {
                "ledger_path": str(run_root / "run-1" / "citations.json"),
                "title": "官方文档",
                "url": "https://example.com/docs",
                "evidence": "原文",
                "snapshot_path": str(tmp_path / "outside.md"),
            }
        )
    )

    assert result["ok"] is False
    assert "snapshot_path" in result["error"]
