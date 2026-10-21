"""工具注册表：tools/<name>/tool.yaml 即一个工具（目录约定见 tools/README.md）。

每次检索/执行都重扫目录——热加载：新增工具丢个目录进去即可，不改代码、不重启。

工具种类（tool.yaml 的 kind）：
  script   默认。目录里的 run.py 就是实现
  http     声明式 HTTP 调用（tool.yaml 的 http 段），由 kinds/http_kind.py 执行
  command  声明式命令模板（tool.yaml 的 command 段），由 kinds/command_kind.py 执行
origin: builtin（随项目发布）/ user（在网页「工具工坊」里创建，可编辑删除）

工具集过滤（网关按会话/任务下发）：
  AGENT_TOOLS_DENY   逗号分隔，全局禁用的工具
  AGENT_TOOLS_ALLOW  逗号分隔，当前预设允许的工具；未设或 "*" = 不限
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TOOLS_DIR = Path(__file__).resolve().parent / "tools"
MAX_RESULTS = 5          # 默认最多返回 5 张工具卡
KINDS = ("script", "http", "command")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")


@dataclass
class Tool:
    name: str
    description: str
    category: str
    danger: str
    keywords: list = field(default_factory=list)
    search_text: str = ""
    example: dict = field(default_factory=dict)
    schema: dict = field(default_factory=dict)
    timeout: int = 600
    requires: list = field(default_factory=list)
    path: Path = None
    source: str = "builtin"   # builtin / skill / mcp:<源名>
    kind: str = "script"
    origin: str = "builtin"   # builtin / user
    spec: dict = field(default_factory=dict)   # http / command 声明段
    # 捆绑工具（mcp_bridge.bundle_tool）专用：操作名 → danger。
    # 网关 danger_of 靠它对 ida(op) 逐操作判读/写，防兜圈逻辑不失真。
    op_danger: dict = field(default_factory=dict)


def load_tools() -> dict:
    tools = {}
    for yml in sorted(TOOLS_DIR.glob("*/tool.yaml")):
        try:
            meta = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue          # 坏工具不能搞挂整个注册表
        if not meta.get("name"):
            continue
        kind = meta.get("kind", "script")
        if kind not in KINDS:
            continue
        tools[meta["name"]] = Tool(
            name=meta["name"],
            description=meta.get("description", ""),
            category=meta.get("category", "misc"),
            danger=meta.get("danger", "unknown"),
            keywords=[str(k) for k in meta.get("keywords") or []],
            search_text=meta.get("search_text", ""),
            example=meta.get("example") or {},
            schema=meta.get("schema") or {"type": "object", "properties": {}},
            timeout=int(meta.get("timeout", 600)),
            requires=meta.get("requires") or [],
            path=yml.parent,
            kind=kind,
            origin=meta.get("origin", "builtin"),
            spec=(meta.get(kind) or {}) if kind != "script" else {},
            op_danger={str(k): str(v) for k, v in (meta.get("op_danger") or {}).items()},
        )
    return tools


def _env_set(name: str) -> set | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() in ("", "*"):
        return None
    return {x.strip() for x in raw.split(",") if x.strip()}


def apply_toolset(tools: dict) -> dict:
    """按网关下发的工具集过滤。被过滤的工具对模型完全不可见（检索不到、也执行不了）。"""
    deny = _env_set("AGENT_TOOLS_DENY") or set()
    allow = _env_set("AGENT_TOOLS_ALLOW")
    return {n: t for n, t in tools.items()
            if n not in deny and (allow is None or n in allow)}


# ═══ 检索 ═══

def _tokens(query: str) -> list:
    """整句 + 空格分词 + 中文二元组（"写入文件" → 写入/入文/文件），
    解决中文无空格导致的"整句不命中任何关键词"问题。"""
    q = query.strip().lower()
    out = []
    for t in [q, *q.split()]:
        if t and t not in out:
            out.append(t)
    for run in _CJK_RUN.findall(q):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in out:
                out.append(bg)
    return out


def _score(tool: Tool, tokens: list, query: str = "") -> int:
    """关键词是检索权重的主体。整句查询时额外做"关键词出现在原句里"的反向匹配——
    长句"把结果写入到文件里"切不出"写文件"，但关键词"写入""保存"会直接出现在原句中。
    关键词越长越具体，分越高（"写入"比"文件"更能区分 file_write 和 file_read）。"""
    score = 0
    name = tool.name.lower()
    kws = [k.lower() for k in tool.keywords]
    text = f"{tool.description} {tool.search_text}".lower()
    for t in tokens:
        if t == name:
            score += 10
        elif t in name:
            score += 3
        if any(t == k for k in kws):
            score += 4
        elif any(t in k for k in kws):
            score += 2
        if t in text:
            score += 1
    q = query.strip().lower()
    for k in kws:
        if len(k) >= 2 and k in q:
            score += 2 + len(k)
    return score


def search(query: str, category: str = "", tools: dict = None,
           limit: int = MAX_RESULTS) -> list:
    """返回 [(tool, score)]。空查询 = 按分类列出全部（便于模型"先看看有什么"）。"""
    tools = tools if tools is not None else load_tools()
    if category:
        tools = {n: t for n, t in tools.items() if t.category == category}
    if not query.strip():
        return [(t, 0) for t in sorted(tools.values(), key=lambda t: (t.category, t.name))][:limit]
    tokens = _tokens(query)
    scored = sorted(((t, _score(t, tokens, query)) for t in tools.values()),
                    key=lambda p: (-p[1], p[0].name))
    return [(t, s) for t, s in scored if s > 0][:limit]


def categories(tools: dict = None) -> dict:
    tools = tools if tools is not None else load_tools()
    out = {}
    for t in tools.values():
        out.setdefault(t.category, []).append(t.name)
    return dict(sorted(out.items()))


# ═══ 工具卡 ═══

def _type_of(spec: dict) -> str:
    if "enum" in spec:
        vals = [str(x) for x in spec["enum"]]
        # 签名是模型选值的依据，截断会让它以为后面的值不存在；过长才折叠
        return "|".join(vals) if len(vals) <= 16 else "|".join(vals[:15]) + f"|…(+{len(vals) - 15})"
    t = spec.get("type", "any")
    if t == "array":
        return f"{_type_of(spec.get('items') or {})}[]"
    return {"string": "str", "integer": "int", "number": "num",
            "boolean": "bool", "object": "obj"}.get(t, str(t))


def signature(tool: Tool) -> str:
    """一行签名：file_read(path: str, offset?: int, limit?: int)。
    检索结果用它代替整份 JSON Schema，一张卡从 ~150 token 降到 ~40。"""
    props = (tool.schema or {}).get("properties") or {}
    req = set((tool.schema or {}).get("required") or [])
    parts = [f"{k}{'' if k in req else '?'}: {_type_of(v)}" for k, v in props.items()]
    return f"{tool.name}({', '.join(parts)})"


def card(tool: Tool, detail: bool = False) -> dict:
    """工具卡。检索用紧凑卡（签名 + 一句描述 + 示例）；detail=True 或 dump 时带完整 schema。"""
    c = {"name": tool.name, "sig": signature(tool), "description": tool.description,
         "category": tool.category, "danger": tool.danger}
    if tool.example:
        c["example"] = tool.example
    if detail:
        c["params_schema"] = tool.schema
        if tool.search_text:
            c["usage"] = tool.search_text
        if tool.op_danger:
            # 捆绑工具的逐操作副作用表（网关 LoopGuard 用；模型也可据此分辨读写）
            c["op_danger"] = tool.op_danger
    if tool.source != "builtin":
        c["source"] = tool.source   # 桥接来源，让模型知道这不是本机工具
    return c


def admin_card(tool: Tool) -> dict:
    """管理页用的全量信息（不进模型上下文）。"""
    return {**card(tool, detail=True), "keywords": tool.keywords, "search_text": tool.search_text,
            "timeout": tool.timeout, "requires": tool.requires, "kind": tool.kind,
            "origin": tool.origin, "source": tool.source,
            "editable": tool.origin == "user" and tool.source == "builtin"}
