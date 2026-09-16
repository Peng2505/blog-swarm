"""断点续传判定的回归测试。

真机踩过：ModelScope 的 file 接口对 Range 请求返回部分内容并带 Content-Range，
但状态行写 200 而不是 206。只认 206 会把已下载的 872MB 当成"服务端发了全量"，
清空重来 —— 实测被打回 0，进度反复丢失。
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "fetch_bge_m3.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_bge_m3", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_content_is_recognised_even_with_status_200() -> None:
    module = load_module()

    assert module._range_satisfied(200, "bytes 1000-1999/2271145830") is True


def test_proper_206_is_accepted() -> None:
    module = load_module()

    assert module._range_satisfied(206, None) is True


def test_full_body_response_is_not_treated_as_partial() -> None:
    """服务端忽略 Range、直接发全量时必须重来，不能把全量接到旧文件后面。"""
    module = load_module()

    assert module._range_satisfied(200, None) is False


def test_file_list_covers_everything_sentence_transformers_needs() -> None:
    """modules.json 里引用的模块目录必须都在下载清单里。

    注意 2_Normalize 是有意不在清单里的：Normalize.load 忽略路径参数，缺目录无害；
    而 Pooling.load 会读 1_Pooling/config.json，所以那个必须有。
    """
    module = load_module()
    names = {rel for rel, _ in module.FILES}

    assert "1_Pooling/config.json" in names
    assert "modules.json" in names
    assert "sentence_bert_config.json" in names
    assert "pytorch_model.bin" in names
    assert not any(n.startswith("2_Normalize") for n in names), "2_Normalize 不应被下载"


def test_expected_sizes_are_declared_for_every_file() -> None:
    module = load_module()

    for rel, expected in module.FILES:
        assert expected is None or expected > 0, rel


# ---------------------------------------------------------------------------
# 内容级校验：复现 2026-09-16 那次"大小正确但文件全废"的事故
# ---------------------------------------------------------------------------


def _make_zip(path: Path, entries: dict[str, bytes]) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_valid_zip_passes_content_check(tmp_path: Path) -> None:
    module = load_module()
    good = tmp_path / "good.bin"
    _make_zip(good, {"a": b"x" * 1000, "b": b"y" * 1000})

    ok, why = module.zip_entries_ok(good)

    assert ok is True
    assert "CRC32 全过" in why


def test_flipped_byte_fails_content_check(tmp_path: Path) -> None:
    """内容被改一个字节（大小不变）必须被发现。"""
    module = load_module()
    broken = tmp_path / "broken.bin"
    _make_zip(broken, {"a": b"x" * 1000, "b": b"y" * 1000})
    raw = bytearray(broken.read_bytes())
    raw[40] ^= 0xFF  # 落在第一个条目的数据里，不动长度
    broken.write_bytes(bytes(raw))

    ok, why = module.zip_entries_ok(broken)

    assert ok is False
    assert "CRC 失败" in why


def test_size_correct_but_content_wrong_is_rejected(tmp_path: Path) -> None:
    """事故本体：文件大小完全正确，开头却是别处的数据。

    当时 pytorch_model.bin 大小 2,271,145,830 一字不差，torch 却完全读不了
    —— 只比大小的旧版校验放它过了。这条测试锁死这个行为。
    """
    module = load_module()
    shifted = tmp_path / "shifted.bin"
    _make_zip(shifted, {"a": b"x" * 4096, "b": b"y" * 4096})
    original = shifted.read_bytes()
    # 模拟"把 source[X:] 写在偏移 0"：整体前移，再补齐长度，大小一字不变
    cut = 64
    shifted.write_bytes(original[cut:] + original[:cut])

    assert shifted.stat().st_size == len(original), "大小必须保持一致，才是有效复现"

    ok, why = module.verify_file("pytorch_model.bin", shifted, len(original))

    assert ok is False, f"大小对但内容错必须判失败，实际 why={why}"


def test_non_zip_bin_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    garbage = tmp_path / "garbage.bin"
    garbage.write_bytes(b"\x00" * 4096)

    ok, why = module.zip_entries_ok(garbage)

    assert ok is False
    assert "不是有效 zip" in why


def test_size_mismatch_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    small = tmp_path / "small.bin"
    _make_zip(small, {"a": b"x" * 10})

    ok, why = module.verify_file("pytorch_model.bin", small, 999_999)

    assert ok is False
    assert "大小" in why


def test_bad_json_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    bad = tmp_path / "config.json"
    bad.write_text("{not json", encoding="utf-8")

    ok, why = module.verify_file("config.json", bad, None)

    assert ok is False
    assert "JSON" in why


def test_good_json_passes(tmp_path: Path) -> None:
    module = load_module()
    good = tmp_path / "config.json"
    good.write_text('{"hidden_size": 1024}', encoding="utf-8")

    ok, _ = module.verify_file("config.json", good, None)

    assert ok is True


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    module = load_module()

    ok, why = module.verify_file("pytorch_model.bin", tmp_path / "nope.bin", None)

    assert ok is False
    assert why == "文件不存在"
