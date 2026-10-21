"""Read-only index for the local CyberStrike methodology Skill documents.

The tool deliberately returns small search cards and bounded excerpts.  Skill
files are reference material: this module never interprets or executes them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_RELATIVE_ROOT = Path.home() / ".local" / "share" / "cyberstrike" / "skill"
FRONTMATTER = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$", re.S)
HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.M)
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*|[\u4e00-\u9fff]{2,}")
MAX_SCAN_FILES = 20000
DEFAULT_TOP_K = 8
DEFAULT_SEARCH_CHARS = 420
DEFAULT_READ_CHARS = 5000
MAX_READ_CHARS = 12000


def _root() -> Path:
    raw = os.environ.get("CYBERSTRIKE_SKILL_ROOT", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_RELATIVE_ROOT


def _resolved_root() -> Path:
    return _root().resolve(strict=False)


def _available(root: Path) -> tuple[bool, str]:
    if not root.exists():
        return False, f"Skill 根目录不存在: {root}"
    if not root.is_dir():
        return False, f"Skill 根路径不是目录: {root}"
    return True, ""


def _paths(root: Path) -> list[Path]:
    """Return only SKILL.md files whose resolved path remains under root."""
    out: list[Path] = []
    for path in root.rglob("SKILL.md"):
        if len(out) >= MAX_SCAN_FILES:
            break
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if resolved.is_file() and resolved.name.lower() == "skill.md":
                out.append(resolved)
        except (OSError, RuntimeError, ValueError):
            continue
    return sorted(set(out), key=lambda p: p.as_posix().lower())


def _fallback_frontmatter(text: str) -> dict:
    result: dict = {}
    active_list = ""
    list_keys = {"tags", "tech_stack", "cwe_ids", "chains_with"}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if active_list and line.startswith("-"):
            result.setdefault(active_list, []).append(line[1:].strip().strip("\"'"))
            continue
        if ":" not in line:
            active_list = ""
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key in {"name", "description", "category", "version", "author"}:
            result[key] = value
            active_list = ""
        elif key in list_keys:
            if value.startswith("[") and value.endswith("]"):
                result[key] = [item.strip().strip("\"'")
                               for item in value[1:-1].split(",") if item.strip()]
                active_list = ""
            else:
                result[key] = []
                active_list = key
        else:
            active_list = ""
    return result


def _document(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    raw_meta = match.group(1)
    # Search runs over thousands of files.  The fields needed for a reference
    # card are simple scalars/lists, so avoid a full YAML parse on every call.
    meta = _fallback_frontmatter(raw_meta)
    return meta if isinstance(meta, dict) else {}, match.group(2)


def _list_value(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if value is not None and str(value).strip() else []


def _record(path: Path, root: Path, with_body: bool = True) -> dict:
    meta, body = _document(path)
    rel = path.relative_to(root).as_posix()
    name = str(meta.get("name") or path.parent.name)
    category = str(meta.get("category") or rel.split("/", 1)[0])
    title_match = HEADING.search(body)
    title = title_match.group(1).strip() if title_match else name
    description = str(meta.get("description") or "").strip()
    tags = _list_value(meta.get("tags"))
    tech_stack = _list_value(meta.get("tech_stack"))
    record = {
        "name": name,
        "title": title,
        "description": description,
        "category": category,
        "tags": tags,
        "tech_stack": tech_stack,
        "path": rel,
    }
    if with_body:
        record["body"] = body
    return record


def _unavailable(root: Path) -> dict | None:
    ok, detail = _available(root)
    if not ok:
        return {"status": "unavailable", "root": str(root), "detail": detail}
    return None


def _tokens(query: str) -> list[str]:
    q = query.strip().lower()
    tokens = [q] if q else []
    for token in WORD.findall(q):
        if token not in tokens:
            tokens.append(token)
    return tokens


def _snippet(body: str, tokens: list[str], max_chars: int) -> str:
    text = " ".join(line.strip() for line in body.splitlines() if line.strip())
    if not text:
        return ""
    lower = text.lower()
    pos = min((lower.find(token) for token in tokens if token and lower.find(token) >= 0),
              default=0)
    width = max(120, min(max_chars, 1200))
    start = max(0, pos - width // 3)
    excerpt = text[start:start + width]
    if start:
        excerpt = "..." + excerpt
    if start + width < len(text):
        excerpt += "..."
    return excerpt


def _search(root: Path, params: dict) -> dict:
    query = str(params.get("query") or "").strip()
    if not query:
        raise ValueError("search 需要 params.query")
    top_k = max(1, min(int(params.get("top_k") or DEFAULT_TOP_K), 20))
    max_chars = max(200, min(int(params.get("max_chars") or DEFAULT_SEARCH_CHARS), 1200))
    category = str(params.get("category") or "").strip().lower()
    tokens = _tokens(query)
    hits = []
    for path in _paths(root):
        try:
            record = _record(path, root, with_body=True)
        except (OSError, UnicodeError):
            continue
        if category and category not in record["category"].lower():
            continue
        hay_meta = " ".join([
            record["name"], record["title"], record["description"],
            record["category"], " ".join(record["tags"]),
            " ".join(record["tech_stack"]), record["path"]
        ]).lower()
        body = record.pop("body")
        hay_body = body.lower()
        score = 0
        for token in tokens:
            if token in record["name"].lower():
                score += 12
            if token in record["title"].lower():
                score += 6
            if token in hay_meta:
                score += 3
            if token in hay_body:
                score += 1 + min(3, hay_body.count(token))
        if score <= 0:
            continue
        record["score"] = score
        record["snippet"] = _snippet(body, tokens, max_chars)
        hits.append(record)
    hits.sort(key=lambda item: (-item["score"], item["path"].lower()))
    hits = hits[:top_k]
    return {"status": "ok", "query": query, "count": len(hits), "results": hits,
            "hint": "结果是方法参考，不代表目标存在漏洞；结合授权范围和实时证据验证。"}


def _safe_candidate(root: Path, identifier: str) -> Path:
    value = str(identifier or "").strip()
    if not value:
        raise ValueError("read 需要 params.path 或 params.skill")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("read 只接受 Skill 根目录内的相对 path")
    if candidate.name.lower() != "skill.md":
        candidate = candidate / "SKILL.md"
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("拒绝读取 Skill 根目录之外的路径") from exc
    if resolved.name.lower() != "skill.md" or not resolved.is_file():
        raise FileNotFoundError(f"Skill 不存在: {value}")
    return resolved


def _resolve_read(root: Path, params: dict) -> Path:
    if params.get("path"):
        return _safe_candidate(root, str(params["path"]))
    skill = str(params.get("skill") or "").strip()
    if not skill:
        raise ValueError("read 需要 params.path 或 params.skill")
    try:
        return _safe_candidate(root, skill)
    except FileNotFoundError:
        # Metadata names are accepted as a convenience, but are still resolved
        # through the same root and SKILL.md checks.
        for path in _paths(root):
            try:
                meta, _ = _document(path)
            except (OSError, UnicodeError):
                continue
            if str(meta.get("name") or path.parent.name).lower() == skill.lower():
                return path
        raise


def _read(root: Path, params: dict) -> dict:
    path = _resolve_read(root, params)
    _meta, body = _document(path)
    lines = body.splitlines()
    offset = max(0, int(params.get("offset") or 0))
    max_chars = max(200, min(int(params.get("max_chars") or DEFAULT_READ_CHARS), MAX_READ_CHARS))
    content = "\n".join(lines[offset:])
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]
        next_offset = offset + content.count("\n") + 1
    else:
        next_offset = None
    result = {
        "status": "ok",
        "skill": path.parent.name,
        "path": path.relative_to(root).as_posix(),
        "offset": offset,
        "content": content,
        "truncated": truncated,
        "total_lines": len(lines),
    }
    if next_offset is not None and next_offset < len(lines):
        result["next_offset"] = next_offset
    return result


def _categories(root: Path) -> dict:
    counts: dict[str, int] = {}
    attack: list[dict] = []
    for path in _paths(root):
        rel = path.relative_to(root).as_posix()
        top = rel.split("/", 1)[0]
        counts[top] = counts.get(top, 0) + 1
        if top.startswith("attack-") and "/" not in rel:
            attack.append({"name": path.parent.name, "path": rel})
    categories = [{"name": name, "count": count}
                  for name, count in sorted(counts.items(), key=lambda item: item[0].lower())]
    return {"status": "ok", "total": sum(counts.values()), "categories": categories,
            "attack_skills": sorted(attack, key=lambda item: item["name"].lower())}


def _status(root: Path) -> dict:
    missing = _unavailable(root)
    if missing:
        return missing
    paths = _paths(root)
    try:
        latest = max((p.stat().st_mtime for p in paths), default=0)
    except OSError:
        latest = 0
    return {
        "status": "ok",
        "root": str(root),
        "file_count": len(paths),
        "last_modified": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only": True,
    }


def _error(detail: str, code: int = 2) -> int:
    print(json.dumps({"status": "error", "detail": detail}, ensure_ascii=False))
    return code


def main() -> int:
    try:
        request = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except (json.JSONDecodeError, IndexError):
        return _error('参数必须是 JSON: {"op": ..., "params": {...}}')
    if not isinstance(request, dict):
        return _error("请求必须是对象")
    op = str(request.get("op") or "")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _error("params 必须是对象")
    root = _resolved_root()
    missing = _unavailable(root)
    try:
        if op == "status":
            result = _status(root)
        elif missing:
            result = missing
        elif op == "categories":
            result = _categories(root)
        elif op == "search":
            result = _search(root, params)
        elif op == "read":
            result = _read(root, params)
        else:
            return _error("未知 op: %r。可用: categories/search/read/status" % op)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return _error(str(exc))
    except Exception as exc:  # keep the model-facing error bounded
        return _error(f"{type(exc).__name__}: {exc}", 1)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
