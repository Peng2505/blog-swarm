"""embedding provider 的加载参数测试。

本机 huggingface.co 不可达，加载本地权重时必须显式离线，
否则 sentence-transformers 会尝试联网解析并卡住。
"""

from pathlib import Path

from private_rag.embeddings import _loader_kwargs


def test_local_directory_forces_offline_loading(tmp_path: Path) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()

    assert _loader_kwargs(str(model_dir)) == {"local_files_only": True}


def test_hub_model_id_stays_online_capable() -> None:
    assert _loader_kwargs("BAAI/bge-m3") == {}


def test_nonexistent_path_is_treated_as_a_hub_id(tmp_path: Path) -> None:
    assert _loader_kwargs(str(tmp_path / "not-downloaded-yet")) == {}
