"""技能库（Skill 栏后端）。

gateway/skills/<name>/SKILL.md + 可选参考文件（refs/ 等任意层级）。
SKILL.md 带 YAML frontmatter（name/description），正文是**索引式**内容：
写清这个技能做什么、按什么流程走，细节只给文件相对路径——注入块里会附上
绝对路径文件索引，智能体读到后按需用 file 工具取全文，不整包塞上下文。

勾选注入走 system 通道（extra_system，仅当次任务生效，不入会话记忆）：
  【skill·<name>】
  <SKILL.md 正文>
  [参考文件] <绝对路径清单>

管理：GET /api/skills 列表（每次现扫目录，热更新）/ GET /api/skills/{name}
详情 / POST /api/skills 新建或改写 / DELETE /api/skills/{name} 删目录。
"""
import re
import shutil
import uuid
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# 目录名即技能名：ASCII 字母数字-_（23 个攻击技能包等外部包天然满足），
# 顺带挡掉路径穿越（../、盘符、反斜杠都进不来）
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,39}$")
MAX_FILES_LISTED = 60          # 文件索引最多列多少条（防超长注入块）
MAX_CONTENT_CHARS = 40000      # 单技能注入上限，超出截断并提示


def _safe_name(raw) -> str | None:
    name = str(raw or "").strip()
    return name if NAME_RE.match(name) else None


def _parse_card(md_text: str) -> tuple[dict, str]:
    """拆 frontmatter 与正文；无 frontmatter 时整篇当正文。"""
    text = md_text.lstrip("﻿")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[m.end():].strip()


def _dir_files(skill_dir: Path) -> list[Path]:
    """技能目录全部文件（含 SKILL.md 本身），相对序 + 大小稳定排序。"""
    return sorted((p for p in skill_dir.rglob("*") if p.is_file()),
                  key=lambda p: (p.relative_to(skill_dir).as_posix().lower(),
                                 p.stat().st_size))


def list_skills() -> list[dict]:
    """扫描技能目录（每次现扫 = 热更新；往目录丢新包立即可见）。"""
    if not SKILLS_DIR.exists():
        return []
    out = []
    for d in sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or not _safe_name(d.name):
            continue
        card = d / "SKILL.md"
        if not card.exists():
            continue
        meta, body = _parse_card(card.read_text(encoding="utf-8", errors="replace"))
        name = _safe_name(meta.get("name")) or d.name
        out.append({"name": name,
                    "description": str(meta.get("description") or "")[:300],
                    "chars": len(body),
                    "files": len(_dir_files(d)) - 1})     # 减掉 SKILL.md 自身
    return out


def get_skill(name: str) -> dict | None:
    skill_dir = SKILLS_DIR / (name or "")
    if not _safe_name(name) or not (skill_dir / "SKILL.md").exists():
        return None
    meta, body = _parse_card((skill_dir / "SKILL.md").read_text(
        encoding="utf-8", errors="replace"))
    files = _dir_files(skill_dir)
    return {"name": _safe_name(meta.get("name")) or name,
            "description": str(meta.get("description") or "")[:300],
            "content": body,
            "files": [{"path": str(p), "name": p.relative_to(skill_dir).as_posix(),
                       "size": p.stat().st_size} for p in files]}


def save_skill(name: str, description: str, content: str) -> dict:
    """新建/整体改写一个技能（只动 SKILL.md，参考文件手工放进目录）。"""
    safe = _safe_name(name)
    if not safe:
        raise ValueError(f"技能名只能用字母数字-_（1~40 字符）: {name!r}")
    skill_dir = SKILLS_DIR / safe
    skill_dir.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump({"name": safe,
                            "description": str(description or "").strip()[:300]},
                           allow_unicode=True, sort_keys=False).strip()
    (skill_dir / "SKILL.md").write_text(
        f"---\n{front}\n---\n\n{str(content or '').strip()}\n", encoding="utf-8")
    return get_skill(safe)


def delete_skill(name: str) -> bool:
    skill_dir = SKILLS_DIR / (name or "")
    if not _safe_name(name) or not skill_dir.exists():
        return False
    shutil.rmtree(skill_dir)
    return True


def render(names: list) -> tuple[str, list]:
    """把勾选的技能渲染成注入块。

    返回 (拼接后的 extra_system 文本, [(name, 注入块)] 供逐个发 skill_injected 事件)。
    不存在的技能名静默跳过（前端只该送列表里有的；旧勾选被删后自然失效）。
    """
    blocks, emitted = [], []
    seen = set()
    for raw in names or []:
        name = _safe_name(raw)
        if not name or name in seen:
            continue
        skill = get_skill(name)
        if not skill:
            continue
        seen.add(name)
        body = skill["content"] or "(空技能)"
        if len(body) > MAX_CONTENT_CHARS:
            body = body[:MAX_CONTENT_CHARS] + f"\n…(截断，全文 {len(skill['content'])} 字，按下面路径读)"
        refs = [f for f in skill["files"] if f["name"] != "SKILL.md"][:MAX_FILES_LISTED]
        lines = [f"【skill·{name}】", body, ""]
        if refs:
            lines.append("[参考文件] 需要细节时用 file 工具按绝对路径读全文:")
            lines += [f"  - {f['path']}  ({f['name']}, {f['size']}B)" for f in refs]
        else:
            lines.append("[参考文件] 无（技能内容已自包含）")
        block = "\n".join(lines)
        blocks.append(block)
        emitted.append((name, block))
    return "\n\n".join(blocks), emitted
