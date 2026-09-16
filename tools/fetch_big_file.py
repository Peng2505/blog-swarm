#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可续传 + 带完整性校验的大文件下载器。

    python tools/fetch_big_file.py <url> <输出路径>

为什么不用 curl / pip / uv 直接下：
  本机到外网的长连接会中途断（实测 index 页 97KB 都断过，BGE-M3 权重断过 8,782 字节）。
  pip/uv 不支持续传 —— 断一次就从头再来，1.8GB 的文件代价很高。

两条从事故里学来的硬规则（见 blog-swarm-pipeline 技能）：
  1. **服务端可能对 Range 请求回 200 而不是 206**（ModelScope 就是），
     所以判定"是否只给了这一段"要看 Content-Range，不能只看状态码。
  2. **长度相等 ≠ 内容正确**（BGE-M3 权重大小一字不差但内容错位）。
     所以失败重试前必须校验内容，且**校验不过时不能续传** —— 续传只会把
     已错位的部分固化得更深。zip/wheel 一律逐条目校验 CRC32。
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

CHUNK = 1 << 22


def head_size(url: str, *, attempts: int = 4) -> int | None:
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=60) as response:
                length = response.headers.get("Content-Length")
                return int(length) if length else None
        except Exception as exc:  # noqa: BLE001
            print(f"  HEAD 第 {attempt} 次失败: {type(exc).__name__}", flush=True)
            time.sleep(3 * attempt)
    return None


def verify_zip(path: Path) -> tuple[bool, str]:
    """wheel / zip 逐条目 CRC32 校验。"""
    try:
        archive = zipfile.ZipFile(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"不是有效 zip：{type(exc).__name__}"
    bad = []
    for info in archive.infolist():
        try:
            with archive.open(info.filename) as handle:
                crc = 0
                while True:
                    block = handle.read(CHUNK)
                    if not block:
                        break
                    crc = zlib.crc32(block, crc)
            if crc != info.CRC:
                bad.append(info.filename)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{info.filename}({type(exc).__name__})")
    if bad:
        return False, f"CRC 失败 {len(bad)} 条：{bad[:3]}"
    return True, f"{len(archive.infolist())} 条目 CRC32 全过"


def download(url: str, out: Path, *, expected: int | None, attempts: int = 8) -> int:
    for attempt in range(1, attempts + 1):
        have = out.stat().st_size if out.exists() else 0
        if expected and have >= expected:
            return have
        request = urllib.request.Request(url)
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_range = response.headers.get("Content-Range")
                partial = response.status == 206 or bool(content_range)
                if have and not partial:
                    print(f"  [{attempt}] 服务端未按 Range 返回，从头写", flush=True)
                    have = 0
                mode = "ab" if have else "wb"
                written = have
                with out.open(mode) as handle:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
                        if written % (256 << 20) < CHUNK:
                            pct = f"{written / expected * 100:.0f}%" if expected else "?"
                            print(f"    {written / 1048576:.0f} MB ({pct})", flush=True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  [{attempt}] 中断 {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3 * attempt)
            continue
    return out.stat().st_size if out.exists() else 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2
    url, dest = args[0], Path(args[1])
    dest.parent.mkdir(parents=True, exist_ok=True)

    expected = head_size(url)
    print(f"目标 {dest.name}")
    print(f"远端大小 {expected:,} 字节" if expected else "远端未给 Content-Length")

    for round_number in range(1, 4):
        size = download(url, dest, expected=expected)
        print(f"  第 {round_number} 轮结束：本地 {size:,} 字节", flush=True)
        if expected and size != expected:
            print("  长度不足，继续续传", flush=True)
            continue
        ok, why = verify_zip(dest)
        if ok:
            print(f"  校验通过：{why}")
            return 0
        print(f"  校验失败：{why} —— 删除重下（不续传，避免固化错误）", flush=True)
        dest.unlink(missing_ok=True)
    print("失败：多轮之后仍未拿到完整文件")
    return 1


if __name__ == "__main__":
    sys.exit(main())
