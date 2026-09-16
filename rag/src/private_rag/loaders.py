from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader

from .schema import SourceDocument


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    return metadata, text[end + 5 :]


def _source_id(relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.replace("\\", "/").encode("utf-8")).hexdigest()[:16]
    return f"src_{digest}"


def load_documents(source_dir: str | Path) -> list[SourceDocument]:
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"knowledge source directory does not exist: {root}")
    documents: list[SourceDocument] = []
    for candidate in sorted(root.rglob("*")):
        path = candidate.resolve()
        try:
            relative_path = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"source path escapes knowledge root: {candidate}") from exc
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".pdf"}:
            continue
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(
                f"# Page {index}\n\n{page_text.strip()}"
                for index, page_text in enumerate(pages, start=1)
            )
            metadata = {"page_count": str(len(pages))}
        else:
            raw = path.read_text(encoding="utf-8")
            metadata, text = _parse_frontmatter(raw) if suffix == ".md" else ({}, raw)
        title = metadata.get("title") or path.stem
        documents.append(
            SourceDocument(
                source_id=_source_id(relative_path),
                title=title,
                relative_path=relative_path,
                text=text.strip(),
                metadata=metadata,
            )
        )
    return documents
