"""embedding provider 的加载参数测试。

本机 huggingface.co 不可达，加载本地权重时必须显式离线，
否则 sentence-transformers 会尝试联网解析并卡住。
"""

from pathlib import Path

from private_rag.embeddings import SentenceTransformerEmbedding, _loader_kwargs


def test_local_directory_forces_offline_loading(tmp_path: Path) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()

    assert _loader_kwargs(str(model_dir)) == {"local_files_only": True}


def test_hub_model_id_stays_online_capable() -> None:
    assert _loader_kwargs("BAAI/bge-m3") == {}


def test_nonexistent_path_is_treated_as_a_hub_id(tmp_path: Path) -> None:
    assert _loader_kwargs(str(tmp_path / "not-downloaded-yet")) == {}


# ---------------------------------------------------------------------------
# 嵌入身份的规范化
#
# `name` 会被写进索引 manifest，而 `store._load_manifest()` 对 embedding 字段是
# **字符串直接比较**。同一个目录写成反斜杠或正斜杠会产生两个不同的"身份"，
# 于是明明用的是同一份权重却报「embedding model changed, rebuild the index」。
# 这里不加载模型（BGE-M3 有 2.27GB，单测不该背这个），只验证纯属性 `name`。
# ---------------------------------------------------------------------------


def _bare(model_name: str) -> SentenceTransformerEmbedding:
    """绕过 __init__（它要真加载模型）构造一个只带 model_name 的实例。"""
    instance = SentenceTransformerEmbedding.__new__(SentenceTransformerEmbedding)
    instance.model_name = model_name
    return instance


def test_backslash_local_path_is_canonicalised_to_posix() -> None:
    assert _bare(r"D:\blog-knowledge\models\bge-m3").name == "D:/blog-knowledge/models/bge-m3"


def test_posix_local_path_is_left_as_is(tmp_path: Path) -> None:
    assert _bare(tmp_path.as_posix()).name == tmp_path.as_posix()


def test_hub_model_id_is_not_rewritten() -> None:
    """hub 模型 id 不是本地路径、也不含反斜杠，必须原样返回。"""
    assert _bare("BAAI/bge-m3").name == "BAAI/bge-m3"


def test_same_directory_different_separators_give_same_identity() -> None:
    """核心断言：反斜杠与正斜杠写法必须得到同一个身份（否则 manifest 比对会误报）。"""
    backslash = _bare(r"D:\blog-knowledge\models\bge-m3").name
    posix = _bare("D:/blog-knowledge/models/bge-m3").name

    assert backslash == posix
