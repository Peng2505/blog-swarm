"""评测脚本自身的测试：先证明尺子是准的，再用它量系统。"""

import json
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(EVAL_DIR))

import run_eval  # noqa: E402

QUERIES = EVAL_DIR / "queries.jsonl"
INDEX = Path(r"D:\blog-knowledge\index")

VALID_CATEGORIES = {"direct", "paraphrase", "crosslingual", "confusable", "no_answer"}


def test_query_file_schema_is_valid() -> None:
    items = run_eval.load_queries(QUERIES)

    assert len(items) >= 15
    assert len({item["id"] for item in items}) == len(items), "id 必须唯一"
    for item in items:
        assert item["category"] in VALID_CATEGORIES, item
        assert item["query"].strip()
        answerable = item["category"] != "no_answer"
        if answerable:
            # 有答案的必须且只能用一种标注方式：正文锚点 或 章节前缀
            labels = [bool(item.get("anchor")), bool(item.get("locator"))]
            assert labels.count(True) == 1, f"{item['id']} 标注方式不合法"
            assert item.get("file"), f"{item['id']} 缺 file"
        else:
            assert not item.get("anchor") and not item.get("locator"), item["id"]
            assert not item.get("file"), item["id"]


def test_every_category_is_covered() -> None:
    items = run_eval.load_queries(QUERIES)
    categories = {item["category"] for item in items}

    assert categories == VALID_CATEGORIES


@pytest.mark.parametrize(
    "ranked,relevant,expected",
    [
        (["a", "b"], {"a"}, True),
        (["a", "b"], {"c"}, False),
        ([], {"a"}, False),
        (["x", "y", "a"], {"a"}, True),
    ],
)
def test_hit_at_k(ranked, relevant, expected) -> None:
    assert run_eval.hit_at_k(ranked, relevant, 3) is expected


def test_hit_at_k_respects_the_cutoff() -> None:
    ranked = ["x", "y", "z", "a"]

    assert run_eval.hit_at_k(ranked, {"a"}, 4) is True
    assert run_eval.hit_at_k(ranked, {"a"}, 3) is False


@pytest.mark.parametrize(
    "ranked,relevant,expected",
    [
        (["a", "b"], {"a"}, 1.0),
        (["b", "a"], {"a"}, 0.5),
        (["b", "c", "a"], {"a"}, 1 / 3),
        (["b", "c"], {"a"}, 0.0),
        ([], {"a"}, 0.0),
    ],
)
def test_reciprocal_rank(ranked, relevant, expected) -> None:
    assert run_eval.reciprocal_rank(ranked, relevant) == pytest.approx(expected)


def test_relevant_chunks_must_be_within_top_k_to_count() -> None:
    """Hit@k 只数前 k 名，第 k+1 名不算命中。"""
    ranked = list("abcdef")
    assert run_eval.hit_at_k(ranked, {"f"}, 5) is False
    assert run_eval.hit_at_k(ranked, {"f"}, 6) is True


@pytest.mark.skipif(not INDEX.is_dir(), reason="需要真实索引")
def test_every_label_resolves_on_the_current_index() -> None:
    """标注完整性：标注在当前索引里必须找得到，否则指标无意义（应 fail loud）。"""
    items = run_eval.load_queries(QUERIES)
    unresolved = []
    for item in items:
        if not item.get("file"):
            continue
        relevant = run_eval.resolve_relevant(
            INDEX, item["file"], anchor=item.get("anchor"), locator=item.get("locator")
        )
        if not relevant:
            unresolved.append(
                f"{item['id']} -> {item['file']} / {item.get('anchor') or item.get('locator')!r}"
            )

    assert unresolved == [], "标注失效（索引或文件已变）：" + "; ".join(unresolved)


@pytest.mark.skipif(not INDEX.is_dir(), reason="需要真实索引")
def test_locator_labels_and_anchor_labels_are_both_supported() -> None:
    by_anchor = run_eval.resolve_relevant(INDEX, "2307.03172v3.pdf", anchor="U-shaped")
    by_locator = run_eval.resolve_relevant(
        INDEX, "2307.03172v3.pdf", locator="Page 1"
    )

    assert by_anchor and by_locator
    assert set(by_locator) != set(by_anchor)


def test_resolve_relevant_rejects_ambiguous_or_missing_labels() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        run_eval.resolve_relevant(INDEX, "x.md")
    with pytest.raises(ValueError, match="exactly one"):
        run_eval.resolve_relevant(INDEX, "x.md", anchor="a", locator="b")


def test_family_of_classifies_corpus_files() -> None:
    assert run_eval.family_of("2307.03172v3.pdf") == "论文"
    assert run_eval.family_of("hermes-docs-full.txt") == "官方文档"
    assert run_eval.family_of("blog-pipeline.md") == "本机"


@pytest.mark.skipif(not INDEX.is_dir(), reason="需要真实索引")
def test_anchor_resolution_is_independent_of_query_ranking() -> None:
    """同一个锚点解析两次必须一致，且不依赖检索结果。"""
    first = run_eval.resolve_relevant(INDEX, "2307.03172v3.pdf", "U-shaped")
    second = run_eval.resolve_relevant(INDEX, "2307.03172v3.pdf", "U-shaped")

    assert first == second
    assert len(first) >= 1


@pytest.mark.skipif(not INDEX.is_dir(), reason="需要真实索引")
def test_missing_anchor_returns_empty_not_error() -> None:
    assert run_eval.resolve_relevant(INDEX, "2307.03172v3.pdf", "这句话肯定不存在zzz") == []
