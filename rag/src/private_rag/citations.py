from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .schema import RetrievalHit


CITATION_RE = re.compile(r"\[((?:I|W)\d{2,})\]")

# Quantitative claims that must carry a citation. Narrow on purpose: this gate
# blocks the reviewer card, so a false positive wedges the pipeline. Only
# measurement-shaped claims qualify -- percentages, multipliers, money, timing
# and counted technical nouns, plus v-prefixed versions. Bare integers
# ("5 段"), bare decimals ("温度 0.2") and un-prefixed versions
# ("langgraph 1.2.11") are prose and must not trip it.
QUANTITY_RE = re.compile(
    r"(?:"
    r"\bv\d+(?:\.\d+)+\b"                       # v0.21.0 -- explicit release tag
    r"|\b\d+(?:\.\d+)?%"                        # percentages
    r"|\d+(?:\.\d+)?\s*倍"                       # multipliers ("3 倍"; not "1.96×SE")
    r"|[¥$€]\s?\d"                              # money
    r"|\d+(?:\.\d+)?\s*(?:ms|毫秒|秒|s|分钟|min|小时|h)\b"
    r"|\d+(?:\.\d+)?\s*"
    r"(?:workers?|threads?|tokens?|requests?|users?|qps|rps|并发|条|字符)"
    r")",
    re.I,
)

# Headings that end the prose body. Writers use several variants; an
# unrecognised one makes the whole reference list look like prose.
REFERENCES_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:sources|参考资料|参考文献|参考|引用来源|说明与参考)\s*$",
    re.I | re.M,
)


@dataclass(frozen=True)
class CitationReport:
    ok: bool
    issues: tuple[str, ...]
    cited_ids: tuple[str, ...]


def add_private_hits(
    ledger_path: str | Path,
    source_root: str | Path,
    hits: list[RetrievalHit],
) -> list[str]:
    path = Path(ledger_path)
    root = Path(source_root).resolve()
    if path.exists():
        ledger = json.loads(path.read_text(encoding="utf-8"))
    else:
        ledger = {"version": 1, "sources": []}
    sources: list[dict] = ledger.setdefault("sources", [])
    by_chunk = {
        str(source.get("chunk_id")): str(source["id"])
        for source in sources
        if source.get("kind") == "private" and source.get("chunk_id")
    }
    existing_numbers = [
        int(str(source["id"])[1:])
        for source in sources
        if re.fullmatch(r"I\d{2,}", str(source.get("id", "")))
    ]
    next_number = max(existing_numbers, default=0) + 1
    assigned: list[str] = []
    for hit in hits:
        if hit.chunk_id in by_chunk:
            assigned.append(by_chunk[hit.chunk_id])
            continue
        source_path = (root / hit.relative_path).resolve()
        try:
            source_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"source path escapes knowledge root: {hit.relative_path}") from exc
        citation_id = f"I{next_number:02d}"
        next_number += 1
        snapshot = path.parent / "evidence" / f"private-{citation_id}.txt"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(hit.text, encoding="utf-8")
        sources.append(
            {
                "id": citation_id,
                "kind": "private",
                "chunk_id": hit.chunk_id,
                "source_id": hit.source_id,
                "content_hash": hit.content_hash,
                "title": hit.title,
                "uri": str(source_path),
                "locator": hit.locator,
                "evidence": hit.text,
                "snapshot_path": str(snapshot.resolve()),
                "score": hit.score,
            }
        )
        by_chunk[hit.chunk_id] = citation_id
        assigned.append(citation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(path)
    return assigned


def add_public_source(
    ledger_path: str | Path,
    *,
    title: str,
    url: str,
    evidence: str,
    snapshot_path: str | Path,
    locator: str = "",
) -> str:
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("url must start with http:// or https://")
    snapshot = Path(snapshot_path).resolve()
    if not snapshot.is_file():
        raise FileNotFoundError(f"snapshot does not exist: {snapshot}")
    snapshot_text = snapshot.read_text(encoding="utf-8", errors="replace")
    clean_evidence = evidence.strip()
    if not clean_evidence or clean_evidence not in snapshot_text:
        raise ValueError("evidence must appear exactly in the snapshot")

    path = Path(ledger_path)
    if path.exists():
        ledger = json.loads(path.read_text(encoding="utf-8"))
    else:
        ledger = {"version": 1, "sources": []}
    sources: list[dict] = ledger.setdefault("sources", [])
    for source in sources:
        if (
            source.get("kind") == "public"
            and source.get("uri") == url
            and source.get("evidence") == clean_evidence
            and source.get("locator", "") == locator
        ):
            return str(source["id"])
    numbers = [
        int(str(source["id"])[1:])
        for source in sources
        if re.fullmatch(r"W\d{2,}", str(source.get("id", "")))
    ]
    citation_id = f"W{max(numbers, default=0) + 1:02d}"
    sources.append(
        {
            "id": citation_id,
            "kind": "public",
            "title": title.strip(),
            "uri": url,
            "locator": locator,
            "evidence": clean_evidence,
            "snapshot_path": str(snapshot),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(path)
    return citation_id


def _prose_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_code = False
    in_sources = False
    in_frontmatter = text.startswith("---\n")
    frontmatter_closed = not in_frontmatter
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if in_frontmatter and stripped == "---" and line_number > 1:
            in_frontmatter = False
            frontmatter_closed = True
            continue
        if not frontmatter_closed:
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if REFERENCES_HEADING_RE.match(stripped):
            in_sources = True
            continue
        if in_sources or not stripped or stripped.startswith("#"):
            continue
        lines.append((line_number, stripped))
    return lines


SENTENCE_END_RE = re.compile(r"[。！？!?；;]\s*$")


def _sentence_blocks(prose_lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join wrapped lines so a citation on the next line still covers its claim.

    Chinese technical drafts wrap mid-sentence; checking line-by-line reports a
    claim as uncited when the citation simply sits on the following line.
    """
    blocks: list[tuple[int, str]] = []
    start = 0
    buffer: list[str] = []
    for line_number, line in prose_lines:
        if not buffer:
            start = line_number
        buffer.append(line)
        # A citation already terminates its own claim: never merge past it, or
        # a later uncited sentence gets covered by the previous line's marker.
        if SENTENCE_END_RE.search(line) or CITATION_RE.search(line):
            blocks.append((start, " ".join(buffer)))
            buffer = []
    if buffer:
        blocks.append((start, " ".join(buffer)))
    return blocks


def _index_chunks(index_db: str | Path):
    """Chunk evidence recorded in the current index, for corpus binding."""
    db_dir = Path(index_db)
    if not db_dir.is_dir():
        return None
    for sqlite_file in sorted(db_dir.glob("*.sqlite3")):
        import sqlite3

        connection = sqlite3.connect(sqlite_file)
        try:
            rows = connection.execute(
                "SELECT chunk_id, content_hash FROM chunks"
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]): str(row[1]) for row in rows}
    return None


def verify_citations(
    draft_path: str | Path,
    ledger_path: str | Path,
    *,
    index_db: str | Path | None = None,
) -> CitationReport:
    draft = Path(draft_path).read_text(encoding="utf-8")
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    entries = list(ledger.get("sources", []) or [])
    issues: list[str] = []

    sources: dict[str, dict] = {}
    for entry in entries:
        citation_id = str(entry.get("id", ""))
        if citation_id in sources:
            issues.append(f"账本中的引用编号重复：{citation_id}")
            continue
        sources[citation_id] = entry
    for citation_id, entry in sources.items():
        kind = str(entry.get("kind", ""))
        if kind not in {"private", "public"}:
            issues.append(f"引用 {citation_id} 的 kind 非法：{kind!r}")
        elif kind == "private" and not citation_id.startswith("I"):
            issues.append(f"引用 {citation_id} 的 kind=private 与编号前缀不符")
        elif kind == "public" and not citation_id.startswith("W"):
            issues.append(f"引用 {citation_id} 的 kind=public 与编号前缀不符")
        if not citation_id:
            issues.append("账本中存在缺少 id 的引用记录")

    prose_lines = _prose_lines(draft)
    cited_ids = tuple(
        dict.fromkeys(
            citation_id
            for _, line in prose_lines
            for citation_id in CITATION_RE.findall(line)
        )
    )
    all_ids = tuple(dict.fromkeys(CITATION_RE.findall(draft)))
    if not prose_lines:
        issues.append("正文为空：没有可校验的散文段落")

    for citation_id in all_ids:
        if citation_id not in sources:
            issues.append(f"未知引用 {citation_id}")

    for line_number, block in _sentence_blocks(prose_lines):
        if QUANTITY_RE.search(block) and not CITATION_RE.search(block):
            issues.append(f"第 {line_number} 行：量化断言缺少引用")

    known_chunks = _index_chunks(index_db) if index_db is not None else None
    if known_chunks is not None:
        for citation_id, source in sources.items():
            if source.get("kind") != "private":
                continue
            chunk_id = str(source.get("chunk_id", ""))
            content_hash = str(source.get("content_hash", ""))
            if not chunk_id:
                issues.append(f"引用 {citation_id} 未绑定知识库 chunk")
            elif chunk_id not in known_chunks:
                issues.append(
                    f"引用 {citation_id} 的 chunk {chunk_id} 不在当前知识库索引中"
                )
            elif content_hash and known_chunks[chunk_id] != content_hash:
                issues.append(
                    f"引用 {citation_id} 的 chunk 内容与索引不一致（证据可能被改写）"
                )

    for citation_id in cited_ids:
        source = sources.get(citation_id)
        if not source:
            continue
        evidence = str(source.get("evidence", "")).strip()
        uri = str(source.get("uri", "")).strip()
        if not evidence:
            issues.append(f"引用 {citation_id} 缺少证据原文")
            continue
        if source.get("kind") == "private":
            source_path = Path(uri)
            if not source_path.is_file():
                issues.append(f"引用 {citation_id} 的私有来源不存在：{uri}")
                continue
            snapshot_value = str(source.get("snapshot_path", "")).strip()
            evidence_path = Path(snapshot_value) if snapshot_value else source_path
            if not evidence_path.is_file():
                issues.append(f"引用 {citation_id} 的私有证据快照不存在")
                continue
            source_text = evidence_path.read_text(encoding="utf-8", errors="replace")
            if evidence not in source_text:
                issues.append(f"引用 {citation_id} 的证据原文不存在于私有证据快照")
        elif source.get("kind") == "public":
            snapshot_path = Path(str(source.get("snapshot_path", "")))
            if not snapshot_path.is_file():
                issues.append(f"引用 {citation_id} 的公开来源快照不存在")
                continue
            snapshot_text = snapshot_path.read_text(encoding="utf-8", errors="replace")
            if evidence not in snapshot_text:
                issues.append(f"引用 {citation_id} 的证据原文不存在于公开来源快照")

    sources_heading = REFERENCES_HEADING_RE.search(draft)
    if cited_ids and sources_heading is None:
        issues.append("文章引用了来源但缺少 ## Sources 列表")
    elif cited_ids and sources_heading is not None:
        rendered = draft[sources_heading.end() :].upper()
        for citation_id in cited_ids:
            if citation_id in sources and f"[{citation_id}]" not in rendered:
                issues.append(f"Sources 列表缺少 {citation_id}")
    if sources_heading is not None:
        listed_ids = tuple(
            dict.fromkeys(CITATION_RE.findall(draft[sources_heading.end() :]))
        )
        for citation_id in listed_ids:
            if citation_id not in cited_ids:
                issues.append(f"Sources 列表中的 {citation_id} 未在正文引用")

    return CitationReport(not issues, tuple(issues), cited_ids)
