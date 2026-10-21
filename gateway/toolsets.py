"""工具配置：全局禁用 + 工具集预设 + 自建工具（工具工坊）。

gateway/toolsets.json
  {"disabled": ["run_cmd"],                     全局禁用：任何预设都用不了
   "default": "all",                            新任务默认用哪个预设
   "presets": [{"id", "name", "description", "tools": ["file_read", ...] | ["*"],
                "system": "附加给模型的用法说明（可选）"}]}

下发方式：网关拉起 toolbox 子进程时设置环境变量
  AGENT_TOOLS_DENY  = 全局禁用列表
  AGENT_TOOLS_ALLOW = 预设工具列表（"*" 不限）
toolbox 在检索/执行两处都按它过滤——被过滤的工具对模型完全不可见，也执行不了。

自建工具写到 toolbox/tools/<name>/tool.yaml（origin: user），和内置工具同一套热加载。
只允许编辑/删除 origin=user 的工具，内置工具只能禁用。
"""
import json
import re
import shutil
import uuid
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
FILE = BASE / "toolsets.json"
TOOLS_DIR = BASE.parent / "toolbox" / "tools"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
META_TOOLS = {"search_tools", "run_tool", "read_output", "list_categories",
              "dump_tools", "describe_tool"}

BUILTIN_PRESETS = [
    {"id": "all", "name": "全部工具", "description": "不限制，除全局禁用外的所有工具", "tools": ["*"],
     "builtin": True},
    {"id": "readonly", "name": "只读", "description": "只能读文件、列目录、上网查资料，不改任何东西",
     "tools": ["file_read", "file_list", "web_fetch"], "builtin": True},
    {"id": "none", "name": "纯对话", "description": "不给模型任何工具", "tools": [], "builtin": True},
]


def _default() -> dict:
    return {"disabled": [], "default": "all", "presets": []}


def load() -> dict:
    data = _default()
    if FILE.exists():
        try:
            data.update(json.loads(FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return data


def save(data: dict) -> dict:
    clean = {
        "disabled": sorted({str(x) for x in data.get("disabled") or []}),
        "default": str(data.get("default") or "all"),
        "presets": [],
    }
    for p in data.get("presets") or []:
        if p.get("builtin") or p.get("id") in {b["id"] for b in BUILTIN_PRESETS}:
            continue                          # 内置预设不落盘
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        clean["presets"].append({
            "id": str(p.get("id") or "ts_" + uuid.uuid4().hex[:8]),
            "name": name[:40],
            "description": str(p.get("description") or "")[:200],
            "tools": sorted({str(x) for x in p.get("tools") or []}),
            "system": str(p.get("system") or "")[:4000],
        })
    if clean["default"] not in {p["id"] for p in all_presets(clean)}:
        clean["default"] = "all"
    FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def all_presets(data: dict | None = None) -> list:
    data = data or load()
    return [*BUILTIN_PRESETS, *data.get("presets", [])]


def get_preset(pid: str, data: dict | None = None) -> dict:
    data = data or load()
    pid = pid or data.get("default") or "all"
    return next((p for p in all_presets(data) if p["id"] == pid), BUILTIN_PRESETS[0])


def env_for(pid: str) -> dict:
    """某预设对应的 toolbox 环境变量。"""
    data = load()
    preset = get_preset(pid, data)
    tools = preset.get("tools") or []
    return {"AGENT_TOOLS_DENY": ",".join(data.get("disabled") or []),
            # 空列表 = 纯对话；用一个不存在的名字占位，让 allow 集合非空但什么都不匹配
            "AGENT_TOOLS_ALLOW": "*" if "*" in tools else (",".join(tools) or "__none__")}


def is_empty(pid: str) -> bool:
    """纯对话预设：直接不拉起工具服务器，省一个子进程。"""
    return get_preset(pid).get("tools") == []


# ═══ 工具工坊：自建工具 ═══

class ToolSpecError(ValueError):
    pass


def validate_spec(spec: dict) -> dict:
    """校验并规整一个自建工具定义，返回可写入 tool.yaml 的 dict。"""
    name = str(spec.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ToolSpecError("工具名只能用小写字母、数字、下划线，字母开头，2-40 位")
    if name in META_TOOLS:
        raise ToolSpecError(f"{name} 是保留名")
    kind = spec.get("kind") or "http"
    if kind not in ("http", "command", "script"):
        raise ToolSpecError("kind 只能是 http / command / script")
    desc = str(spec.get("description") or "").strip()
    if not desc:
        raise ToolSpecError("描述不能为空（模型靠它决定要不要用这个工具）")
    schema = spec.get("schema") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ToolSpecError("参数 schema 必须是 type=object 的 JSON Schema")
    props = schema.get("properties") or {}
    for k in props:
        if not re.match(r"^[A-Za-z_]\w*$", k):
            raise ToolSpecError(f"参数名 {k!r} 不合法")
    try:
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(schema)
    except ImportError:
        pass
    except Exception as e:
        raise ToolSpecError(f"schema 不合法: {e}")
    out = {
        "name": name, "kind": kind, "origin": "user",
        "description": desc[:120],
        "category": str(spec.get("category") or "custom").strip()[:30] or "custom",
        "danger": spec.get("danger") if spec.get("danger") in ("readonly", "intrusive", "destructive") else "intrusive",
        "timeout": max(1, min(int(spec.get("timeout") or 60), 600)),
        "requires": [str(x) for x in spec.get("requires") or []],
        "keywords": [str(x).strip() for x in spec.get("keywords") or [] if str(x).strip()][:30],
        "search_text": str(spec.get("search_text") or "")[:2000],
        "example": spec.get("example") or {},
        "schema": schema,
    }
    if kind == "http":
        http = spec.get("http") or {}
        if not str(http.get("url") or "").startswith(("http://", "https://", "{{")):
            raise ToolSpecError("http.url 必须以 http:// 或 https:// 开头")
        out["http"] = {k: http[k] for k in ("method", "url", "headers", "query", "body", "pick", "allow_error")
                       if k in http and http[k] not in (None, "", {}, [])}
        out["http"].setdefault("method", "GET")
    elif kind == "command":
        cmd = spec.get("command") or {}
        argv = cmd.get("argv")
        if not isinstance(argv, list) or not argv or not str(argv[0]).strip():
            raise ToolSpecError("command.argv 必须是非空列表，第一项为程序名")
        out["command"] = {"argv": [str(a) for a in argv]}
        if cmd.get("stdin"):
            out["command"]["stdin"] = str(cmd["stdin"])
    return out


def _tool_dir(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ToolSpecError("非法工具名")
    return TOOLS_DIR / name


def read_user_tool(name: str) -> dict | None:
    d = _tool_dir(name)
    yml = d / "tool.yaml"
    if not yml.exists():
        return None
    meta = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    if (d / "run.py").exists():
        meta["script"] = (d / "run.py").read_text(encoding="utf-8")
    return meta


def write_user_tool(spec: dict, original: str | None = None) -> dict:
    meta = validate_spec(spec)
    d = _tool_dir(meta["name"])
    existing = d / "tool.yaml"
    if existing.exists():
        cur = yaml.safe_load(existing.read_text(encoding="utf-8")) or {}
        if cur.get("origin") != "user":
            raise ToolSpecError(f"{meta['name']} 是内置工具，不能覆盖（换个名字）")
        if original != meta["name"]:
            raise ToolSpecError(f"工具 {meta['name']} 已存在")
    if original and original != meta["name"]:           # 改名：先删旧目录
        delete_user_tool(original)
    d.mkdir(parents=True, exist_ok=True)
    if meta["kind"] == "script":
        code = str(spec.get("script") or "")
        if "def main" not in code:
            raise ToolSpecError("script 工具需要 def main(params: dict)")
        # 用户只写 main()；没写入口就套上标准外壳（UTF-8 输出 + argv 取参数）
        body = code if "__main__" in code else _SCRIPT_WRAPPER.replace("{code}", code)
        (d / "run.py").write_text(body, encoding="utf-8")
    (d / "tool.yaml").write_text(
        "# 由工具工坊生成（origin: user）。可以手改，但网页保存会覆盖本文件\n"
        + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return meta


def delete_user_tool(name: str) -> None:
    d = _tool_dir(name)
    yml = d / "tool.yaml"
    if not yml.exists():
        return
    meta = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    if meta.get("origin") != "user":
        raise ToolSpecError("内置工具不能删除，可以在工具配置里禁用")
    shutil.rmtree(d)
    data = load()                                      # 顺手从预设/禁用里摘掉
    data["disabled"] = [x for x in data["disabled"] if x != name]
    for p in data["presets"]:
        p["tools"] = [x for x in p.get("tools", []) if x != name]
    save(data)


_SCRIPT_WRAPPER = '''"""由工具工坊生成的脚本工具。约定：main(params) 打印结果到 stdout。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

{code}


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
'''
