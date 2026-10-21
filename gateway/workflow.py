"""工作流引擎（Dify 式可视化 DAG）。

数据模型（workflows/<id>.json，前端 Vue Flow 画布直接存取）：
  {id, name, description, nodes:[{id, type, position, data}], edges:[{id, source,
   target, sourceHandle}], updated}

执行语义：
  - 从唯一 start 节点出发，按边推进；节点"就绪" = 所有入边都已结算（激活或跳过）。
    分支节点（if_else / classifier）只激活命中的出口 handle，其余出边标记跳过，
    跳过沿图传播——汇合节点只要有一条入边激活就执行（Dify 同款语义）。
  - 变量引用 {{node_id.field}} / {{sys.query}} / {{env.xxx}}，节点输出存
    ctx.outputs[node_id]；变量聚合节点取第一个非空来源。
  - 同一"层"的就绪节点并行执行（asyncio.gather），天然支持并行分支。
  - iteration 节点：对列表逐项跑一个子工作流（子图 = 以 iteration 为 parent 的节点），
    loop 节点：条件满足前重复跑子图（最多 max_loops 次）。
  - 节点失败：按节点 data.on_error = fail(默认) / continue(输出 error 字段继续) /
    branch(走 fail 出口)。

运行记录 workflow_runs/<run_id>.json：每节点状态/耗时/输入/输出，前端节点上实时显示。
"""
import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from model import OpenAICompatModel

BASE = Path(__file__).resolve().parent
WORKFLOW_DIR = BASE / "workflows"
RUN_DIR = BASE / "workflow_runs"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-\.\[\]]+)\s*\}\}")
MAX_NODE_OUTPUT_CHARS = 40_000
_MISSING = object()
# 有节点外副作用的类型不做断点缓存：输出可以复用，副作用（写 final / 推回答 /
# 写 env / 追加共享对话）复现不了，漏掉比重跑（都是本地微秒级开销）危险。
NO_CACHE_TYPES = {"end", "answer", "assigner"}
NO_MANUAL_TYPES = NO_CACHE_TYPES | {"start", "note", "iteration_start", "loop_start"}


def manual_override(wf: dict, key: str, outputs, handle=None) -> dict:
    """Validate explicit substitute outputs, including empty objects/text/null fields."""
    nid = key.partition("@")[0]
    node = next((n for n in wf.get("nodes", []) if n["id"] == nid), None)
    if not node or node["type"] in NO_MANUAL_TYPES:
        raise NodeError("此节点不支持手动跳过")
    if not isinstance(outputs, dict):
        raise NodeError("跳过结果必须是输出对象，允许空对象或空字段")
    handles = None
    if node["type"] in ("if_else", "classifier"):
        data = node.get("data", {})
        field = "result" if node["type"] == "if_else" else "class_id"
        allowed = ({c["id"] for c in data.get("cases", [])} | {"else"}
                   if node["type"] == "if_else" else {c["id"] for c in data.get("classes", [])})
        handle = handle or outputs.get(field)
        if handle not in allowed:
            raise NodeError("跳过分支节点需要选择有效的继续分支")
        outputs = {**outputs, field: handle}
        handles = [handle]
    elif handle is not None:
        if handle not in ("out", "fail") or (handle == "fail" and node.get("data", {}).get("on_error") != "branch"):
            raise NodeError("无效的继续分支")
        handles = [handle]
    return {"outputs": copy.deepcopy(outputs), "handles": handles}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _graph_hash(wf: dict) -> str:
    """图结构指纹（节点 id/类型/配置 + 边）：重试时对不上就放弃缓存全新跑。"""
    core = json.dumps({
        "nodes": sorted((n.get("id"), n.get("type"), n.get("data", {}))
                        for n in wf.get("nodes", [])),
        "edges": sorted((e.get("source"), e.get("target"), e.get("sourceHandle") or "out")
                        for e in wf.get("edges", [])),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(core.encode("utf-8")).hexdigest()[:16]


def valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))


# ═══════════════ 持久化 ═══════════════

def _wf_path(wid: str) -> Path:
    if not valid_id(wid):
        raise ValueError("非法工作流 id")
    return WORKFLOW_DIR / f"{wid}.json"


def list_workflows() -> list:
    WORKFLOW_DIR.mkdir(exist_ok=True)
    out = []
    for p in WORKFLOW_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": d.get("id", p.stem), "name": d.get("name", p.stem),
                    "description": d.get("description", ""),
                    "nodes": len(d.get("nodes", [])), "updated": d.get("updated", "")})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


def get_workflow(wid: str) -> dict | None:
    p = _wf_path(wid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_workflow(data: dict) -> dict:
    WORKFLOW_DIR.mkdir(exist_ok=True)
    wid = data.get("id") or ("wf_" + uuid.uuid4().hex[:10])
    data = {**data, "id": wid, "updated": _now()}
    data.setdefault("name", "未命名工作流")
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    _wf_path(wid).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def delete_workflow(wid: str) -> None:
    """删除工作流及其全部运行记录（运行记录只增不减，不跟着删会一直堆积）。"""
    import shutil
    p = _wf_path(wid)
    if p.exists():
        p.unlink()
    for rp in RUN_DIR.glob("*.json") if RUN_DIR.exists() else []:
        try:
            if json.loads(rp.read_text(encoding="utf-8")).get("workflow_id") == wid:
                rp.unlink()
                shutil.rmtree(RUN_DIR / rp.stem, ignore_errors=True)
        except (OSError, json.JSONDecodeError):
            continue


def new_workflow(name: str = "未命名工作流") -> dict:
    """默认模板：开始 → LLM → 结束，打开就能跑。"""
    return save_workflow({
        "name": name, "description": "",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 80, "y": 220},
             "data": {"title": "开始", "variables": [
                 {"name": "query", "label": "用户输入", "type": "paragraph", "required": True}]}},
            {"id": "llm_1", "type": "llm", "position": {"x": 420, "y": 200},
             "data": {"title": "LLM", "system": "你是一个乐于助人的助手。",
                      "prompt": "{{start.query}}", "temperature": 0.7}},
            {"id": "end", "type": "end", "position": {"x": 760, "y": 220},
             "data": {"title": "结束", "outputs": [
                 {"name": "answer", "value": "{{llm_1.text}}"}]}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "llm_1", "sourceHandle": "out"},
            {"id": "e2", "source": "llm_1", "target": "end", "sourceHandle": "out"},
        ],
    })


def get_run(run_id: str) -> dict | None:
    if not valid_id(run_id):
        return None
    p = RUN_DIR / f"{run_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_runs(wid: str, limit: int = 30) -> list:
    RUN_DIR.mkdir(exist_ok=True)
    runs = []
    for p in sorted(RUN_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("workflow_id") == wid:
            runs.append({k: r.get(k) for k in ("id", "status", "started", "finished",
                                               "duration_ms", "inputs", "error")})
        if len(runs) >= limit:
            break
    return runs


def _write_run(run: dict) -> None:
    RUN_DIR.mkdir(exist_ok=True)
    (RUN_DIR / f"{run['id']}.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")


# ═══════════════ 变量系统 ═══════════════

def _dig(value, path: str, default=None):
    """a.b[0].c 取值；取不到返回 None。字符串形式的 JSON 自动解析再往下取。"""
    for part in re.findall(r"[^\.\[\]]+", path):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return default
        if isinstance(value, dict):
            value = value.get(part, default)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            idx = int(part)
            value = value[idx] if -len(value) <= idx < len(value) else default
        else:
            return default
    return value


def _stringify(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


class Ctx:
    """一次运行的变量空间：outputs[node_id] = {field: value}；sys/env 两个保留命名空间。"""

    def __init__(self, inputs: dict, env: dict | None = None):
        self.outputs: dict[str, dict] = {"sys": {"query": inputs.get("query", ""),
                                                 "run_at": _now()},
                                         "env": dict(env or {})}
        self.convo: list = []          # LLM 节点 memory=true 时共享的多轮对话

    def get(self, ref: str):
        """ref 形如 node.field 或 {{node.field}}"""
        m = _VAR_RE.fullmatch(ref.strip()) if "{{" in ref else None
        node, path = (m.group(1), m.group(2)) if m else (ref.split(".", 1) + [""])[:2]
        return _dig(self.outputs.get(node, {}), path) if path else self.outputs.get(node)

    def render(self, template) -> str:
        """模板替换：整串恰好是一个引用时保留原类型（给代码节点传 list/dict 用）。"""
        if not isinstance(template, str):
            return template
        return _VAR_RE.sub(lambda m: _stringify(
            _dig(self.outputs.get(m.group(1), {}), m.group(2))), template)

    def resolve(self, value, strict: bool = False):
        if isinstance(value, str):
            if strict:
                for ref in _VAR_RE.finditer(value):
                    result = _dig(self.outputs.get(ref.group(1), _MISSING),
                                  ref.group(2), _MISSING)
                    if result is _MISSING:
                        raise NodeError(f"输出引用 {ref.group(0)} 不存在或上游节点未执行")
            m = _VAR_RE.fullmatch(value.strip())
            if m:
                return _dig(self.outputs.get(m.group(1), {}), m.group(2))
            return self.render(value)
        if isinstance(value, list):
            return [self.resolve(v, strict=strict) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v, strict=strict) for k, v in value.items()}
        return value

    def fork(self) -> "Ctx":
        c = Ctx({})
        c.outputs = copy.deepcopy(self.outputs)
        c.convo = self.convo
        return c


# ═══════════════ 条件求值（if_else / loop） ═══════════════

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def eval_condition(cond: dict, ctx: Ctx) -> bool:
    left = ctx.resolve(cond.get("var", ""))
    op = cond.get("op", "contains")
    right = ctx.render(cond.get("value", ""))
    ls = _stringify(left)
    if op == "empty":
        return left in (None, "", [], {})
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op == "contains":
        return right in (left if isinstance(left, list) else ls)
    if op == "not_contains":
        return right not in (left if isinstance(left, list) else ls)
    if op == "eq":
        return ls == right
    if op == "ne":
        return ls != right
    if op == "starts_with":
        return ls.startswith(right)
    if op == "ends_with":
        return ls.endswith(right)
    if op == "regex":
        try:
            return re.search(right, ls) is not None
        except re.error:
            return False
    if op in ("gt", "lt", "ge", "le"):
        a, b = _num(left), _num(right)
        if a is None or b is None:
            return False
        return {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b}[op]
    if op == "len_gt":
        return len(left or []) > (_num(right) or 0)
    return False


def eval_case(case: dict, ctx: Ctx) -> bool:
    conds = case.get("conditions") or []
    if not conds:
        return False
    results = [eval_condition(c, ctx) for c in conds]
    return all(results) if case.get("logic", "and") == "and" else any(results)


# ═══════════════ 节点执行器 ═══════════════
# 每个执行器: async (node, ctx, rt) -> (outputs: dict, handles: set | None)
# handles=None 表示激活全部默认出口 "out"；分支节点返回命中的 handle 集合。

class NodeError(Exception):
    pass


def _clip(v):
    s = _stringify(v)
    return v if len(s) <= MAX_NODE_OUTPUT_CHARS else s[:MAX_NODE_OUTPUT_CHARS] + "…(截断)"


async def _llm_call(rt, system: str, messages: list, data: dict, node_id: str,
                    json_mode: bool = False) -> tuple[str, dict]:
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    overrides = {"temperature": data.get("temperature"),
                 "max_tokens": data.get("max_tokens") or None,
                 "model": data.get("model") or None}
    if json_mode:
        overrides["response_format"] = {"type": "json_object"}

    def on_delta(kind, text):
        if kind == "reset":
            rt.emit_live("node_delta", node_id=node_id, reset=True)
        if kind == "content":
            rt.emit_live("node_delta", node_id=node_id, text=text)

    # 节点指定档案 → 用该档案的连接（base_url/key/模型名整套）；旧 d.model 仅改名
    model_cfg = (rt.cfg.get("model_profiles") or {}).get(data.get("profile") or "")
    model = OpenAICompatModel(model_cfg if model_cfg else rt.cfg["model"])
    try:
        resp = await model.achat(msgs, [], on_delta=on_delta, overrides=overrides)
    except RuntimeError as e:
        if json_mode and "response_format" in str(e):   # 服务端不支持 json 模式：去掉重试
            overrides.pop("response_format")
            resp = await model.achat(msgs, [], on_delta=on_delta, overrides=overrides)
        else:
            raise
    return resp.content, resp.usage


def _parse_json_loose(text: str):
    """从模型输出里捞 JSON：优先 ```json 代码块，其次第一个 {...}/[...]。"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    cand = m.group(1) if m else text
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        mm = re.search(pat, cand)
        if mm:
            try:
                return json.loads(mm.group(0))
            except json.JSONDecodeError:
                continue
    return None


async def n_start(node, ctx, rt):
    out = {}
    for v in node["data"].get("variables", []):
        name = v["name"]
        val = rt.inputs.get(name, v.get("default", ""))
        if v.get("required") and val in (None, ""):
            raise NodeError(f"缺少必填输入: {v.get('label') or name}")
        if v.get("type") == "number":
            val = _num(val) if val != "" else None
        out[name] = val
    return out, None


async def n_end(node, ctx, rt):
    out = {o["name"]: ctx.resolve(o.get("value", ""), strict=True)
           for o in node["data"].get("outputs", [])}
    rt.final.update(out)
    return out, None


async def n_answer(node, ctx, rt):
    text = ctx.render(node["data"].get("text", ""))
    rt.answers.append(text)
    rt.emit("workflow_answer", node_id=node["id"], text=text)
    return {"text": text}, None


async def n_llm(node, ctx, rt):
    d = node["data"]
    system = ctx.render(d.get("system", ""))
    prompt = ctx.render(d.get("prompt", ""))
    if not prompt.strip():
        raise NodeError("提示词为空")
    messages = list(ctx.convo) if d.get("memory") else []
    messages.append({"role": "user", "content": prompt})
    text, usage = await _llm_call(rt, system, messages, d, node["id"],
                                  json_mode=d.get("output") == "json")
    if d.get("memory"):
        ctx.convo += [{"role": "user", "content": prompt},
                      {"role": "assistant", "content": text}]
    out = {"text": text, "usage": usage}
    if d.get("output") == "json":
        out["json"] = _parse_json_loose(text)
    return out, None


async def n_classifier(node, ctx, rt):
    d = node["data"]
    classes = d.get("classes") or []
    if not classes:
        raise NodeError("未配置分类")
    query = ctx.render(d.get("input", "{{sys.query}}"))
    listing = "\n".join(f"{i + 1}. {c['name']}" for i, c in enumerate(classes))
    system = ("你是一个文本分类器。根据用户输入，从给定类别中选出最匹配的一个。"
              "只输出 JSON：{\"index\": 类别序号, \"reason\": \"一句话理由\"}。"
              + (f"\n补充说明：{d['instruction']}" if d.get("instruction") else ""))
    text, _ = await _llm_call(rt, system, [{"role": "user", "content":
                                            f"类别：\n{listing}\n\n输入：\n{query}"}],
                              {**d, "temperature": d.get("temperature", 0)}, node["id"],
                              json_mode=True)
    parsed = _parse_json_loose(text) or {}
    idx = int(_num(parsed.get("index")) or 0) - 1
    if not 0 <= idx < len(classes):   # 解析失败：按名字模糊匹配，兜底第一个
        idx = next((i for i, c in enumerate(classes) if c["name"] in text), 0)
    hit = classes[idx]
    return {"class_name": hit["name"], "class_id": hit["id"],
            "reason": parsed.get("reason", "")}, {hit["id"]}


async def n_extractor(node, ctx, rt):
    d = node["data"]
    params = d.get("params") or []
    if not params:
        raise NodeError("未配置要提取的参数")
    text_in = ctx.render(d.get("input", "{{sys.query}}"))
    spec = "\n".join(f"- {p['name']} ({p.get('type', 'string')}"
                     f"{', 必填' if p.get('required') else ''}): {p.get('description', '')}"
                     for p in params)
    system = ("从文本中提取结构化参数，只输出一个 JSON 对象，键为参数名；"
              "提取不到的参数给 null。\n参数：\n" + spec
              + (f"\n补充说明：{d['instruction']}" if d.get("instruction") else ""))
    text, _ = await _llm_call(rt, system, [{"role": "user", "content": text_in}],
                              {**d, "temperature": d.get("temperature", 0)}, node["id"],
                              json_mode=True)
    parsed = _parse_json_loose(text)
    if not isinstance(parsed, dict):
        raise NodeError(f"模型未返回可解析的 JSON：{text[:200]}")
    out = {p["name"]: parsed.get(p["name"]) for p in params}
    missing = [p["name"] for p in params if p.get("required") and out[p["name"]] in (None, "")]
    out["__is_success"] = not missing
    out["__reason"] = f"缺少: {missing}" if missing else ""
    return out, None


async def n_if_else(node, ctx, rt):
    """cases 依次求值，命中第一个就走它的 handle（case.id），都不中走 else。"""
    for case in node["data"].get("cases", []):
        if eval_case(case, ctx):
            return {"result": case["id"]}, {case["id"]}
    return {"result": "else"}, {"else"}


async def n_code(node, ctx, rt):
    """Python 代码节点：子进程隔离执行，main(**inputs) 返回 dict 作为输出。"""
    d = node["data"]
    inputs = {v["name"]: ctx.resolve(v.get("value", "")) for v in d.get("inputs", [])}
    code = d.get("code") or ""
    if "def main" not in code:
        raise NodeError("代码中需要定义 def main(...) -> dict")
    runner = ("import json,sys\n" + code +
              "\n_in=json.loads(sys.stdin.read())\n_r=main(**_in)\n"
              "if not isinstance(_r,dict): _r={'result':_r}\n"
              "sys.stdout.write('\\n__WF_OUT__'+json.dumps(_r,ensure_ascii=False,default=str))\n")
    import sys as _sys
    proc = await asyncio.create_subprocess_exec(
        _sys.executable, "-I", "-c", runner,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=str(rt.workdir))
    try:
        so, se = await asyncio.wait_for(
            proc.communicate(json.dumps(inputs, ensure_ascii=False, default=str).encode()),
            timeout=float(d.get("timeout", 30)))
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise NodeError("代码执行超时") from exc
    stdout = so.decode("utf-8", "replace")
    if proc.returncode != 0 or "__WF_OUT__" not in stdout:
        raise NodeError((se.decode("utf-8", "replace") or stdout)[-1500:])
    logs, _, payload = stdout.rpartition("__WF_OUT__")
    out = json.loads(payload)
    if logs.strip():
        out.setdefault("__logs", logs.strip()[-2000:])
    return out, None


async def n_template(node, ctx, rt):
    return {"output": ctx.render(node["data"].get("template", ""))}, None


async def n_http(node, ctx, rt):
    d = node["data"]
    url = ctx.render(d.get("url", "")).strip()
    if not re.match(r"^https?://", url):
        raise NodeError(f"URL 不合法: {url!r}")
    headers = {h["key"]: ctx.render(h.get("value", "")) for h in d.get("headers", []) if h.get("key")}
    params = {h["key"]: ctx.render(h.get("value", "")) for h in d.get("params", []) if h.get("key")}
    body = ctx.render(d.get("body", "")) if d.get("method", "GET") not in ("GET", "HEAD") else None
    kw = {}
    if body:
        if d.get("body_type", "json") == "json":
            try:
                kw["json"] = json.loads(body)
            except json.JSONDecodeError:
                raise NodeError("请求体不是合法 JSON")
        else:
            kw["content"] = body.encode()
    async with httpx.AsyncClient(timeout=float(d.get("timeout", 30)),
                                 follow_redirects=True) as c:
        r = await c.request(d.get("method", "GET"), url, headers=headers, params=params, **kw)
    text = r.text
    out = {"status_code": r.status_code, "body": _clip(text),
           "headers": dict(r.headers)}
    with_json = None
    if "json" in r.headers.get("content-type", ""):
        try:
            with_json = r.json()
        except ValueError:
            pass
    out["json"] = with_json
    if r.status_code >= 400 and not d.get("allow_error"):
        raise NodeError(f"HTTP {r.status_code}: {text[:300]}")
    return out, None


async def n_tool(node, ctx, rt):
    """调用 toolbox 工具（与智能体同一条 校验→隔离执行→归一化→审计 管道）。"""
    d = node["data"]
    name = d.get("tool")
    if not name:
        raise NodeError("未选择工具")
    params = ctx.resolve(d.get("params") or {})
    box = await rt.tools()
    if name == "subagent":
        import subagent
        box = await subagent.bind(box, rt.cfg, rt.session_id,
                                  _NodeBus(rt, node["id"]), rt.cancel)
    # 外部工具/MCP 调用裸等会永远挂住（MCP SDK 默认 read_timeout 无限），
    # 这里兜一层默认 600s 的硬超时；超时走节点既有的 retry/on_error 管道。
    timeout = float(d.get("tool_timeout") or 600)
    try:
        raw = await asyncio.wait_for(box.call_surface(name, params, "native"),
                                     timeout=timeout)
    except asyncio.TimeoutError:
        raise NodeError(f"工具 {name} {int(timeout)}s 未返回（外部工具/MCP 无响应），已掐断")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"status": "ok", "output": raw}
    if str(parsed.get("status", "ok")) not in ("ok",):
        raise NodeError(f"工具返回 {parsed.get('status')}: "
                        f"{parsed.get('detail') or parsed.get('hint') or raw[:300]}")
    text = parsed.get("output") or "\n".join(
        x for x in (parsed.get("output_head"), parsed.get("output_tail")) if x)
    payload = None
    if not parsed.get("stats", {}).get("truncated") and isinstance(text, str):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            pass
    return {"text": _clip(text), "summary": parsed.get("summary", ""),
            "call_id": parsed.get("call_id", ""), "raw": parsed,
            "json": payload}, None


async def n_agent(node, ctx, rt):
    """智能体节点：在工作流里跑完整 agent 循环（可调工具、自带防兜圈）。"""
    from agent import run_agent
    d = node["data"]
    task = ctx.render(d.get("task", ""))
    if not task.strip():
        raise NodeError("任务描述为空")
    cfg = copy.deepcopy(rt.cfg)
    cfg["agent"]["max_iterations"] = int(d.get("max_iterations") or 10)
    if d.get("system"):
        cfg["agent"]["system_prompt"] = ctx.render(d["system"])
    prof = (rt.cfg.get("model_profiles") or {}).get(d.get("profile") or "")
    if prof:                                   # 节点指定档案：整套连接替换（不继承全局 failover）
        cfg["model"] = dict(prof)
    if d.get("max_tokens"):
        cfg["model"]["max_tokens"] = int(d["max_tokens"])
    if d.get("temperature") is not None:
        cfg["model"]["temperature"] = float(d["temperature"])
    bus = _NodeBus(rt, node["id"])
    toolset = d.get("toolset_id")
    box = await rt.tools() if toolset in (None, "all") and rt.cfg.get("tools", {}).get("command") else None
    answer = await run_agent(rt.session_id, task, cfg, bus, rt.cancel,
                             tools_box=box, persist=False, emit_user=False,
                             toolset=toolset)
    if rt.cancel.is_set():
        raise asyncio.CancelledError()
    if bus.error:
        raise NodeError(bus.error)
    answer = answer or bus.done_content
    if not answer or not answer.strip():
        raise NodeError("智能体没有返回可用文本，无法传给下游节点")
    return {"text": answer, "tool_calls": bus.tool_calls, "iterations": bus.iterations}, None


async def n_aggregator(node, ctx, rt):
    """变量聚合：多条分支汇合后取第一个非空的来源（Dify 同款）。"""
    for ref in node["data"].get("variables", []):
        v = ctx.resolve(ref)
        if v not in (None, "", [], {}):
            return {"output": v}, None
    return {"output": None}, None


async def n_assigner(node, ctx, rt):
    """变量赋值：写会话级变量 env.xxx，后续节点/下一轮循环可读。"""
    for item in node["data"].get("items", []):
        name = item.get("name")
        if not name:
            continue
        v = ctx.resolve(item.get("value", ""))
        mode = item.get("mode", "set")
        cur = ctx.outputs["env"].get(name)
        if mode == "append":
            cur = (cur if isinstance(cur, list) else ([] if cur in (None, "") else [cur]))
            v = cur + [v]
        elif mode == "add":
            v = (_num(cur) or 0) + (_num(v) or 0)
        ctx.outputs["env"][name] = v
    return dict(ctx.outputs["env"]), None


async def n_list_op(node, ctx, rt):
    """列表操作：过滤 / 排序 / 切片 / 去重 / 拆分 / 合并。"""
    d = node["data"]
    src = ctx.resolve(d.get("input", ""))
    op = d.get("op", "slice")
    if op == "split":
        return {"output": [s for s in _stringify(src).split(
            (d.get("sep") or "\n").encode().decode("unicode_escape")) if s.strip()]}, None
    if isinstance(src, str):
        src = _parse_json_loose(src) if src.strip()[:1] in "[{" else [src]
    items = list(src or [])
    if op == "filter":
        kw = ctx.render(d.get("value", ""))
        items = [x for x in items if kw in _stringify(x)]
    elif op == "sort":
        key = d.get("key") or ""
        items.sort(key=lambda x: _stringify(_dig(x, key) if key else x),
                   reverse=bool(d.get("desc")))
    elif op == "unique":
        seen, uniq = set(), []
        for x in items:
            k = _stringify(x)
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        items = uniq
    elif op == "join":
        return {"output": (d.get("sep") or "\n").join(_stringify(x) for x in items)}, None
    if op in ("slice", "filter", "sort", "unique"):
        start, end = int(_num(d.get("start")) or 0), _num(d.get("end"))
        items = items[start:int(end)] if end is not None else items[start:]
    return {"output": items, "count": len(items),
            "first": items[0] if items else None,
            "last": items[-1] if items else None}, None


async def n_note(node, ctx, rt):
    return {}, None


class _NodeBus:
    """agent 节点的事件适配：把 agent 循环事件转成工作流节点日志。"""

    def __init__(self, rt, node_id: str):
        self.rt, self.node_id = rt, node_id
        self.tool_calls, self.iterations, self.error = [], 0, ""
        self.done_content = ""

    def emit(self, t, **f):
        if t == "subagent_state":
            if hasattr(self.rt, 'emit'):
                self.rt.emit('subagent_state', **f)
            self.rt.log(self.node_id, f"[subagent] {f.get('name', '')} "
                                      f"{f.get('status', '')} {f.get('detail', '')}"[:240])
        if t == "model_call":
            self.iterations = f.get("iteration", 0)
            self.rt.emit_live("node_delta", node_id=self.node_id, reset=True)
            self.rt.log(self.node_id, f"[agent] model_call iteration={self.iterations}")
        elif t == "done":
            self.done_content = f.get("content", "")
        elif t == "tool_call":
            self.tool_calls.append({"tool": f.get("tool"), "params": f.get("params")})
        elif t == "error":
            self.error = f.get("detail", "")
        if t in ("tool_call", "tool_result", "rollback", "error"):
            # tool_call 带 params、tool_result 带 result(agent._preview 预览)、error 带 detail
            self.rt.log(self.node_id, f"[agent] {t} {f.get('tool', '')} "
                                      f"{_stringify(f.get('params') or f.get('result') or f.get('detail') or '')[:160]}")
        return {}

    def emit_live(self, t, **f):
        if t == "assistant_delta":
            if f.get("reset"):
                self.rt.emit_live("node_delta", node_id=self.node_id, reset=True)
            elif f.get("content") or f.get("reasoning"):
                self.rt.emit_live("node_delta", node_id=self.node_id,
                                  text=f.get("content", ""), reasoning=f.get("reasoning", ""))


async def _gather_nodes(coros):
    """Drain cancelled siblings before resources are closed or a retry starts."""
    tasks = [asyncio.create_task(coro) for coro in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def n_iteration(node, ctx, rt):
    """迭代：对列表每一项跑一遍子图。子图内用 {{<iteration_id>.item}} / .index 取当前项。
    parallel=true 时并发（上限 parallel_limit），输出 = 每项子图 output_var 的结果列表。"""
    d = node["data"]
    items = ctx.resolve(d.get("input", ""))
    if isinstance(items, str):
        items = _parse_json_loose(items) if items.strip()[:1] == "[" else \
            [s for s in items.split("\n") if s.strip()]
    if not isinstance(items, list):
        raise NodeError("迭代输入不是列表")
    items = items[: int(d.get("max_items") or 100)]
    limit = max(1, int(d.get("parallel_limit") or 4)) if d.get("parallel") else 1
    sem = asyncio.Semaphore(limit)

    async def one(idx, item):
        async with sem:
            sub = ctx.fork()
            sub.outputs[node["id"]] = {"item": item, "index": idx}
            await rt.run_graph(node["id"], sub, label=f"#{idx + 1}")
            return sub.resolve(d.get("output_var", "")) if d.get("output_var") else \
                sub.outputs.get(node["id"], {}).get("item")

    results = await _gather_nodes(one(i, x) for i, x in enumerate(items))
    return {"output": list(results), "count": len(results)}, None


async def n_loop(node, ctx, rt):
    """循环：重复跑子图直到 break 条件成立或达到 max_loops。env 变量跨轮保留。"""
    d = node["data"]
    max_loops = min(int(d.get("max_loops") or 5), 100)
    rounds = 0
    sub = ctx.fork()
    for rounds in range(1, max_loops + 1):
        sub.outputs[node["id"]] = {"index": rounds - 1}
        await rt.run_graph(node["id"], sub, label=f"第{rounds}轮")
        if d.get("break_case") and eval_case(d["break_case"], sub):
            break
    ctx.outputs["env"].update(sub.outputs["env"])
    for k, v in sub.outputs.items():          # 子图节点输出冒泡给外层引用
        if k not in ("sys", "env", node["id"]):
            ctx.outputs[k] = v
    return {"rounds": rounds,
            "output": sub.resolve(d.get("output_var", "")) if d.get("output_var") else None}, None


EXECUTORS = {
    "start": n_start, "end": n_end, "answer": n_answer, "llm": n_llm,
    "classifier": n_classifier, "extractor": n_extractor, "if_else": n_if_else,
    "code": n_code, "template": n_template, "http": n_http, "tool": n_tool,
    "agent": n_agent, "aggregator": n_aggregator, "assigner": n_assigner,
    "list_op": n_list_op, "iteration": n_iteration, "loop": n_loop,
    "iteration_start": n_note, "loop_start": n_note, "note": n_note,
}
PASSIVE = {"note"}             # 画布注释，不参与执行


# ═══════════════ 调度器 ═══════════════

def validate(wf: dict) -> list[str]:
    """保存/运行前的静态检查。返回问题列表（空 = 可运行）。"""
    problems = []
    nodes = {n["id"]: n for n in wf.get("nodes", [])}
    top = [n for n in nodes.values() if not n.get("parentNode") and n["type"] not in PASSIVE]
    starts = [n for n in top if n["type"] == "start"]
    if len(starts) != 1:
        problems.append("需要且只能有一个『开始』节点")
    if not any(n["type"] in ("end", "answer") for n in top):
        problems.append("至少需要一个『结束』或『直接回复』节点")
    for n in nodes.values():
        if n["type"] not in EXECUTORS:
            problems.append(f"未知节点类型: {n['type']}")
    for e in wf.get("edges", []):
        if e["source"] not in nodes or e["target"] not in nodes:
            problems.append(f"连线 {e.get('id')} 指向不存在的节点")
    # 环检测（容器内部子图同理）：Kahn 拓扑排序剩余即成环
    indeg = {nid: 0 for nid in nodes}
    for e in wf.get("edges", []):
        if e["target"] in indeg:
            indeg[e["target"]] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    adj: dict[str, list] = {}
    for e in wf.get("edges", []):
        adj.setdefault(e["source"], []).append(e["target"])
    while queue:
        cur = queue.pop()
        seen += 1
        for t in adj.get(cur, []):
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    if seen != len(nodes):
        problems.append("连线成环：需要重复执行请用『循环』节点")
    return problems


class Runtime:
    """一次运行的上下文：图索引、运行记录、事件推送、工具句柄（懒启动、全程复用）。"""

    def __init__(self, wf: dict, inputs: dict, cfg: dict, bus, cancel: asyncio.Event,
                 run_id: str, resume_from: dict | None = None,
                 node_overrides: dict | None = None):
        self.wf, self.inputs, self.cfg, self.bus, self.cancel = wf, inputs, cfg, bus, cancel
        self.nodes = {n["id"]: n for n in wf["nodes"]}
        self.out_edges: dict[str, list] = {}
        self.in_edges: dict[str, list] = {}
        for e in wf["edges"]:
            self.out_edges.setdefault(e["source"], []).append(e)
            self.in_edges.setdefault(e["target"], []).append(e)
        self.final: dict = {}
        self.answers: list = []
        self.session_id = f"wf-{run_id}"
        self.workdir = RUN_DIR / run_id
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._tools = None
        self._tools_lock = asyncio.Lock()
        self._tools_task = None
        self._tools_ready = None
        self._tools_stop = asyncio.Event()
        # 断点续跑：图快照进记录（重试按当时的图跑，改过画布不影响），
        # 上一次运行里 succeeded 节点的输出按 key(nid@label) 供 exec_node 命中复用。
        self._cache: dict[str, dict] = {}
        self._active_nodes: dict[str, asyncio.Task] = {}
        self._skip_requests: dict[str, dict] = {}
        self._manual: dict[str, dict] = {}
        self.run = {"id": run_id, "workflow_id": wf["id"], "workflow_name": wf.get("name"),
                    "status": "running", "started": _now(), "inputs": inputs,
                    "graph_hash": _graph_hash(wf),
                    "graph": {"nodes": wf.get("nodes", []), "edges": wf.get("edges", [])},
                    "nodes": {}, "logs": [], "outputs": {}, "answers": []}
        if resume_from:
            self.run["resumed_from"] = resume_from.get("id")
            if resume_from.get("graph_hash") == self.run["graph_hash"]:
                for key, rec in (resume_from.get("nodes") or {}).items():
                    if rec.get("status") in ("succeeded", "cached", "manual") and "outputs" in rec:
                        self._cache[key] = rec["outputs"]
                    if rec.get("status") == "manual":
                        self._manual[key] = {"outputs": rec.get("outputs", {}), "handles": rec.get("handles")}
        if node_overrides:
            self._manual.update(copy.deepcopy(node_overrides))
        _write_run(self.run)

    def request_skip(self, key: str, outputs, handle=None):
        task = self._active_nodes.get(key)
        if self.cancel.is_set() or not task or task.done() or key in self._skip_requests:
            raise NodeError("节点已结束、正在跳过或工作流正在停止")
        replacement = manual_override(self.wf, key, outputs, handle)
        self._skip_requests[key] = replacement
        nid, _, label = key.partition("@")
        self.emit("workflow_node", node_id=nid, label=label, status="skipping")
        task.cancel()

    def _apply_manual(self, key, node, ctx, label, replacement):
        outputs = copy.deepcopy(replacement["outputs"])
        handles = replacement.get("handles")
        # 跳过整个容器时，它的子任务已随容器取消；同步结束子卡片动画。
        descendants = {node["id"]}
        for _ in range(len(self.nodes)):
            added = {n["id"] for n in self.nodes.values() if n.get("parentNode") in descendants}
            if added <= descendants:
                break
            descendants.update(added)
        for child_key, rec in self.run["nodes"].items():
            child_id, _, child_label = child_key.partition("@")
            if child_id != node["id"] and child_id in descendants and rec.get("status") in ("running", "skipping"):
                rec.update(status="stopped", error="所属容器已跳过")
                self.emit("workflow_node", node_id=child_id, label=child_label, status="stopped", error=rec["error"])
        ctx.outputs[node["id"]] = outputs
        self.run["nodes"][key] = {"status": "manual", "title": node.get("data", {}).get("title", node["id"]),
            "type": node["type"], "label": label, "duration_ms": 0,
            "outputs": outputs, "handles": handles, "manual": True}
        self.emit("workflow_node", node_id=node["id"], label=label, status="manual", outputs=outputs,
                  duration_ms=0)
        self.log(node["id"], "已跳过执行，使用手动填写的结果继续", "warn")
        return set(handles) if handles is not None else None

    def finish_active_nodes(self, status="stopped"):
        for key, rec in self.run["nodes"].items():
            if rec.get("status") in ("running", "skipping", "stopping"):
                rec.update(status=status, error="已停止" if status == "stopped" else "执行已终止")
                nid, _, label = key.partition("@")
                self.emit("workflow_node", node_id=nid, label=label, status=status, error=rec["error"])

    # ── 事件 ──
    def emit(self, t: str, **f):
        self.bus.emit(t, run_id=self.run["id"], **f)

    def emit_live(self, t: str, **f):
        self.bus.emit_live(t, run_id=self.run["id"], **f)

    def log(self, node_id: str, msg: str, level: str = "info"):
        item = {"ts": _now(), "node_id": node_id, "level": level, "msg": msg[:600]}
        self.run["logs"].append(item)
        self.emit("workflow_log", **item)

    # ── 工具句柄：第一次用到才拉起 MCP 子进程 ──
    async def tools(self):
        async with self._tools_lock:
            if self._tools_task is None:
                tcfg = self.cfg.get("tools", {}) or {}
                if not tcfg.get("command"):
                    raise NodeError("未配置工具服务器（config.yaml tools 段）")
                self._tools_ready = asyncio.get_running_loop().create_future()
                self._tools_task = asyncio.create_task(self._own_tools(tcfg))
            return await asyncio.shield(self._tools_ready)

    async def _own_tools(self, tcfg):
        import mcp_client
        try:
            async with mcp_client.open_tools(tcfg, self.session_id, toolset="all") as box:
                self._tools = box
                self._tools_ready.set_result(box)
                await self._tools_stop.wait()
        except BaseException as exc:
            if not self._tools_ready.done():
                self._tools_ready.set_exception(exc)
                return
            raise

    async def close(self):
        if self._tools_task is not None:
            self._tools_stop.set()
            try:
                await asyncio.shield(self._tools_task)
            except Exception:
                self.run.update(status="failed", error="工具服务器连接清理失败")
                self.log("", "工具服务器连接清理失败", "error")
                raise
            finally:
                if self._tools_ready.done() and not self._tools_ready.cancelled():
                    self._tools_ready.exception()

    async def cleanup_tools(self, ctx):
        for index, data in enumerate(self.wf.get("cleanup_tools") or []):
            try:
                ctx.resolve(data.get("params") or {}, strict=True)
            except NodeError:
                continue
            node = {"id": f"cleanup_{index}", "data": data}
            for attempt in range(3):
                try:
                    await asyncio.wait_for(n_tool(node, ctx, self), timeout=15)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5)

    # ── 单节点 ──
    async def exec_node(self, nid: str, ctx: Ctx, label: str = "") -> set | None:
        node = self.nodes[nid]
        d = node.get("data", {})
        key = f"{nid}{label and '@' + label}"
        if key in self._manual:
            return self._apply_manual(key, node, ctx, label, self._manual.pop(key))
        # 断点缓存命中：直接复用上次成功的输出，不再执行。
        # pop 而非 get——循环节点多轮同 key，只该复用对应那一轮。
        if (node["type"] not in NO_CACHE_TYPES
                and not (node["type"] == "llm" and d.get("memory"))
                and key in self._cache):
            outputs = copy.deepcopy(self._cache.pop(key))
            handles = None                     # 分支节点从缓存输出重建命中的出口
            if node["type"] == "if_else" and outputs.get("result"):
                handles = {outputs["result"]}
            elif node["type"] == "classifier" and outputs.get("class_id"):
                handles = {outputs["class_id"]}
            ctx.outputs[nid] = outputs
            preview = {k: _clip(v) for k, v in outputs.items()}
            self.run["nodes"][key] = {"status": "cached", "title": d.get("title", nid),
                                      "type": node["type"], "label": label,
                                      "duration_ms": 0, "outputs": preview}
            self.emit("workflow_node", node_id=nid, label=label, status="cached",
                      duration_ms=0, outputs=preview)
            return handles
        rec = {"status": "running", "started": _now(), "title": d.get("title", nid),
               "type": node["type"], "label": label}
        self.run["nodes"][key] = rec
        self.emit("workflow_node", node_id=nid, label=label, status="running",
                  title=rec["title"])
        t0 = time.monotonic()
        retries = int(d.get("retry") or 0)
        err = None
        for attempt in range(retries + 1):
            if self.cancel.is_set():
                raise asyncio.CancelledError()
            try:
                timeout = float(d.get("node_timeout") or 0) or None
                task = asyncio.create_task(EXECUTORS[node["type"]](node, ctx, self))
                self._active_nodes[key] = task
                outputs, handles = await asyncio.wait_for(
                    task, timeout=timeout)
                err = None
            except asyncio.CancelledError:
                if self.cancel.is_set() or key not in self._skip_requests:
                    raise
            except asyncio.TimeoutError:
                err = "节点执行超时"
            except Exception as e:
                err = str(e) or type(e).__name__
            finally:
                self._active_nodes.pop(key, None)
            if not self.cancel.is_set() and key in self._skip_requests:
                return self._apply_manual(key, node, ctx, label, self._skip_requests.pop(key))
            if err is None:
                break
            if attempt < retries:
                self.log(nid, f"失败重试 {attempt + 1}/{retries}: {err}", "warn")
                await asyncio.sleep(min(2 ** attempt, 8))
        ms = int((time.monotonic() - t0) * 1000)
        if err is not None:
            policy = d.get("on_error", "fail")
            if policy == "continue":
                outputs, handles = {"error": err, **(d.get("default_outputs") or {})}, None
                self.log(nid, f"失败但继续: {err}", "warn")
            elif policy == "branch":
                outputs, handles = {"error": err}, {"fail"}
                self.log(nid, f"失败走异常分支: {err}", "warn")
            else:
                rec.update(status="failed", error=err, duration_ms=ms)
                self.emit("workflow_node", node_id=nid, label=label, status="failed",
                          error=err, duration_ms=ms)
                raise NodeError(f"节点『{rec['title']}』失败: {err}")
        ctx.outputs[nid] = outputs
        preview = {k: _clip(v) for k, v in outputs.items()}
        rec.update(status="succeeded" if err is None else "exception",
                   duration_ms=ms, outputs=preview, error=err)
        self.emit("workflow_node", node_id=nid, label=label, status=rec["status"],
                  duration_ms=ms, outputs=preview, error=err)
        return handles

    # ── 图遍历 ──
    async def run_graph(self, parent: str | None, ctx: Ctx, label: str = "") -> None:
        """跑一个（子）图：parent=None 为顶层，否则为某个 iteration/loop 容器内部。"""
        members = {nid for nid, n in self.nodes.items()
                   if (n.get("parentNode") or None) == parent and n["type"] not in PASSIVE}
        if parent is None:
            entry = [nid for nid in members if self.nodes[nid]["type"] == "start"]
        else:
            entry = [nid for nid in members
                     if self.nodes[nid]["type"] in ("iteration_start", "loop_start")]
            if not entry:   # 容器里没放起点：取无入边的成员
                entry = [nid for nid in members
                         if not any(e["source"] in members for e in self.in_edges.get(nid, []))]
        # 边状态：None=未结算 / True=激活 / False=跳过
        edge_state: dict[str, bool | None] = {
            e["id"]: None for nid in members for e in self.out_edges.get(nid, [])
            if e["target"] in members}
        done: set[str] = set()
        ready = list(entry)
        while ready:
            if self.cancel.is_set():
                raise asyncio.CancelledError()
            batch, ready = ready, []
            results = await _gather_nodes(self._step(n, ctx, label, members, edge_state)
                                          for n in batch)
            for nid, skipped in zip(batch, results):
                done.add(nid)
                for e in self.out_edges.get(nid, []):
                    t = e["target"]
                    if t not in members or t in done or t in ready:
                        continue
                    ins = [x for x in self.in_edges.get(t, []) if x["id"] in edge_state]
                    if all(edge_state[x["id"]] is not None for x in ins):
                        ready.append(t)

    async def _step(self, nid, ctx, label, members, edge_state) -> bool:
        ins = [e for e in self.in_edges.get(nid, []) if e["id"] in edge_state]
        # 所有入边都被跳过 → 本节点跳过并向下传播（首节点没有入边，不算）
        if ins and not any(edge_state[e["id"]] for e in ins):
            self.emit("workflow_node", node_id=nid, label=label, status="skipped")
            self.run["nodes"].setdefault(f"{nid}{label and '@' + label}",
                                         {"status": "skipped", "type": self.nodes[nid]["type"]})
            for e in self.out_edges.get(nid, []):
                if e["id"] in edge_state:
                    edge_state[e["id"]] = False
            return True
        handles = await self.exec_node(nid, ctx, label)
        for e in self.out_edges.get(nid, []):
            if e["id"] not in edge_state:
                continue
            h = e.get("sourceHandle") or "out"
            edge_state[e["id"]] = (h in handles) if handles is not None else h != "fail"
        return False


async def execute(wf: dict, inputs: dict, cfg: dict, bus, cancel: asyncio.Event,
                  run_id: str | None = None, resume_from: dict | None = None,
                  node_overrides: dict | None = None, on_runtime=None) -> dict:
    """跑完整个工作流，返回运行记录。事件经 bus 推送（workflow_* 系列）。
    resume_from=旧运行记录 → 图指纹一致时 succeeded 节点直接复用输出（断点续跑）。"""
    run_id = run_id or ("run_" + datetime.now().strftime("%m%d%H%M%S") + uuid.uuid4().hex[:4])
    problems = validate(wf)
    rt = Runtime(wf, inputs, cfg, bus, cancel, run_id, resume_from=resume_from,
                 node_overrides=node_overrides)
    if on_runtime:
        on_runtime(rt)
    rt.emit("workflow_started", workflow_id=wf["id"], inputs=inputs)
    t0 = time.monotonic()
    ctx = Ctx(inputs)
    try:
        if problems:
            raise NodeError("；".join(problems))
        await rt.run_graph(None, ctx)
        rt.run.update(status="succeeded", outputs=rt.final, answers=rt.answers)
    except asyncio.CancelledError:
        rt.run.update(status="stopped", error="已停止")
    except Exception as e:
        rt.run.update(status="failed", error=str(e))
    finally:
        rt.finish_active_nodes()
        try:
            await rt.cleanup_tools(ctx)
        except Exception as exc:
            rt.run.update(status="failed", error=f"工作流工具收尾失败: {exc}")
        try:
            await rt.close()
        except Exception as exc:
            rt.run.update(status="failed", error=f"工具服务器连接清理失败: {exc}")
        rt.run.update(finished=_now(), duration_ms=int((time.monotonic() - t0) * 1000))
        _write_run(rt.run)
        rt.emit("workflow_finished", status=rt.run["status"], outputs=rt.run.get("outputs"),
                answers=rt.answers, error=rt.run.get("error"),
                duration_ms=rt.run["duration_ms"])
    return rt.run


async def run_single_node(wf: dict, node_id: str, inputs: dict, cfg: dict, bus,
                          cancel: asyncio.Event) -> dict:
    """单节点调试：inputs 以 {"node.field": value} 形式喂上游变量。"""
    run_id = "dbg_" + uuid.uuid4().hex[:8]
    rt = Runtime(wf, {}, cfg, bus, cancel, run_id)
    ctx = Ctx({})
    for ref, value in inputs.items():
        node, _, field = ref.partition(".")
        ctx.outputs.setdefault(node, {})[field] = value
    try:
        await rt.exec_node(node_id, ctx)
        rt.run.update(status="succeeded", outputs=ctx.outputs.get(node_id, {}))
    except Exception as e:
        rt.run.update(status="failed", error=str(e))
    except asyncio.CancelledError:
        rt.run.update(status="stopped", error="已停止")
    finally:
        rt.finish_active_nodes()
        try:
            await rt.close()
        except Exception as exc:
            rt.run.update(status="failed", error=f"工具服务器连接清理失败: {exc}")
        rt.run["finished"] = _now()
        _write_run(rt.run)
    return rt.run


class WorkflowBus:
    """工作流运行的事件总线：内存广播 + 运行级订阅（不需要会话目录）。"""

    def __init__(self):
        self._subs: list[asyncio.Queue] = []
        self._seq = 0
        self.history: list = []

    def emit(self, t: str, **f):
        self._seq += 1
        evt = {"seq": self._seq, "ts": _now(), "type": t, **f}
        self.history.append(evt)
        for q in list(self._subs):
            q.put_nowait(evt)
        return evt

    def emit_live(self, t: str, **f):
        evt = {"seq": 0, "live": True, "ts": _now(), "type": t, **f}
        for q in list(self._subs):
            q.put_nowait(evt)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        if q in self._subs:
            self._subs.remove(q)
