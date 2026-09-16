"""从 ModelScope 拉 BGE-M3 权重（HuggingFace 在本机不可达）。

    python tools/fetch_bge_m3.py [目标目录]
    python tools/fetch_bge_m3.py --verify [模型目录]     # 只校验，不下载

特点：断点续传（2.3GB 断一次不用从头来）、已完整的文件跳过、每个文件独立重试、
**下载完做内容级完整性校验**。只拉 sentence-transformers 密集检索需要的文件；
colbert/sparse 的多向量权重不拉。

为什么必须做内容校验（血的教训，2026-09-16）：
  原版只比对文件大小。但"大小相等"是必要不充分条件 —— 当早期版本的 Range 判定
  出错时，文件内容会整体前移（把 source[X:] 写在偏移 0），大小变成 T-X；
  修好 Range 判定后，续传按"当前文件大小"续，于是尾部接上了正确数据，最终大小
  恰好等于 T，头部 1.02GB 却仍然是错位内容。
  实测后果：pytorch_model.bin 大小 2,271,145,830 完全正确，但 torch/transformers
  完全读不了（开头不是 PK\\x03\\x04），只有 392/395 个 zip 条目 CRC 通过。
  所以校验必须落到**内容**：zip 结构 + 每个条目的 CRC32。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

REPO = "BAAI/bge-m3"
REVISION = "master"
BASE = f"https://www.modelscope.cn/api/v1/models/{REPO}/repo?Revision={REVISION}&FilePath="

FILES = [
    ("config.json", 687),
    ("config_sentence_transformers.json", 123),
    ("modules.json", 349),
    ("sentence_bert_config.json", 54),
    ("1_Pooling/config.json", None),
    ("tokenizer.json", 17_098_108),
    ("tokenizer_config.json", 444),
    ("special_tokens_map.json", 964),
    ("sentencepiece.bpe.model", 5_069_051),
    ("pytorch_model.bin", 2_271_145_830),
]

CHUNK = 1 << 22


def _range_satisfied(status: int, content_range: str | None) -> bool:
    """服务端是否真的只返回了请求的那一段。

    ModelScope 的接口会为 Range 请求返回**部分内容**并带上 Content-Range，
    但状态行写的是 200 而不是 206。只认 206 会把已下载进度当成"服务端发了全量"，
    于是每次重试都 truncate 重来（实测 800MB 被打回 303MB）。
    """
    return status == 206 or bool(content_range)


def zip_entries_ok(path: Path) -> tuple[bool, str]:
    """torch zip 格式权重的内容校验：每个条目的 CRC32 都必须对。"""
    try:
        archive = zipfile.ZipFile(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"不是有效 zip：{type(exc).__name__}: {exc}"

    infos = archive.infolist()
    failed: list[str] = []
    for info in infos:
        try:
            with archive.open(info.filename) as handle:
                crc = 0
                while True:
                    block = handle.read(CHUNK)
                    if not block:
                        break
                    crc = zlib.crc32(block, crc)
            if crc != info.CRC:
                failed.append(info.filename)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{info.filename}({type(exc).__name__})")

    if failed:
        return False, f"CRC 失败 {len(failed)}/{len(infos)} 条：{failed[:3]}"
    return True, f"{len(infos)} 条目 CRC32 全过"


def verify_file(rel: str, path: Path, expected: int | None) -> tuple[bool, str]:
    """大小 + 内容双重校验。"""
    if not path.exists():
        return False, "文件不存在"
    size = path.stat().st_size
    if expected is not None and size != expected:
        return False, f"大小 {size:,} != {expected:,}"
    if rel.endswith(".bin"):
        return zip_entries_ok(path)
    if rel.endswith(".json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return False, f"JSON 解析失败：{type(exc).__name__}: {exc}"
    return True, f"{size:,} bytes"


def fetch_one(rel: str, expected: int | None, dest_dir: Path) -> None:
    target = dest_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        ok, why = verify_file(rel, target, expected)
        if ok:
            print(f"  skip  {rel}  ({why})", flush=True)
            return
        print(f"  BAD   {rel}  ({why}) -> 删除重下", flush=True)
        target.unlink()

    for attempt in range(1, 8):
        have = target.stat().st_size if target.exists() else 0
        request = urllib.request.Request(BASE + urllib.parse.quote(rel))
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = bool(have) and _range_satisfied(
                    response.status, response.headers.get("Content-Range")
                )
                if append:
                    written = have
                else:
                    have = 0
                    written = 0
                    print(
                        f"  FULL  {rel}  (服务端未按 Range 返回，从头写)",
                        flush=True,
                    )
                mode = "ab" if append else "wb"
                with open(target, mode) as handle:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
                        if expected and written % (128 << 20) < CHUNK:
                            print(
                                f"    {rel}  {written/1e6:.0f}/{expected/1e6:.0f} MB "
                                f"({written/expected*100:.0f}%)",
                                flush=True,
                            )

            ok, why = verify_file(rel, target, expected)
            if not ok:
                print(f"  RETRY {rel}: 校验不过（{why}）", flush=True)
                # 内容错了，不能续传（续传只会把错误固化得更深），整个删掉重来
                target.unlink()
                continue
            print(f"  done  {rel}  ({why})", flush=True)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  retry {rel} (attempt {attempt}): {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3 * attempt)
    raise SystemExit(f"failed to download {rel}")


def verify_all(dest: Path) -> int:
    print(f"校验 {dest}", flush=True)
    bad = 0
    for rel, expected in FILES:
        ok, why = verify_file(rel, dest / rel, expected)
        print(f"  {'OK  ' if ok else 'BAD '} {rel}  ({why})", flush=True)
        bad += 0 if ok else 1
    print(f"\n{'全部通过' if not bad else f'{bad} 个文件有问题'}", flush=True)
    return 0 if not bad else 1


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--verify":
        dest = Path(args[1]) if len(args) > 1 else Path(r"D:\blog-knowledge\models\bge-m3")
        return verify_all(dest)

    dest = Path(args[0]) if args else Path(r"D:\blog-knowledge\models\bge-m3")
    dest.mkdir(parents=True, exist_ok=True)
    print(f"下载 BGE-M3 -> {dest}", flush=True)
    for rel, expected in FILES:
        fetch_one(rel, expected, dest)
    total = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"\n完成，共 {total/1e9:.2f} GB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
