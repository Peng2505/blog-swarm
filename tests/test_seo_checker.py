"""Local smoke test for the seo-checker plugin handler.

Runs the handler directly (no agent, no registry) over edge cases plus a real
post from the blog repo. Exit code 0 = all expectations held.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

PLUGIN = Path(r"C:\Users\peng\AppData\Local\hermes\profiles\reviewer\plugins\seo-checker\__init__.py")
spec = importlib.util.spec_from_file_location("seo_checker_under_test", PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# 1. The exact case that crashes the original draft: empty title, non-empty body.
r = json.loads(mod.handle_check_seo({"title": "", "description": "", "content": "内容非空，用于触发原版的 IndexError。"}))
check("empty title does not raise", "internal error" not in " ".join(r["issues"]), r["issues"][:2])
check("empty title reported", any("Title is empty" in i for i in r["issues"]))

# 2. Whitespace-only title (the other crash path in the original).
r = json.loads(mod.handle_check_seo({"title": "   ", "description": "", "content": "一些正文内容。"}))
check("whitespace title does not raise", "internal error" not in " ".join(r["issues"]))

# 3. Missing args entirely.
r = json.loads(mod.handle_check_seo({}))
check("empty args returns JSON", r["ok"] is False and isinstance(r["score"], int), r["score"])
r = json.loads(mod.handle_check_seo(None))
check("None args returns JSON", isinstance(r, dict))

# 4. Chinese title thresholds (a good zh title must NOT be flagged).
r = json.loads(mod.handle_check_seo(
    {"title": "Kanban Swarm 实测：把多智能体协作做成可恢复任务图",
     "description": "用 Hermes 的 Kanban 看板把多智能体博客流水线拆成五段可恢复的持久任务，实测端到端跑通并复盘其中的坑。",
     "content": "## 为什么进程内的子代理撑不起长任务\n\n正文内容。\n## 拆解\n\nKanban Swarm 的另一段正文。",
     "tags": ["hermes", "kanban", "multi-agent"]}))
check("zh title not flagged", not any("Title too" in i for i in r["issues"]), r["metrics"]["title_chars"])
check("zh lang detected", r["language"] == "zh", r["language"])
check("no false keyword issue on zh (keyword from tags[0])", "keyword" in r["metrics"])

# 5. The original draft's guaranteed false positive: Chinese title + no keyword given.
r = json.loads(mod.handle_check_seo(
    {"title": "多智能体协作的持久任务图实测", "description": "中文描述" * 20, "content": "中文正文" * 30}))
check("skips density instead of inventing keyword", "keyword_density" in r["checks_skipped"])

# 5b. Tiny sample must not report a density band (the 18%-stuffing false positive).
r = json.loads(mod.handle_check_seo(
    {"title": "深度解析 LangGraph 的持久化检查点机制",
     "description": "从 PostgresSaver 的表结构讲到恢复语义，说明检查点如何在崩溃后把多智能体流程接回来。",
     "tags": ["langgraph", "persistence", "agent"],
     "content": "## 检查点是什么\n\nLangGraph 的检查点把状态写进数据库。\n\n## 恢复语义\n\n恢复时从最后一个检查点继续。"}))
check("short sample: no stuffing false positive",
      not any("stuffing" in w for w in r["warnings"]), r["warnings"])
check("short sample: still reports occurrences", r["metrics"]["keyword_occurrences"] == 1)
check("short sample: density threshold marked skipped",
      "keyword_density_threshold" in r["checks_skipped"])
check("short sample: ok is True", r["ok"] is True, r["issues"])

# 5c. A long body with the keyword once per 2000 chars must judge density normally.
long_body = ("## 小节\n\n这是一段很长的正文内容，用于让密度判定进入生效区间。" * 60) + " langgraph "
r = json.loads(mod.handle_check_seo(
    {"title": "长正文密度判定测试标题内容", "description": "描述" * 60,
     "tags": ["langgraph"], "content": long_body}))
check("long body: density judged", "keyword_density_threshold" not in r["checks_skipped"],
      r["metrics"]["body_chars"])

# 6. Real post from the blog repo: frontmatter has no description -> must be an issue.
post = Path(r"D:\AI_BLOG\content\posts\hermes-kanban-swarm.md")
if post.exists():
    raw = post.read_text(encoding="utf-8")
    fm = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    head, body = fm.group(1), fm.group(2)
    title = re.search(r"title:\s*(.+)", head).group(1).strip().strip('"')
    tags = re.search(r"tags:\s*\[(.*?)\]", head)
    tags = [t.strip() for t in tags.group(1).split(",")] if tags else []
    r = json.loads(mod.handle_check_seo({"title": title, "tags": tags, "content": body,
                                        "slug": post.stem}))
    print("\n--- real post result ---")
    print(json.dumps(r, ensure_ascii=False, indent=2)[:1200])
    check("real post: missing description is an issue",
          any("meta description" in i for i in r["issues"]))
    check("real post: no internal error", "internal error" not in " ".join(r["issues"]))
    check("real post: H2 structure detected", r["metrics"].get("h2_count", 0) >= 2,
          r["metrics"].get("h2_count"))
else:
    print(f"[SKIP] real post not found at {post}")

# 7. English article must use English thresholds.
r = json.loads(mod.handle_check_seo(
    {"title": "A Reasonably Long English Post Title For Testing",
     "description": "A meta description for English content that easily clears the fifty character floor.",
     "content": "## Section one\n\nSome English body text carrying the primary term.\n## Section two\n\nMore English text.",
     "keyword": "english"}))
check("en lang detected", r["language"] == "en", r["language"])
check("en article scores clean", r["ok"] is True, r["issues"] + r["warnings"])

# 8. Alt-text and H1 findings.
r = json.loads(mod.handle_check_seo(
    {"title": "标题长度足够的测试文章标题内容", "description": "描述" * 40,
     "content": "# 不该出现在正文的 H1\n\n![](img/a.png)\n\n## 小节一\n\n关键词：测试\n\n![有alt](img/b.png)\n## 小节二\n\n测试内容。"}))
check("body H1 flagged", any("H1 heading" in i for i in r["issues"]))
check("missing alt flagged", any("alt text" in i for i in r["issues"]))
check("alt only counts the empty one", r["metrics"]["images_missing_alt"] == 1,
      r["metrics"]["images_missing_alt"])

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("all checks passed")
