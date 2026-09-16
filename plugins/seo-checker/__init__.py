"""seo-checker — SEO metadata quality check for the blog pipeline.

Registers the ``check_seo`` tool (toolset ``blog_tools``) so the reviewer
worker in the Kanban blog pipeline can grade a post's frontmatter and body
before it reaches the publisher.

Design notes (why this is not a one-liner):

* **Chinese-aware.** Thresholds are char counts tuned per script, because one
  CJK character occupies roughly two Latin characters of SERP snippet width.
  A 20-char minimum is right for an English title and wrong for a Chinese one,
  where a 20-char title is already long.
* **Keyword density needs a keyword.** Splitting on whitespace to invent one
  does not work for Chinese text (there are no spaces), so density is only
  computed when the caller supplies ``keyword`` (or a tag to fall back on);
  otherwise the check is reported as skipped rather than silently passing.
* **Never raises.** Empty title, empty body, malformed markdown — all become
  structured findings, because a handler that raises into the tool runtime is
  worse than a handler that reports "could not check".
"""

from __future__ import annotations

import json
import re

_MAX_CONTENT = 20000  # bound the work; 20k chars is far past useful signal
_DENSITY_MIN_BODY = 800  # below this, a density band is sampling noise, not a finding

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_H1 = re.compile(r"^[ \t]{0,3}#[ \t]+\S", re.M)
_H2 = re.compile(r"^[ \t]{0,3}##[ \t]+\S", re.M)
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)")
# Opening/closing fence of a fenced code block: up to 3 leading spaces, then
# 3+ backticks or 3+ tildes (optionally followed by an info string).
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")

# Heuristic char-count windows. 1 CJK char ~ 2 Latin chars of snippet width.
THRESHOLDS = {
    "zh": {"title_min": 12, "title_max": 32, "desc_min": 60, "desc_max": 120},
    "mixed": {"title_min": 15, "title_max": 45, "desc_min": 55, "desc_max": 140},
    "en": {"title_min": 20, "title_max": 70, "desc_min": 50, "desc_max": 160},
}

# Chars that carry no SEO signal but do occupy snippet width.
_FILLER = "　 \t\r\n"

IDEAL_TAGS_MIN = 3
IDEAL_TAGS_MAX = 6


def _detect_lang(text: str) -> str:
    """Classify by CJK share of visible characters: zh / mixed / en."""
    visible = [c for c in text if c not in _FILLER and not c.isspace()]
    if not visible:
        return "zh"  # this pipeline is a Chinese blog; default to zh rules
    cjk = sum(1 for c in visible if _CJK.match(c))
    ratio = cjk / len(visible)
    if ratio >= 0.25:
        return "zh"
    if ratio >= 0.05:
        return "mixed"
    return "en"


def _visible_len(text: str) -> int:
    """Length ignoring whitespace — what a snippet actually renders."""
    return len(re.sub(r"\s+", "", text or ""))


def _count_keyword(text: str, keyword: str) -> int:
    """Case-insensitive occurrence count; substring-based so CJK works."""
    if not text or not keyword:
        return 0
    return text.lower().count(keyword.lower())


def _first_paragraph(content: str) -> str:
    """First non-heading, non-code block of body text."""
    in_fence = False
    for raw in (content or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#") or line.startswith("|"):
            continue
        return line
    return ""


def _strip_fenced_blocks(content: str) -> str:
    """Body text with fenced code blocks cut out (fence lines included).

    ``^[ \\t]{0,3}#`` matches the comment line of a ```python block just as well
    as a markdown heading, so heading checks must never read fenced lines: a
    Python snippet whose first line is ``# ...`` would otherwise be reported as
    "body contains H1" and force the writer to reword code comments.
    An unclosed fence swallows the rest of the document (CommonMark behaviour),
    which keeps a truncated payload from being read as prose.
    """
    kept: list[str] = []
    open_fence = ""
    for raw in (content or "").splitlines():
        m = _FENCE.match(raw)
        if open_fence:
            # A closing fence repeats the character and is at least as long;
            # either way the line is inside the block and gets dropped.
            if m and m.group(1)[0] == open_fence[0] and len(m.group(1)) >= len(open_fence):
                open_fence = ""
            continue
        if m:
            open_fence = m.group(1)
            continue
        kept.append(raw)
    return "\n".join(kept)


def handle_check_seo(args: dict, **kwargs) -> str:
    """Handler for check_seo. Always returns a JSON string, never raises."""
    try:
        args = args or {}
        title = str(args.get("title") or "").strip()
        description = str(args.get("description") or "").strip()
        content = str(args.get("content") or "")[:_MAX_CONTENT]
        slug = str(args.get("slug") or "").strip()
        keyword = str(args.get("keyword") or "").strip()
        raw_tags = args.get("tags") or []
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in re.split(r"[,\s]+", raw_tags) if t.strip()]
        elif isinstance(raw_tags, (list, tuple)):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []

        issues: list[str] = []
        warnings: list[str] = []
        skipped: list[str] = []
        metrics: dict = {}

        # ---- language + thresholds -------------------------------------
        sample = " ".join((title, description, content[:500]))
        lang = _detect_lang(sample)
        th = THRESHOLDS[lang]

        # ---- title -----------------------------------------------------
        title_len = _visible_len(title)
        metrics["title_chars"] = title_len
        if not title:
            issues.append("Title is empty — Hugo frontmatter has no title to render as H1.")
        else:
            if title_len < th["title_min"]:
                issues.append(
                    f"Title too short ({title_len} chars, {lang} floor {th['title_min']}) "
                    "— low keyword coverage in the snippet."
                )
            elif title_len > th["title_max"]:
                issues.append(
                    f"Title too long ({title_len} chars, {lang} cap {th['title_max']}) "
                    "— will be truncated in search results."
                )

        # ---- description ----------------------------------------------
        desc_len = _visible_len(description)
        metrics["description_chars"] = desc_len
        if not description:
            issues.append(
                "No per-post meta description. layouts/partials/head.html falls back to "
                "Site.Params.description, so this post ships the same generic description "
                "as every other post (duplicate meta descriptions site-wide). Add "
                "`description:` to frontmatter."
            )
        else:
            if desc_len < th["desc_min"]:
                warnings.append(
                    f"Meta description short ({desc_len} chars, {lang} floor {th['desc_min']})."
                )
            elif desc_len > th["desc_max"]:
                warnings.append(
                    f"Meta description long ({desc_len} chars, {lang} cap {th['desc_max']}) "
                    "— tail gets cut off."
                )
            if title and _visible_len(description) and description.strip() == title:
                issues.append("Meta description is identical to the title — it adds nothing.")
            elif title and description.startswith(title[: min(10, len(title))]):
                warnings.append("Meta description opens with the title — spend the opening on the hook instead.")

        # ---- tags ------------------------------------------------------
        metrics["tags_count"] = len(tags)
        if not tags:
            warnings.append("No tags — Hugo taxonomy pages and related-post links stay empty.")
        elif len(tags) > IDEAL_TAGS_MAX:
            warnings.append(f"{len(tags)} tags (> {IDEAL_TAGS_MAX}) — dilutes each taxonomy page.")
        elif len(tags) < IDEAL_TAGS_MIN:
            warnings.append(f"Only {len(tags)} tag(s) — {IDEAL_TAGS_MIN}-{IDEAL_TAGS_MAX} is the useful range.")

        # ---- slug ------------------------------------------------------
        if slug:
            metrics["slug"] = slug
            if _CJK.search(slug):
                warnings.append(
                    f"Slug '{slug}' is non-ASCII — the URL ships percent-encoded "
                    "(%E4%B8%AD%E6%96%87-shaped), which is ugly to share and to cite."
                )
            if len(slug) > 60:
                warnings.append(f"Slug is {len(slug)} chars — keep it under ~60.")

        # ---- body ------------------------------------------------------
        body_visible = _visible_len(content)
        metrics["body_chars"] = body_visible
        if not content.strip():
            skipped.append("body_and_keyword")  # nothing to inspect
            issues.append("Body content was not passed in — title/description checks only ran.")
        else:
            # Heading structure is judged on prose only: fenced code blocks are
            # removed first so `# comment` inside a snippet is not read as an H1.
            prose = _strip_fenced_blocks(content)
            h1s = len(_H1.findall(prose))
            h2s = len(_H2.findall(prose))
            metrics["h1_in_body"] = h1s
            metrics["h2_count"] = h2s
            if h1s:
                issues.append(
                    f"Body contains {h1s} H1 heading(s). The frontmatter title is the page H1; "
                    "body sections must start at H2 (##)."
                )
            if h2s < 2:
                warnings.append(
                    f"Only {h2s} H2 heading(s) in the sampled body — thin structure for a long post."
                )

            imgs = _IMG.findall(content)
            missing_alt = [src for alt, src in imgs if not alt.strip()]
            metrics["images"] = len(imgs)
            metrics["images_missing_alt"] = len(missing_alt)
            if missing_alt:
                shown = ", ".join(missing_alt[:3])
                extra = "" if len(missing_alt) <= 3 else f" (+{len(missing_alt) - 3} more)"
                issues.append(f"{len(missing_alt)} image(s) missing alt text: {shown}{extra}")

            links = _LINK.findall(content)
            metrics["links"] = len(links)
            metrics["links_external"] = sum(1 for u in links if u.startswith(("http://", "https://")))
            if not links:
                warnings.append("No outbound or internal links in the sampled body.")

        # ---- keyword ---------------------------------------------------
        if not keyword and tags:
            keyword = tags[0]
            metrics["keyword_source"] = "tags[0]"
        elif keyword:
            metrics["keyword_source"] = "param"

        if not keyword:
            skipped.append("keyword_density")
            skipped.append("keyword_placement")
        else:
            metrics["keyword"] = keyword
            occurrences = _count_keyword(content, keyword)
            metrics["keyword_occurrences"] = occurrences
            if occurrences == 0:
                issues.append(f"Keyword '{keyword}' never appears in the sampled body.")
            if body_visible:
                kw_chars = occurrences * max(1, _visible_len(keyword))
                density = round(kw_chars / body_visible * 100, 2)
                metrics["keyword_density_pct"] = density
                # A density band is meaningless on a short excerpt — a keyword
                # appearing once in 50 chars reads as 15% "stuffing". Report the
                # number, but only judge it on a body long enough to have a
                # representative distribution.
                if body_visible < _DENSITY_MIN_BODY:
                    skipped.append("keyword_density_threshold")
                    metrics["keyword_density_floor_chars"] = _DENSITY_MIN_BODY
                elif density > 3.0:
                    warnings.append(
                        f"Keyword '{keyword}' density {density}% (> 3%) reads as stuffing."
                    )
                elif density < 0.3:
                    warnings.append(
                        f"Keyword '{keyword}' density {density}% — thin for the primary term."
                    )
            if title and not _count_keyword(title, keyword):
                warnings.append(f"Keyword '{keyword}' missing from the title.")
            if description and not _count_keyword(description, keyword):
                warnings.append(f"Keyword '{keyword}' missing from the meta description.")
            first_para = _first_paragraph(content)
            if first_para and not _count_keyword(first_para, keyword):
                warnings.append(f"Keyword '{keyword}' missing from the opening paragraph.")

        score = max(0, 100 - 20 * len(issues) - 5 * len(warnings))
        return json.dumps(
            {
                "ok": not issues,
                "score": score,
                "language": lang,
                "issues": issues,
                "warnings": warnings,
                "checks_skipped": sorted(set(skipped)),
                "metrics": metrics,
                "thresholds_used": th,
                "scoring": "100 - 20 per issue - 5 per warning, floored at 0",
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # handler contract: report, never raise
        return json.dumps(
            {
                "ok": False,
                "score": 0,
                "issues": [f"check_seo internal error: {type(exc).__name__}: {exc}"],
                "warnings": [],
                "checks_skipped": [],
            },
            ensure_ascii=False,
        )


def register(ctx):
    """Wire check_seo into the registry."""
    schema = {
        "name": "check_seo",
        "description": (
            "Check SEO metadata quality for a blog post. Returns JSON with score (0-100), "
            "issues (must-fix), warnings (nice-to-have), checks_skipped and raw metrics. "
            "Chinese and English thresholds differ automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title from frontmatter"},
                "description": {
                    "type": "string",
                    "description": "Meta description (Hugo frontmatter `description`)",
                },
                "content": {
                    "type": "string",
                    "description": "Article body — first ~500 chars is enough for keyword checks, more enables structure checks (H1/H2, alt text)",
                },
                "keyword": {
                    "type": "string",
                    "description": "Primary keyword; falls back to tags[0] if omitted",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Frontmatter tags",
                },
                "slug": {"type": "string", "description": "URL filename without .md"},
            },
            "required": ["title"],
        },
    }
    ctx.register_tool(
        name="check_seo",
        toolset="blog_tools",
        schema=schema,
        handler=handle_check_seo,
        description="Check SEO metadata quality for a blog post.",
    )
