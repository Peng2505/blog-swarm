import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "blog_swarm.py"


def load_blog_swarm():
    spec = importlib.util.spec_from_file_location("blog_swarm_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_researcher_contract_requires_private_retrieval_and_shared_ledger() -> None:
    module = load_blog_swarm()

    assert "retrieve_private_knowledge" in module.RESEARCH_BODY
    assert "citations.json" in module.RESEARCH_BODY
    assert "版本号" in module.RESEARCH_BODY and "官方" in module.RESEARCH_BODY


def test_writer_and_reviewer_contract_enforce_traceable_citations() -> None:
    module = load_blog_swarm()

    assert "内联引用" in module.WRITER_BODY
    assert "citations.json" in module.WRITER_BODY
    assert "check_citations" in module.REVIEW_BODY
    assert "kanban_block" in module.REVIEW_BODY


@pytest.mark.parametrize("run_id", ["../escape", "..\\escape", "D:/escape", "a/b", ""])
def test_build_plan_rejects_unsafe_run_id(run_id: str) -> None:
    module = load_blog_swarm()

    with pytest.raises(ValueError, match="run_id"):
        module.build_plan("测试主题", run_id)
