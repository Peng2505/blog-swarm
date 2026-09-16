"""大文件下载器的守门测试。

两条规则的回归测试（都来自真机事故）：
  1. 服务端对 Range 请求可能回 200（不是 206）但确实只给了这一段 → 必须续传而非重写；
     但它也可能真的忽略 Range 直接发全量 → 那时必须从头写，不能把全量接在后面。
  2. 长度相等 ≠ 内容正确。所以校验要落到 zip 条目 CRC32，且校验不过时**不能续传**。
"""

import importlib.util
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "fetch_big_file.py"


def load_module():
    name = "fetch_big_file"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_valid_zip_passes(tmp_path) -> None:
    module = load_module()
    good = tmp_path / "t.whl"
    make_zip(good, {"a": b"x" * 500, "b": b"y" * 500})

    ok, why = module.verify_zip(good)

    assert ok is True
    assert "CRC32 全过" in why


def test_truncated_zip_fails(tmp_path) -> None:
    """截断的 wheel 必须判失败 —— 这正是下载中断后的典型形态。"""
    module = load_module()
    broken = tmp_path / "t.whl"
    make_zip(broken, {"a": b"x" * 5000, "b": b"y" * 5000})
    broken.write_bytes(broken.read_bytes()[: 5000 + 30])

    ok, _ = module.verify_zip(broken)

    assert ok is False


def test_flipped_byte_fails(tmp_path) -> None:
    """改动一个字节（长度不变）也要发现。"""
    module = load_module()
    broken = tmp_path / "t.whl"
    make_zip(broken, {"a": b"x" * 1000})
    raw = bytearray(broken.read_bytes())
    raw[60] ^= 0xFF
    broken.write_bytes(bytes(raw))

    ok, why = module.verify_zip(broken)

    assert ok is False
    assert "CRC 失败" in why


def test_non_zip_fails(tmp_path) -> None:
    module = load_module()
    junk = tmp_path / "t.whl"
    junk.write_bytes(b"\x00" * 1000)

    ok, why = module.verify_zip(junk)

    assert ok is False
    assert "不是有效 zip" in why
