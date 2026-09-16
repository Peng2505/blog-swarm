"""对比脚本的守门测试。

重点是那个守卫：**题集不一致时必须拒绝出结论**，而不是静默 join。
实测踩过 —— 磁盘上的基线文件一度是 20 题时代的遗留，新结果是 30 题，
直接对比会得出"提升 4 倍"的假结论。
"""

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "eval" / "compare.py"


def load_module():
    name = "eval_compare"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def row(ident: str, *, hit1: bool, hit3: bool, hit5: bool, rr: float, score: float = 0.5) -> dict:
    return {
        "id": ident,
        "category": "direct",
        "family": "论文",
        "query": f"query {ident}",
        "relevant_count": 2,
        "hit@1": hit1,
        "hit@3": hit3,
        "hit@5": hit5,
        "rr": rr,
        "top1_score": score,
    }


def noans(ident: str, score: float) -> dict:
    return {
        "id": ident,
        "category": "direct",
        "family": "论文",
        "query": f"query {ident}",
        "relevant_count": 0,
        "top1_score": score,
        "top1_file": "x.txt",
        "top1_locator": "y",
    }


def write(path: Path, embedding: str, rows: list[dict]) -> Path:
    path.write_text(
        json.dumps({"embedding": embedding, "top_k": 5, "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_mismatched_query_sets_are_refused(tmp_path, capsys) -> None:
    """核心守卫：20 题 vs 30 题这种对比必须直接拒绝。"""
    module = load_module()
    before = write(tmp_path / "a.json", "hashing-384", [row("q01", hit1=False, hit3=False, hit5=False, rr=0.0)])
    after = write(
        tmp_path / "b.json",
        "bge-m3",
        [row("q01", hit1=True, hit3=True, hit5=True, rr=1.0), row("q02", hit1=True, hit3=True, hit5=True, rr=1.0)],
    )

    code = module.main(["--before", str(before), "--after", str(after)])

    assert code == 2, "题集不一致必须返回非零"
    out = capsys.readouterr().out
    assert "题集不一致" in out
    assert "q02" in out


def test_identical_sets_produce_comparison(tmp_path, capsys) -> None:
    module = load_module()
    before = write(
        tmp_path / "a.json",
        "hashing-384",
        [
            row("q01", hit1=False, hit3=False, hit5=True, rr=0.25),
            row("q02", hit1=True, hit3=True, hit5=True, rr=1.0),
        ],
    )
    after = write(
        tmp_path / "b.json",
        "bge-m3",
        [
            row("q01", hit1=True, hit3=True, hit5=True, rr=1.0),
            row("q02", hit1=True, hit3=True, hit5=True, rr=1.0),
        ],
    )

    code = module.main(["--before", str(before), "--after", str(after)])

    assert code == 0
    out = capsys.readouterr().out
    assert "0.500" in out  # before Hit@1 = 1/2
    assert "1.000" in out  # after Hit@1 = 2/2
    assert "新增命中" in out


def test_flip_table_counts_gained_and_lost(tmp_path) -> None:
    """涨和跌都要数出来，不能只报净变化。"""
    module = load_module()
    before = [
        row("q01", hit1=False, hit3=False, hit5=False, rr=0.0),  # -> 涨
        row("q02", hit1=True, hit3=True, hit5=True, rr=1.0),     # -> 跌
        row("q03", hit1=True, hit3=True, hit5=True, rr=1.0),     # -> 一直命中
        row("q04", hit1=False, hit3=False, hit5=False, rr=0.0),  # -> 一直未命中
    ]
    after = [
        row("q01", hit1=True, hit3=True, hit5=True, rr=1.0),
        row("q02", hit1=False, hit3=False, hit5=False, rr=0.0),
        row("q03", hit1=True, hit3=True, hit5=True, rr=1.0),
        row("q04", hit1=False, hit3=False, hit5=False, rr=0.0),
    ]

    table = module.flip_table(before, after, 5)

    assert table["gained"] == ["q01"]
    assert table["lost"] == ["q02"]
    assert table["kept_hit"] == ["q03"]
    assert table["kept_miss"] == ["q04"]


def test_noanswer_rows_are_excluded_from_metrics() -> None:
    """无答案行没有 hit@1 字段，不能被算进可回答分母。"""
    module = load_module()
    rows = [
        row("q01", hit1=True, hit3=True, hit5=True, rr=1.0),
        noans("q18", 0.9),
    ]

    assert len(module.answerable(rows)) == 1
    assert len(module.noanswer(rows)) == 1
    assert module.metrics(module.answerable(rows), 5)["n"] == 1


def test_missing_rows_field_fails_loud(tmp_path) -> None:
    module = load_module()
    bad = tmp_path / "bad.json"
    bad.write_text('{"embedding": "x"}', encoding="utf-8")

    try:
        module.load_rows(bad)
    except SystemExit as exc:
        assert "rows" in str(exc)
    else:
        raise AssertionError("缺 rows 字段必须报错，不能静默当空")
