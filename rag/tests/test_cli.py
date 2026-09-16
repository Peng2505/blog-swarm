import json
from pathlib import Path

from private_rag.cli import main


def test_cli_indexes_queries_and_records_private_citations(tmp_path: Path, capsys) -> None:
    source = tmp_path / "documents"
    source.mkdir()
    (source / "guide.md").write_text(
        "# 调度\n\n父卡完成后，子卡自动进入 ready 状态。",
        encoding="utf-8",
    )
    database = tmp_path / "index"
    ledger = tmp_path / "run" / "citations.json"

    assert main(
        [
            "index",
            "--source",
            str(source),
            "--db",
            str(database),
            "--embedding",
            "hashing",
        ]
    ) == 0
    index_result = json.loads(capsys.readouterr().out)
    assert index_result["ok"] is True
    assert index_result["indexed_files"] == 1

    assert main(
        [
            "query",
            "父卡完成后子卡状态",
            "--source",
            str(source),
            "--db",
            str(database),
            "--embedding",
            "hashing",
            "--ledger",
            str(ledger),
        ]
    ) == 0
    query_result = json.loads(capsys.readouterr().out)
    assert query_result["ok"] is True
    assert query_result["hits"][0]["citation_id"] == "I01"
    assert ledger.is_file()
