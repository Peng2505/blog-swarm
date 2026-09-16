#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复被写坏的 BGE-M3 权重：只补下开头缺失的字节，不重下 2.27GB。

背景：
  2026-09-16 的下载器有一个 Range 判定 bug —— 服务端对 Range 请求返回**部分内容**
  并带 `Content-Range`，但状态行写 200 而非 206。旧代码只认 206，于是把已下载的
  872MB 当成"服务端发了全量"，用 `wb` 清空重下；多次重试叠加后，文件的头
  132,768,336 字节变成了源文件别处的内容，大小却凑巧等于正确大小。

怎么知道尾部是好的（不是猜的）：
  zip 中央目录里的局部文件头偏移是**绝对偏移**。逐条读取并按中央目录声明的偏移
  校验 CRC32，392/392 通过 —— 若尾部相对真实文件有位移，绝对偏移必然读错、CRC 必挂。
  所以尾部 == 原始文件的 [CUTOFF, end) 字节。只有开头 3 个条目（data.pkl、
  byteorder、data/0）的头落在损坏区。

本脚本：
  1) 只 Range 拉取 [0, CUTOFF) 补到临时文件
  2) 拼成 repaired 文件
  3) 全量校验（大小 + 395 条目 CRC32 全过）
  4) 校验通过才替换原文件；任何一步失败都不碰原文件

用法：
    python tools/repair_bge_m3_head.py --model-dir D:/blog-knowledge/models/bge-m3
    python tools/repair_bge_m3_head.py --model-dir ... --cutoff 132768336
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

REMOTE = "https://www.modelscope.cn/api/v1/models/BAAI/bge-m3/repo?Revision=master&FilePath=pytorch_model.bin"
EXPECTED_SIZE = 2_271_145_830
DEFAULT_CUTOFF = 132_768_336
ZIP_MAGIC = b"PK\x03\x04"
CHUNK = 1 << 22  # 4 MiB


def fetch_prefix(cutoff: int, out: Path, *, attempts: int = 6) -> None:
    """Range 拉取 [0, cutoff)，带断点续传重试。

    实测服务端会正常回 Content-Range，但**连接可能中途断**（首次跑短了 8,782 字节）。
    长度不足必须重试补下，而不是当成成功 —— 静默接受短读正是当初毁掉整个文件的成因。
    """
    for attempt in range(1, attempts + 1):
        have = out.stat().st_size if out.exists() else 0
        if have >= cutoff:
            return
        if have:
            request = urllib.request.Request(
                REMOTE,
                headers={"Range": f"bytes={have}-{cutoff - 1}", "User-Agent": "Mozilla/5.0"},
            )
        else:
            request = urllib.request.Request(
                REMOTE,
                headers={"Range": f"bytes=0-{cutoff - 1}", "User-Agent": "Mozilla/5.0"},
            )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_range = response.headers.get("Content-Range")
                total = response.headers.get("Content-Length")
                print(f"  [{attempt}] HTTP {response.status}  ({have:,}/{cutoff:,} 已有)  本次 {total}  Content-Range={content_range}")
                mode = "ab" if have else "wb"
                with out.open(mode) as handle:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  [{attempt}] 中断 {type(exc).__name__}: {exc}，重试")
            continue

    have = out.stat().st_size if out.exists() else 0
    if have != cutoff:
        raise RuntimeError(f"补下的长度不对：{have} != {cutoff}")


def entry_ok(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    """读完整条目并比对 CRC32。"""
    try:
        with archive.open(info.filename) as handle:
            crc = 0
            while True:
                block = handle.read(CHUNK)
                if not block:
                    break
                crc = zlib.crc32(block, crc)
        return crc == info.CRC
    except Exception:  # noqa: BLE001
        return False


def entry_end(info: zipfile.ZipInfo) -> int:
    """条目在文件中的结束偏移（局部头 + 名 + extra + 压缩数据）。"""
    return info.header_offset + 30 + len(info.filename) + len(info.extra) + info.compress_size


def detect_cutoff(path: Path) -> int:
    """自动定位损坏边界 = 第一个能通过 CRC 校验的条目的**结束**偏移。

    取"结束"而不是"起始"，是为了把前一个条目的数据描述符区（zip 的
    data descriptor，实测 data/0 之后有 72 字节）也纳入重下范围，边界不留缝。

    不要靠"搜 PK\\x03\\x04"来定边界 —— 实测踩过：data/0 的张量数据里恰好出现
    了 50 4b 03 04 这 4 个字节，于是把边界误判成 132MB，而真实损坏区是 1.02GB。
    bf16/fp32 权重里出现任意 4 字节组合都是正常的，字节模式匹配在这里不可靠。
    """
    archive = zipfile.ZipFile(path)
    infos = sorted(archive.infolist(), key=lambda i: i.header_offset)
    for info in infos:
        if entry_ok(archive, info):
            return entry_end(info)
    raise RuntimeError("没有任何条目通过 CRC 校验，无法自动定位边界")


def verify(path: Path, *, expect_entries: int = 395) -> tuple[bool, list[str]]:
    """全量校验：大小、zip 可开、每个条目 CRC32。"""
    problems: list[str] = []
    size = path.stat().st_size
    if size != EXPECTED_SIZE:
        problems.append(f"大小 {size:,} != {EXPECTED_SIZE:,}")

    with path.open("rb") as handle:
        if handle.read(4) != ZIP_MAGIC:
            problems.append("开头不是 PK\\x03\\x04")
    if problems:
        return False, problems

    try:
        archive = zipfile.ZipFile(path)
    except Exception as exc:  # noqa: BLE001
        return False, [f"zip 打不开：{type(exc).__name__}: {exc}"]

    infos = archive.infolist()
    if len(infos) != expect_entries:
        problems.append(f"条目数 {len(infos)} != {expect_entries}")

    failed = [i.filename for i in infos if not entry_ok(archive, i)]
    if failed:
        problems.append(f"CRC 失败 {len(failed)} 条：{failed[:5]}")
    else:
        print(f"  CRC32 全过（{len(infos)} 条目）")
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修复 BGE-M3 权重损坏区")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--cutoff",
        type=int,
        default=0,
        help="损坏边界；0 = 自动检测（推荐，别手填）",
    )
    args = parser.parse_args(argv)

    target = args.model_dir / "pytorch_model.bin"
    if not target.exists():
        print(f"找不到 {target}")
        return 1

    print(f"[修复] {target}")
    print(f"  当前大小 {target.stat().st_size:,}")

    cutoff = args.cutoff
    if cutoff <= 0:
        print("\n[0/4] 自动检测损坏边界")
        try:
            cutoff = detect_cutoff(target)
        except Exception as exc:  # noqa: BLE001
            print(f"  检测失败：{type(exc).__name__}: {exc}")
            return 1
        print(f"  边界 = {cutoff:,}（第一个 CRC 通过的条目的头偏移）")
    print(f"  待重下前缀 {cutoff:,} 字节（占 {cutoff / target.stat().st_size * 100:.1f}%）")

    head = args.model_dir / "pytorch_model.bin.head"
    repaired = args.model_dir / "pytorch_model.bin.repaired"
    if repaired.exists():
        repaired.unlink()  # 上一轮失败产物，避免误留

    print("\n[1/4] Range 拉取缺失前缀（带断点续传，可复用已有 head）")
    try:
        fetch_prefix(cutoff, head)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        print(f"  拉取失败：{type(exc).__name__}: {exc}")
        return 1
    print(f"  已写 {head.stat().st_size:,} 字节")

    print("\n[2/4] 拼接 = 新前缀 + 原文件 [cutoff:]")
    with repaired.open("wb") as out:
        with head.open("rb") as handle:
            shutil.copyfileobj(handle, out, CHUNK)
        with target.open("rb") as handle:
            handle.seek(cutoff)
            shutil.copyfileobj(handle, out, CHUNK)
    print(f"  已写 {repaired.stat().st_size:,} 字节")

    print("\n[3/4] 全量校验拼接结果（395 条目 CRC32）")
    ok, problems = verify(repaired)
    if not ok:
        print("  校验失败，保留原文件不动：")
        for problem in problems:
            print(f"    - {problem}")
        return 1

    print("\n[4/4] 替换原文件")
    backup = args.model_dir / "pytorch_model.bin.corrupt"
    if backup.exists():
        backup.unlink()
    target.replace(backup)
    repaired.replace(target)
    head.unlink(missing_ok=True)
    print(f"  OK  损坏件留档为 {backup.name}")
    print(f"  OK  {target.name} = {target.stat().st_size:,} 字节，校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
