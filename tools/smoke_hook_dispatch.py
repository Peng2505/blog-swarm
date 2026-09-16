"""Prove the pre_tool_call gate through Hermes's real dispatch path.

Run with HERMES_HOME pointing at the reviewer profile so the plugin loads
exactly as it does in a worker, and HERMES_KANBAN_WORKSPACE at a scratch run.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

RUN_ROOT = Path(os.environ["BLOG_RAG_RUN_ROOT"]).resolve()


def fire(run_dir: Path):
    os.environ["HERMES_KANBAN_WORKSPACE"] = str(run_dir)
    block, _ = _dispatch_pre_tool_call_hooks(
        "kanban_complete",
        {"task_id": "smoke", "summary": "done"},
        task_id="smoke",
        session_id="smoke",
        tool_call_id="smoke",
    )
    return block


def scrape():
    return subprocess.run(
        [
            str(Path(os.environ["BLOG_RAG_PYTHON"])),
            "-m",
            "private_rag.cli",
            "query",
            "Kanban 父子卡如何调度 worker",
            "--source",
            os.environ["BLOG_RAG_SOURCE"],
            "--db",
            os.environ["BLOG_RAG_DB"],
            "--embedding",
            "hashing",
            "--top-k",
            "1",
            "--ledger",
            str(Path(os.environ["HERMES_KANBAN_WORKSPACE"]) / "citations.json"),
        ],
        cwd=os.environ["BLOG_RAG_PROJECT"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout

import json  # noqa: E402

scratch = RUN_ROOT / "_gate_proof"
if scratch.exists():
    shutil.rmtree(scratch)
scratch.mkdir(parents=True)
(scratch / "03-draft.md").write_text("草稿", encoding="utf-8")

print("[1] review stage, no final.md  ->", str(fire(scratch))[:70])
print("[2] review stage, no ledger    ->", str(fire(scratch))[:70])

hit = json.loads(scrape())
hits = hit["hits"]
(scratch / "final.md").write_text(
    f"{hits[0]['text']}[{hits[0]['citation_id']}]\n\n"
    f"## Sources\n\n- [{hits[0]['citation_id']}] {hits[0]['title']}\n",
    encoding="utf-8",
)
print("[3] valid ledger + final.md    ->", str(fire(scratch))[:70])

(scratch / "final.md").write_text(
    "并发上限为 4 workers。[I99]\n\n## Sources\n\n- [I99] 不存在\n", encoding="utf-8"
)
print("[4] broken citation            ->", str(fire(scratch))[:70])

(scratch / "final.md").unlink()
print("[5] deleted final.md again     ->", str(fire(scratch))[:70])

shutil.rmtree(scratch)
print("SMOKE_DONE")
