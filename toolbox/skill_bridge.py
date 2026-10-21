"""Claude skill 桥接：把 SKILL.md 目录编目为可检索的"知识工具"。

扫描目录（默认 <项目根>/.claude/skills；环境变量 AGENT_SKILL_DIRS 可覆盖，
分号分隔多个目录，设为空串则完全关闭）。刻意不含用户主目录：
skill 目录是内容来源，默认只认本项目自带的，避免把机器上无关的
（尤其是安全类）skill 混进通用智能体的工具表。每个 skill 在注册表里是
一个伪工具：
  - search_tools 能搜到（frontmatter 的 name/description 参与检索）
  - run_tool 执行 = 返回 SKILL.md 正文（走统一归一化：超长截断+read_output 钻取）
模型拿到正文后按其中的流程行事，继续调用其他工具完成步骤。

兼容边界：**知识/方法论型 skill 完整兼容**；skill 若要求执行脚本/命令，
还需要 toolbox 提供执行类基础工具。
"""
import os
import re
from pathlib import Path

import yaml

from registry import Tool

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _skill_dirs() -> list:
    if (env := os.environ.get("AGENT_SKILL_DIRS")) is not None:
        return [Path(p) for p in env.split(";") if p.strip()]
    project = Path(__file__).resolve().parent.parent / ".claude" / "skills"
    return [project] if project.is_dir() else []


def _parse(md_path: Path):
    text = md_path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, m.group(2)


def catalog() -> dict:
    """扫描 skill 目录 → 注册表条目（category=skill，readonly）。"""
    out = {}
    for d in _skill_dirs():
        for md in sorted(d.glob("*/SKILL.md")):
            meta, _body = _parse(md)
            name = meta.get("name") or md.parent.name
            desc = (meta.get("description") or "").strip()
            out[name] = Tool(
                name=name,
                description=desc[:60] or "Claude skill（无描述）",
                category="skill",
                danger="readonly",
                keywords=[name.lower()],
                search_text=f"{name} {desc}",   # 描述全文参与检索召回
                example={},
                schema={"type": "object", "properties": {}},
                timeout=30,
                requires=[],
                path=md,
                source="skill")
    return out


def body_of(tool: Tool) -> str:
    """执行 skill = 返回 SKILL.md 正文（流程指令）。"""
    _meta, body = _parse(tool.path)
    return body.strip() or "(skill 正文为空)"
