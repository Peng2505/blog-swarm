#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色 Skill 防漂移：磁盘上的 SKILL.md 必须与 blog_swarm.py 模板生成的完全一致。

`blog_swarm.py` 的卡正文模板是**唯一事实来源**，`skills/` 下的是它的渲染产物。
手抄的副本一定会漂移，而两条路径「分别看都很正常」——被复制的那份照样能跑，
只是口径慢慢不一样了。这和 `test_seo_mcp_parity.py` 防的是同一类腐烂，
区别只在于那边钉的是两个适配器，这边钉的是「模板 → Skill」这一条边。

跑法:
    .venv-rag3/Scripts/python.exe -m pytest tests/test_role_skills_sync.py -q
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "tools" / "build_role_skills.py"

# 允许存在、但不属于本生成器的 SKILL.md（按仓库相对 POSIX 路径写全）
KNOWN_OTHER_SKILLS: set[str] = set()


def _load_builder():
    """按路径载入生成器（tools/ 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("role_skill_builder", BUILDER)
    assert spec and spec.loader, BUILDER
    module = importlib.util.module_from_spec(spec)
    sys.modules["role_skill_builder"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


@pytest.fixture(scope="module")
def artifacts(builder) -> dict[str, str]:
    return builder.build()


def _read(path: Path) -> str:
    # newline="" 不做换行翻译，但 CRLF 归一成 LF：本仓 core.autocrlf=true 且无
    # .gitattributes，clone 后 checkout 写 CRLF，而生成器产出 LF。逐字节比对若
    # 把换行当内容，测试会在**别人 clone 后**误报漂移 —— 那是换行风格不是内容漂移。
    return path.open(encoding="utf-8", newline="").read().replace("\r\n", "\n")


# ---------------------------------------------------------------- 核心：不许漂移
def test_build_succeeds_and_is_non_empty(artifacts) -> None:
    """生成器自身必须能跑通并产出东西（校验不过会直接抛错）。"""
    assert artifacts, "生成器没产出任何产物"
    assert any(rel.endswith("SKILL.md") for rel in artifacts)


def test_every_artifact_matches_disk(builder, artifacts) -> None:
    """磁盘上的每个产物必须与「按当前模板重新生成」的结果逐字节一致。

    这条红了 = 有人改了 blog_swarm.py 的模板却没重新生成，或者手改了
    skills/ 下的产物。两种都跑 `tools/build_role_skills.py` 修。
    """
    drifted: list[str] = []
    for rel, content in artifacts.items():
        path = builder.OUT_ROOT / rel
        if not path.is_file():
            drifted.append(f"缺失 {rel}")
        elif _read(path) != content:
            drifted.append(f"漂移 {rel}")
    assert not drifted, (
        "角色 Skill 与模板不一致，跑 tools/build_role_skills.py 重新生成：\n  "
        + "\n  ".join(drifted)
    )


def test_no_orphan_role_skills(builder, artifacts) -> None:
    """反向：磁盘上不能有生成器不认识的角色 Skill（残留/改名留下的一律报错）。"""
    expected = {
        str(Path(rel).as_posix()) for rel in artifacts if rel.endswith("SKILL.md")
    }
    found = {
        p.relative_to(builder.OUT_ROOT).as_posix()
        for p in builder.OUT_ROOT.rglob("SKILL.md")
    }
    orphans = sorted(found - expected - KNOWN_OTHER_SKILLS)
    assert not orphans, f"存在生成器不产出的角色 Skill（残留或改名污染）: {orphans}"


# ---------------------------------------------------------------- 契约不是空话
def test_contract_outputs_really_exist_in_templates(builder) -> None:
    """I/O 契约里声明的每个交付物，必须真的出现在对应模板正文里。

    这是「I/O Schema」不沦为装饰的关键：有人改了模板的产物路径却忘了改契约，
    这里会红。生成器的 validate() 已做同一件事，本测试把它钉进回归套件，
    以免哪天有人绕开 build() 直接用 render()。
    """
    bodies = builder.load_bodies()
    for role in builder.ROLES:
        body = bodies[role["body_const"]]
        for out in role["outputs"]:
            probe = out.split("<")[0] if "<" in out else Path(out.replace("/", "\\")).name
            assert probe in body, (
                f"{role['name']} 声明交付物 {out}（核对串 {probe!r}），"
                f"但 {role['body_const']} 里找不到"
            )


def test_each_role_owns_exactly_one_stage_and_profile(builder) -> None:
    """段号 2/3/4/5 各归一个角色、一个 profile，不许重叠或缺口。"""
    stages = [r["stage"] for r in builder.ROLES]
    assignees = [r["assignee"] for r in builder.ROLES]
    assert sorted(stages) == [2, 3, 4, 5], f"段号不完整或重复: {stages}"
    assert len(set(assignees)) == len(assignees), f"profile 重复占用: {assignees}"


def test_template_names_its_own_role_and_stage(builder) -> None:
    """模板正文里必须出现自己的段号与角色名 —— 防止把模板配错角色。"""
    bodies = builder.load_bodies()
    for role in builder.ROLES:
        body = bodies[role["body_const"]]
        assert f"第 {role['stage']}/5 段" in body, f"{role['name']}: 段号与模板不符"
        assert role["assignee"] in body, f"{role['name']}: 模板里没有角色名"


# ---------------------------------------------------------------- 产物格式
def test_frontmatter_is_valid_and_description_is_short(artifacts) -> None:
    """Frontmatter 必须合法、description 必须 ≤60 字符且以句号结尾。

    60 是 Hermes 仓库的硬线（校验器只查 1024，但索引在第 57 字符处截断，
    触发语必须能自包含在那之前）。
    """
    for rel, content in artifacts.items():
        if not rel.endswith("SKILL.md"):
            continue
        assert content.startswith("---"), f"{rel}: frontmatter 不在第 0 字节"
        match = re.search(r"\n---\s*\n", content[3:])
        assert match, f"{rel}: frontmatter 没有闭合"
        fm_text = content[3 : match.start() + 3]
        fields = dict(
            re.findall(r"^(\w+):\s*(.+)$", fm_text, flags=re.M)
        )
        for key in ("name", "description", "version", "author", "platforms"):
            assert key in fields, f"{rel}: frontmatter 缺 {key}"
        desc = fields["description"].strip().strip('"')
        assert len(desc) <= 60, f"{rel}: description {len(desc)} 字符，硬线是 60"
        assert desc.endswith("."), f"{rel}: description 必须以句号结尾"


def test_generated_file_says_it_is_generated(artifacts) -> None:
    """产物必须自带「勿手改 + 来源」提示，否则下一个人就会手改它。"""
    for rel, content in artifacts.items():
        if rel.endswith("SKILL.md"):
            assert "build_role_skills.py" in content and "请勿手改" in content, rel


def test_io_contract_json_is_machine_readable(artifacts) -> None:
    """references/io-contract.json 必须可解析，且带模板体哈希。"""
    import json

    seen: set[str] = set()
    for rel, content in artifacts.items():
        if not rel.endswith("io-contract.json"):
            continue
        payload = json.loads(content)
        for key in ("skill", "stage", "assignee", "outputs", "body_sha256"):
            assert key in payload, f"{rel}: 缺 {key}"
        assert len(payload["body_sha256"]) == 64, f"{rel}: body_sha256 不是 sha256"
        assert payload["skill"] not in seen, f"{rel}: skill 名重复"
        seen.add(payload["skill"])
