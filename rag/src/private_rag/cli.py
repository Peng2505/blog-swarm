from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .citations import add_private_hits, add_public_source, verify_citations
from .embeddings import HashingEmbedding, SentenceTransformerEmbedding
from .store import KnowledgeBase


DEFAULT_SOURCE = Path(r"D:\blog-knowledge\documents")
DEFAULT_DB = Path(r"D:\blog-knowledge\index")


def _embedding(name: str, model: str | None):
    if name == "hashing":
        return HashingEmbedding()
    if name == "semantic":
        return SentenceTransformerEmbedding(
            model or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    raise ValueError(f"unsupported embedding: {name}")


def _add_kb_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--embedding", choices=["hashing", "semantic"], default="hashing")
    parser.add_argument("--model")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blog-rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="incrementally index private documents")
    _add_kb_options(index)

    query = subparsers.add_parser("query", help="retrieve private evidence")
    query.add_argument("query")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--ledger", type=Path)
    _add_kb_options(query)

    verify = subparsers.add_parser("verify", help="verify a draft citation ledger")
    verify.add_argument("--draft", type=Path, required=True)
    verify.add_argument("--ledger", type=Path, required=True)
    verify.add_argument("--db", type=Path, default=DEFAULT_DB)

    public = subparsers.add_parser("add-public", help="record a public source and exact quote")
    public.add_argument("--ledger", type=Path, required=True)
    public.add_argument("--title", required=True)
    public.add_argument("--url", required=True)
    public.add_argument("--evidence", required=True)
    public.add_argument("--snapshot", type=Path, required=True)
    public.add_argument("--locator", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "index":
            kb = KnowledgeBase(args.db, embedding=_embedding(args.embedding, args.model))
            stats = kb.sync(args.source)
            print(json.dumps({"ok": True, **asdict(stats)}, ensure_ascii=False))
            return 0

        if args.command == "query":
            kb = KnowledgeBase(args.db, embedding=_embedding(args.embedding, args.model))
            sync = kb.sync(args.source)
            hits = kb.query(args.query, top_k=args.top_k)
            citation_ids = (
                add_private_hits(args.ledger, args.source, hits) if args.ledger else [None] * len(hits)
            )
            payload_hits = []
            for hit, citation_id in zip(hits, citation_ids):
                item = asdict(hit)
                item["citation_id"] = citation_id
                payload_hits.append(item)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "query": args.query,
                        "embedding": kb.embedding.name,
                        "sync": asdict(sync),
                        "hits": payload_hits,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        if args.command == "verify":
            report = verify_citations(args.draft, args.ledger, index_db=args.db)
            print(json.dumps(asdict(report), ensure_ascii=False))
            return 0 if report.ok else 2

        if args.command == "add-public":
            citation_id = add_public_source(
                args.ledger,
                title=args.title,
                url=args.url,
                evidence=args.evidence,
                snapshot_path=args.snapshot,
                locator=args.locator,
            )
            print(json.dumps({"ok": True, "citation_id": citation_id}, ensure_ascii=False))
            return 0

        raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
