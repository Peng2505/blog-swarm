"""插件必须能把本地模型路径传给 CLI，且默认值就是语义检索。

背景：
- 本机 HuggingFace 不可达：`--embedding semantic` 不带 `--model` 时，CLI 会退到
  hub 模型 id 并尝试联网，直接卡死。所以宁可报错。
- **默认值必须是 semantic + 本地 BGE-M3 路径**：worker 由 dispatcher 用
  `HERMES_HOME=<profile>` 拉起，环境变量能否传进去不可靠，配置写进代码才确定。
"""

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parents[1] / "plugins" / "private-rag" / "__init__.py"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch) -> None:
    for name in (
        "HERMES_KANBAN_WORKSPACE",
        "BLOG_RAG_RUN_ROOT",
        "BLOG_RAG_MODEL",
        "BLOG_RAG_EMBEDDING",
        "BLOG_RAG_PYTHON",
        "BLOG_RAG_DB",
        "BLOG_RAG_SOURCE",
        "BLOG_RAG_PROJECT",
        "BLOG_RAG_DEVICE",
    ):
        monkeypatch.delenv(name, raising=False)


def load_plugin():
    spec = importlib.util.spec_from_file_location("private_rag_plugin_model", PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(seen):
    import subprocess

    def run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, '{"ok": true, "hits": []}', "")

    return run


def _flag(command, name):
    return command[command.index(name) + 1] if name in command else None


# ---------------------------------------------------------------------------
# 默认值：流水线必须默认就走语义检索
# ---------------------------------------------------------------------------


def test_default_embedding_is_semantic_with_local_model(tmp_path, monkeypatch) -> None:
    """不改任何环境变量时，必须默认 semantic + 本地模型路径。

    这是「功能做完」和「跑在链路上」的分界线 —— 默认还是 hashing 的话，
    索引建得再好，真实流水线用的仍是词法哈希。
    """
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _fake_run(seen))

    module.handle({"query": "测试"})

    assert _flag(seen["command"], "--embedding") == "semantic"
    # 转发出去的必须是 POSIX 形式：索引 manifest 里存的就是 POSIX，而
    # store._load_manifest() 对 embedding 字段是**字符串直接比较** ——
    # 传反斜杠会被判成「换了 embedding 模型」。DEFAULT_MODEL 是反斜杠字面量，
    # 所以这里必须比 as_posix()，不是比 DEFAULT_MODEL。
    assert _flag(seen["command"], "--model") == Path(module.DEFAULT_MODEL).as_posix()


def test_default_db_points_at_semantic_index(tmp_path, monkeypatch) -> None:
    """默认索引必须是 1024 维的那个库，不能是 384 维的哈希库（维数不匹配会直接报错）。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _fake_run(seen))

    module.handle({"query": "测试"})

    assert _flag(seen["command"], "--db") == module.DEFAULT_DB
    assert module.DEFAULT_DB.endswith("index-bge-m3")


def test_model_path_is_forwarded_when_configured(tmp_path, monkeypatch) -> None:
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_EMBEDDING", "semantic")
    monkeypatch.setenv("BLOG_RAG_MODEL", r"D:\blog-knowledge\models\bge-m3")
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _fake_run(seen))

    module.handle({"query": "测试"})

    assert _flag(seen["command"], "--embedding") == "semantic"
    # 即便调用方给的是反斜杠，转发出去的也必须是 POSIX —— 见上一条测试的说明
    assert _flag(seen["command"], "--model") == "D:/blog-knowledge/models/bge-m3"


def test_model_flag_absent_for_hashing(tmp_path, monkeypatch) -> None:
    """显式回退到 hashing（对比实验用）时不带 --model。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_EMBEDDING", "hashing")
    monkeypatch.setenv("BLOG_RAG_MODEL", r"D:\blog-knowledge\models\bge-m3")
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _fake_run(seen))

    module.handle({"query": "测试"})

    assert "--model" not in seen["command"], "hashing 模式不需要模型路径"


def test_default_interpreter_supports_torch() -> None:
    """默认解释器必须是能加载 torch 的那个环境（旧 .venv-rag 基于 Anaconda Python）。"""
    module = load_plugin()

    python = module._python()

    assert "venv-rag3" in str(python), f"默认解释器仍是 {python}"


# ---------------------------------------------------------------------------
# 出错时必须响，不能静默退化
# ---------------------------------------------------------------------------


def test_missing_model_dir_fails_loudly(tmp_path, monkeypatch) -> None:
    """模型目录不存在 → 必须报错，不能退到联网默认模型（本机 HF 不可达）。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_EMBEDDING", "semantic")
    monkeypatch.setenv("BLOG_RAG_MODEL", str(tmp_path / "no-such-model"))
    monkeypatch.setattr(module.subprocess, "run", _fake_run({}))

    result = json.loads(module.handle({"query": "测试"}))

    assert result["ok"] is False
    assert "不存在" in result["error"]


def test_blank_model_env_fails_loudly(tmp_path, monkeypatch) -> None:
    """把 BLOG_RAG_MODEL 显式设成空串 → 也要报错，不能退回默认值。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_EMBEDDING", "semantic")
    monkeypatch.setenv("BLOG_RAG_MODEL", "")

    with pytest.raises(ValueError):
        module._embedding_args()


def test_model_flag_is_also_used_by_verify_gate(tmp_path, monkeypatch) -> None:
    """门禁要核对 chunk 绑定，必须传 --db 指向当前索引（不是默认那个）。

    verify 本身不做 embedding，所以不需要 --model。
    """
    module = load_plugin()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "03-draft.md").write_text("草稿", encoding="utf-8")
    (run_dir / "final.md").write_text("正文。[I01]", encoding="utf-8")
    (run_dir / "citations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(run_root))
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", str(run_dir))
    monkeypatch.setenv("BLOG_RAG_DB", r"D:\blog-knowledge\index-bge-m3")
    seen = {}
    monkeypatch.setattr(
        module,
        "_run_cli",
        lambda arguments, timeout=60: (seen.setdefault("args", arguments), {"ok": True})[1],
    )

    module.gate_kanban_complete(tool_name="kanban_complete")

    assert _flag(seen["args"], "--db") == r"D:\blog-knowledge\index-bge-m3"
    assert "--model" not in seen["args"]


def test_query_uses_configured_db_and_source(tmp_path, monkeypatch) -> None:
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_DB", r"D:\blog-knowledge\index-bge-m3")
    monkeypatch.setenv("BLOG_RAG_SOURCE", r"D:\blog-knowledge\documents")
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _fake_run(seen))

    module.handle({"query": "测试"})

    assert _flag(seen["command"], "--db") == r"D:\blog-knowledge\index-bge-m3"


# ---------------------------------------------------------------------------
# 设备：默认必须 CPU，GPU 需显式 opt-in
# ---------------------------------------------------------------------------


def _capture_env(seen):
    import subprocess

    def run(command, **kwargs):
        seen["env"] = kwargs.get("env", {})
        return subprocess.CompletedProcess(command, 0, '{"ok": true, "hits": []}', "")

    return run


def test_cli_forces_cpu_by_default(tmp_path, monkeypatch) -> None:
    """本机 GPU 曾 TDR 崩溃（GPU is lost），worker 不能挂在半死的 GPU 上。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _capture_env(seen))

    module.handle({"query": "测试"})

    assert seen["env"].get("CUDA_VISIBLE_DEVICES") == "", "默认必须强制 CPU"


def test_gpu_must_be_explicitly_opted_in(tmp_path, monkeypatch) -> None:
    """只有显式 BLOG_RAG_DEVICE=cuda 才放开 GPU。"""
    module = load_plugin()
    monkeypatch.setenv("BLOG_RAG_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("BLOG_RAG_DEVICE", "cuda")
    seen = {}
    monkeypatch.setattr(module.subprocess, "run", _capture_env(seen))

    module.handle({"query": "测试"})

    assert seen["env"].get("CUDA_VISIBLE_DEVICES", "unset") != ""
