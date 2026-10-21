"""CTF 工具箱(All_WP)采集卡执行体:资产入树 + 管理 + MD 笔记入库。

op 分发:health 探活 / trees 树清单 / tree 查树 / find 搜索 / asset 资产入库
(collect 同键合并)/ update 改名移位合并 / del 级联删除 / note 写笔记。
纯 stdlib(urllib),零第三方依赖；目标由 params.server / WPBOX_URL / 默认 :8787 选择。
支持集合 CRUD、批量保存、导出/导入；管理接口需加载新版服务端。
源码副本在 external/All_WP，当前实际服务也可能由 E:/All_WP 启动。

退出码:0=成功;1=网络不通/服务端拒绝/树或节点不存在;2=参数校验失败。
"""
import http.client
import ipaddress
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BASE = os.environ.get("WPBOX_URL", "http://127.0.0.1:8787")
NODE_TYPES = ["target", "domain", "ip", "url", "port", "source", "tech",
              "vuln", "cred", "note"]
SEVERITIES = ["info", "low", "medium", "high", "critical"]
STATUSES = ["new", "confirmed", "fixed", "ignored"]
NOTE_TYPES = ["exp", "knowledge", "tool", "trick", "other"]


def fail(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def api(method: str, path: str, base: str, payload=None, missing_ok=False):
    """只向所选 HTTP 服务发送请求；本机地址绕过代理，不自动切换写入位置。"""
    url = base.rstrip("/") + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    if data is not None and len(data) > 32 * 1024 * 1024:
        fail("请求体超过服务端 32MB 上限", 2)
    req = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {})
    host = urlsplit(base).hostname or ""
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    # 不影响远程服务的系统代理配置，避免本机资产被转发到 HTTP 代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if loopback else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if missing_ok and e.code == 404:
            e.close()
            return None
        raw = e.read(300).decode("utf-8", "replace")
        e.close()
        try:
            parsed = json.loads(raw)
            raw = parsed.get("error", raw) if isinstance(parsed, dict) else raw
        except ValueError:
            pass
        hint = "；管理接口可能尚未加载，请重启实际提供此地址的 All_WP 服务" if e.code in (404, 405) else ""
        fail(f"服务端拒绝({e.code}) {method} {path}: {raw}{hint}；未切换其他存储位置", 1)
    except (UnicodeError, ValueError) as e:
        fail(f"接口未返回有效 JSON: {method} {path} ({e})；请核对服务地址", 1)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        fail(f"连不上采集服务器 {base}(原因: {e})。请在实际使用的 All_WP 目录运行 "
             "`npm run server`；端口不同用 params.server 指定。未切换其他存储位置；"
             "写请求若已发出但响应中断，需查询核实是否入库，勿盲目重发", 1)


def _str_list(v, field: str) -> list:
    """tags 容错:字符串按逗号拆,列表逐项转字符串。"""
    if v in (None, "", []):
        return []
    if isinstance(v, str):
        items = [t.strip() for t in v.split(",") if t.strip()]
    elif isinstance(v, list):
        items = [str(t).strip() for t in v if str(t).strip()]
    else:
        fail(f"{field} 要字符串数组(或逗号分隔字符串)", 2)
    return items


def _norm_attrs(v) -> list:
    """attrs 归一化:[{key,value}] 数组,缺 key 报错。"""
    if not v:
        return []
    if not isinstance(v, list):
        fail("attrs 要 [{key,value}] 数组", 2)
    norm = []
    for a in v:
        if not isinstance(a, dict) or not str(a.get("key") or "").strip():
            fail(f"attrs 项缺 key: {a!r}", 2)
        norm.append({"key": str(a["key"]), "value": str(a.get("value", ""))})
    return norm


def _norm_checklist(v) -> list:
    """checklist 归一化:[{text,done?}] 数组,支持纯字符串项。"""
    if not v:
        return []
    if not isinstance(v, list):
        fail("checklist 要 [{text,done?}] 数组", 2)
    norm = []
    for c in v:
        text = str(c.get("text") if isinstance(c, dict) else c or "").strip()
        if not text:
            fail(f"checklist 项缺 text: {c!r}", 2)
        item = {"text": text}
        if isinstance(c, dict) and "done" in c:
            done = c["done"]
            if isinstance(done, str) and done.lower() in ("true", "false"):
                done = done.lower() == "true"
            if not isinstance(done, bool):
                fail("checklist.done 必须是布尔值 true/false", 2)
            item["done"] = done
        norm.append(item)
    return norm


def _enum_or_none(value, allowed: list, field: str):
    """枚举校验:空返回 None,非法值报错,合法原样返回。"""
    if value in (None, ""):
        return None
    if value not in allowed:
        fail(f"未知 {field} {value!r}: {'/'.join(allowed)}")
    return value


# ── 选择器与合并字段的公共提取(asset/update 共用) ──────────────

def _merge_fields(p: dict, payload: dict):
    """collect/update 通用的合并字段:标量 + tags/attrs/checklist 归一化。"""
    for k in ("url", "ip", "severity", "status", "content"):
        if p.get(k) not in (None, ""):
            payload[k] = p[k]
    if p.get("port") not in (None, ""):
        payload["port"] = str(p["port"])
    tags = _str_list(p.get("tags"), "tags")
    if tags:
        payload["tags"] = tags
    attrs = _norm_attrs(p.get("attrs"))
    if attrs:
        payload["attrs"] = attrs
    cl = _norm_checklist(p.get("checklist"))
    if cl:
        payload["checklist"] = cl


# ── op 实现 ────────────────────────────────────────────────────

def op_health(p: dict, base: str) -> int:
    d = api("GET", "/api/health", base)
    counts = d.get("counts") or {}
    print(f"[健康] ok={d.get('ok')} mode={d.get('mode')} version={d.get('version')}")
    print("  库存: " + (" · ".join(f"{k}={v}" for k, v in counts.items()) or "空"))
    return 0


def op_tree(p: dict, base: str) -> int:
    name = str(p.get("tree") or "").strip()
    if not name:
        fail("tree: params.tree(资产树名)必填")
    trees = api("GET", "/api/trees", base)
    hit = next((t for t in trees if t.get("name") == name), None)
    if not hit:
        have = " / ".join(t.get("name", "?") for t in trees[:10]) or "无"
        fail(f"资产树 {name!r} 不存在。现有: {have}。想新建直接跑 asset(会自动建树)", 1)
    nodes = api("GET", "/api/nodes", base)
    mine = [n for n in nodes if n.get("treeId") == hit.get("id")]
    cap = max(1, min(int(p.get("max_nodes") or 200), 2000))
    kids: dict = {}
    for n in mine:
        kids.setdefault(n.get("parentId"), []).append(n)

    def sort_key(n):
        return (n.get("order") if isinstance(n.get("order"), (int, float)) else 9999,
                n.get("createdAt") or "")

    lines, shown = [], 0

    def walk(pid, depth):
        nonlocal shown
        for n in sorted(kids.get(pid, []), key=sort_key):
            if shown >= cap:
                return
            shown += 1
            mark = f" [{n['severity']}]" if n.get("type") == "vuln" and n.get("severity") else ""
            if n.get("status") and n["status"] != "new":
                mark += f" ({n['status']})"
            lines.append("  " * depth + f"{n.get('name')} <{n.get('type')}>{mark}")
            walk(n.get("id"), depth + 1)

    walk(None, 0)
    print(f"[树] {name} · {len(mine)} 节点")
    print("\n".join(lines) if lines else "  (空树)")
    if len(mine) > shown:
        print(f"…还有 {len(mine) - shown} 个未显示(max_nodes 调大)")
    return 0


def op_note(p: dict, base: str) -> int:
    title = str(p.get("title") or "").strip()
    content = p.get("content")
    if not title:
        fail("note: params.title 必填")
    if not isinstance(content, str) or not content.strip():
        fail("note: params.content 必填(Markdown 正文)")
    ntype = p.get("type")
    if ntype and ntype not in NOTE_TYPES:
        fail(f"未知笔记类型 {ntype!r}: {'/'.join(NOTE_TYPES)}")
    payload = {"title": title, "content": content}
    if p.get("id"):
        payload["id"] = str(p["id"])
    if p.get("category"):
        payload["category"] = str(p["category"])
    if ntype:
        payload["type"] = ntype
    tags = _str_list(p.get("tags"), "tags")
    if tags:
        payload["tags"] = tags
    saved = api("POST", "/api/notes", base, payload)
    print(f"[笔记] 已入库: {saved.get('title')}")
    print(f"  id={saved.get('id')} category={saved.get('category', '')} "
          f"type={saved.get('type', '')} tags={','.join(saved.get('tags') or [])}")
    return 0


def op_asset(p: dict, base: str) -> int:
    tree = str(p.get("tree") or "").strip()
    name = str(p.get("name") or "").strip()
    if not tree:
        fail("asset: params.tree(资产树名)必填")
    if not name:
        fail("asset: params.name(资产名)必填")
    ntype = str(p.get("type") or "note")
    if ntype not in NODE_TYPES:
        fail(f"未知资产类型 {ntype!r}: {'/'.join(NODE_TYPES)}")
    _enum_or_none(p.get("severity"), SEVERITIES, "severity")
    _enum_or_none(p.get("status"), STATUSES, "status")

    payload = {"tree": tree, "name": name, "type": ntype}
    if p.get("node_id") is not None:
        payload["nodeId"] = required_text(p, "node_id")
    if p.get("path") not in (None, ""):
        payload["path"] = p["path"]
    _merge_fields(p, payload)
    if p.get("tree_description"):
        payload["treeDescription"] = str(p["tree_description"])

    r = api("POST", "/api/collect", base, payload)
    node = r.get("node") or {}
    verb = "新建" if r.get("created") else "合并到已有节点"
    print(f"[资产] {name} ({ntype}) {verb}")
    print(f"  树: {'新建' if r.get('treeCreated') else '已有'} \"{tree}\" · "
          f"path 补建 {r.get('chainCreated', 0)} 层")
    print(f"  id={node.get('id')} 位置: /{str(p.get('path') or '').strip('/')}")
    if payload.get("severity"):
        print(f"  severity={payload['severity']}"
              + (f" status={payload['status']}" if payload.get("status") else ""))
    return 0


def op_trees(p: dict, base: str) -> int:
    d = api("GET", "/api/overview", base, missing_ok=True)
    if d is None:
        trees = api("GET", "/api/trees", base)
        nodes = api("GET", "/api/nodes", base)
        counts = {}
        for node in nodes:
            tid = node.get("treeId")
            counts[tid] = counts.get(tid, 0) + 1
        d = {"version": "旧接口兼容", "counts": {"trees": len(trees), "nodes": len(nodes)},
             "trees": [{**t, "nodeCount": counts.get(t.get("id"), 0)} for t in trees]}
    counts = d.get("counts") or {}
    print(f"[总览] 版本={d.get('version')} 库存: "
          + (" · ".join(f"{k}={v}" for k, v in counts.items()) or "空"))
    trees = d.get("trees") or []
    kw = str(p.get("keyword") or "").strip().lower()
    if kw:
        trees = [t for t in trees if kw in str(t.get("name", "")).lower()]
    if not trees:
        tail = f"(关键词 {kw!r} 无匹配)" if kw else "(还没有资产树,asset 入库会自动建)"
        print("  " + tail)
        return 0
    for t in trees:
        desc = f" · {t['description']}" if t.get("description") else ""
        print(f"  {t.get('name')} · {t.get('nodeCount')} 节点{desc}")
    return 0


def op_find(p: dict, base: str) -> int:
    _enum_or_none(p.get("type"), NODE_TYPES, "type")
    _enum_or_none(p.get("severity"), SEVERITIES, "severity")
    _enum_or_none(p.get("status"), STATUSES, "status")
    payload = {}
    for k in ("tree", "keyword", "type", "severity", "status", "tag"):
        if p.get(k) not in (None, ""):
            payload[k] = str(p[k])
    if not payload:
        fail("find: 至少给一个过滤条件(tree/keyword/type/severity/status/tag)", 2)
    if p.get("limit"):
        payload["limit"] = int(p["limit"])
    r = api("POST", "/api/query", base, payload)
    items = r.get("items") or []
    print(f"[搜索] 命中 {r.get('total')}(显示 {r.get('count')})")
    for n in items:
        marks = [n[k] for k in ("severity", "status") if n.get(k) and n[k] != "new"]
        line = f"  {n.get('path')} <{n.get('type')}>"
        if marks:
            line += " [" + "/".join(marks) + "]"
        if n.get("tags"):
            line += f" #{','.join(n['tags'])}"
        print(line + f" (tree={n.get('tree')} id={n.get('id')})")
    if not items:
        print("  (无命中)")
    elif r.get("total", 0) > r.get("count", 0):
        print(f"…还有 {r['total'] - r['count']} 个未显示,limit 调大(默认 50)")
    return 0


def _selector(p: dict) -> dict:
    """定位选择器公共提取:id 优先;否则 tree+path+name+type。"""
    payload = {}
    for k in ("tree", "path", "name", "type", "id"):
        if p.get(k) not in (None, ""):
            payload[k] = str(p[k])
    if not payload.get("tree") and not payload.get("id"):
        fail("params.tree(资产树名,或直接给 id)必填", 2)
    if payload.get("type") and payload["type"] not in NODE_TYPES:
        fail(f"未知资产类型 {payload['type']!r}: {'/'.join(NODE_TYPES)}")
    return payload


def op_del(p: dict, base: str) -> int:
    payload = _selector(p)
    if not payload.get("name") and not payload.get("path") and not payload.get("id"):
        print(f"[删除] 只给了 tree,将删除整棵树 \"{payload['tree']}\" 及其全部节点",
              file=sys.stderr)
    r = api("POST", "/api/remove", base, payload)
    removed = r.get("removed") or {}
    nodes = removed.get("nodes") or []
    if r.get("treeRemoved"):
        print(f"[删除] 整棵树 \"{r.get('tree')}\" 已删,连带 {removed.get('count')} 个节点")
    else:
        head = nodes[0] if nodes else {}
        print(f"[删除] 已级联删除 {removed.get('count')} 个节点: "
              f"{head.get('path')} <{head.get('type')}>")
        for n in nodes[1:]:
            print(f"  - {n.get('path')} <{n.get('type')}>")
    return 0


def op_update(p: dict, base: str) -> int:
    payload = _selector(p)
    if not payload.get("name") and not payload.get("id"):
        fail("update: params.name 必填(或直接给 id 定位)", 2)
    _enum_or_none(p.get("severity"), SEVERITIES, "severity")
    _enum_or_none(p.get("status"), STATUSES, "status")
    if p.get("new_type") and p["new_type"] not in NODE_TYPES:
        fail(f"未知资产类型 {p['new_type']!r}: {'/'.join(NODE_TYPES)}")
    if p.get("new_name") not in (None, ""):
        payload["newName"] = str(p["new_name"]).strip()
    if p.get("new_type"):
        payload["newType"] = str(p["new_type"])
    if p.get("move_to") not in (None, ""):
        payload["moveTo"] = str(p["move_to"])
    _merge_fields(p, payload)

    r = api("POST", "/api/update", base, payload)
    node = r.get("node") or {}
    acts = [a for a, ok in (("改名", r.get("renamed")), ("移动", r.get("moved"))) if ok]
    print(f"[更新] {node.get('name')} <{node.get('type')}> · "
          + ("+".join(acts) if acts else "字段合并"))
    print(f"  id={node.get('id')} 位置: {str(payload.get('moveTo') or '未移动').strip('/')}")
    return 0


COLLECTIONS = ("writeups", "notes", "trees", "nodes")


def required_text(p, key):
    value = p.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"params.{key} 必须是非空字符串", 2)
    return value.strip()


def collection(p):
    value = p.get("collection")
    if value not in COLLECTIONS:
        fail("params.collection 必须是 writeups/notes/trees/nodes", 2)
    return value


def integer(p, key, default, low, high):
    value = p.get(key, default)
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        fail(f"params.{key} 必须是 {low}–{high} 的整数", 2)
    number = int(value)
    if not low <= number <= high:
        fail(f"params.{key} 必须是 {low}–{high} 的整数", 2)
    return number


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def validate_record(value):
    if not isinstance(value, dict):
        fail("data 中每条记录必须是 JSON 对象", 2)
    if "id" in value and (not isinstance(value["id"], str) or not value["id"].strip()):
        fail("记录 id 必须是非空字符串；新记录可省略 id", 2)
    return value


def op_list(p, base):
    coll = collection(p)
    offset = integer(p, "offset", 0, 0, 100000000)
    limit = integer(p, "limit", 50, 1, 2000)
    selected_id = required_text(p, "id") if "id" in p else None
    tree_key = None
    if "tree" in p:
        if coll != "nodes":
            fail("params.tree 仅用于 nodes 集合", 2)
        tree_key = required_text(p, "tree")
    items = api("GET", "/api/" + coll, base)
    if not isinstance(items, list):
        fail("集合接口返回值不是数组", 1)
    if selected_id is not None:
        items = [x for x in items if str(x.get("id")) == selected_id]
    if tree_key is not None:
        trees = api("GET", "/api/trees", base)
        ids = {t["id"] for t in trees if t.get("id") == tree_key or t.get("name") == tree_key}
        items = [x for x in items if x.get("treeId") in ids]
    emit({"collection": coll, "total": len(items), "offset": offset,
          "items": items[offset:offset + limit]})
    return 0


def op_save(p, base):
    coll = collection(p)
    data = p.get("data")
    if isinstance(data, list):
        if not data:
            fail("params.data 数组不能为空", 2)
        for record in data:
            validate_record(record)
    else:
        validate_record(data)
    emit(api("POST", "/api/" + coll, base, data))
    return 0


def op_replace(p, base):
    coll = collection(p)
    rid = required_text(p, "id")
    data = validate_record(p.get("data"))
    if "id" in data and data["id"] != rid:
        fail("params.id 与 data.id 不一致", 2)
    emit(api("PUT", "/api/" + coll + "/" + quote(rid, safe=""), base, data))
    return 0


def op_delete(p, base):
    coll = collection(p)
    rid = required_text(p, "id")
    emit(api("DELETE", "/api/" + coll + "/" + quote(rid, safe=""), base))
    return 0


def op_clear(p, base):
    coll = collection(p)
    # 原接口清空单集合，不级联清空其他集合；不隐式扩展删除范围。
    emit(api("DELETE", "/api/" + coll, base))
    return 0


def resolve_file(p):
    value = required_text(p, "path")
    target = Path(value)
    if target.is_absolute():
        return target
    if target.drive or target.root:
        fail("path 必须是完整绝对路径或会话内相对路径", 2)
    session = os.environ.get("AGENT_SESSION", "default")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", session) or session in (".", ".."):
        fail("AGENT_SESSION 不是合法会话名", 2)
    root = (Path(__file__).resolve().parents[2] / "workspace" / session / "files").resolve()
    target = (root / target).resolve()
    if not target.is_relative_to(root):
        fail("相对 path 不能超出会话 files 目录", 2)
    return target


def validate_backup(data):
    if not isinstance(data, dict) or not data or set(data) - set(COLLECTIONS):
        fail("备份必须是仅含 writeups/notes/trees/nodes 的非空对象", 2)
    for key, records in data.items():
        if not isinstance(records, list):
            fail(f"备份 {key} 必须是数组", 2)
        for record in records:
            validate_record(record)
            if not record.get("id"):
                fail("导入的每条记录必须带 id，避免服务端静默跳过", 2)
    return data


def op_export(p, base):
    params = dict(p)
    if "path" not in params:
        params["path"] = "wpbox/export-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".json"
    target = resolve_file(params)
    data = validate_backup(api("GET", "/api/export", base))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 显式导出文件；拒绝覆盖已有文件，不是接口失败后的备用写入。
        with target.open("x", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        fail(f"导出保存失败: {e}", 1)
    emit({"path": str(target), "counts": {k: len(v) for k, v in data.items()}})
    return 0


def op_import(p, base):
    if ("path" in p) == ("data" in p):
        fail("import 必须且只能提供 path 或 data 其中一个", 2)
    if "path" in p:
        target = resolve_file(p)
        try:
            if target.stat().st_size > 32 * 1024 * 1024:
                fail("备份文件超过 32MB 上限", 2)
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            fail(f"读取导入文件失败: {e}", 2)
    else:
        data = p["data"]
    emit(api("POST", "/api/import", base, validate_backup(data)))
    return 0


OPS = {"health": op_health, "trees": op_trees, "tree": op_tree, "find": op_find,
       "asset": op_asset, "update": op_update, "del": op_del, "note": op_note,
       "list": op_list, "save": op_save, "replace": op_replace, "delete": op_delete,
       "clear": op_clear, "export": op_export, "import": op_import}


def main() -> int:
    try:
        req = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError) as e:
        fail(f"参数必须是 JSON 对象: {e}")
    if not isinstance(req, dict):
        fail("参数必须是 JSON 对象(顶层不是对象)", 2)
    op = str(req.get("op") or "")
    params = req.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        fail("params 必须是对象", 2)
    fn = OPS.get(op)
    if not fn:
        fail(f"未知 op {op!r}。可用: {'/'.join(OPS)}", 2)
    base = str(params.get("server") or DEFAULT_BASE).rstrip("/")
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            fail("params.server 必须是 HTTP(S) 服务根地址，不能含 query/fragment", 2)
        parsed.port
    except ValueError:
        fail("params.server 地址或端口不合法", 2)
    return fn(params, base)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as e:
        fail(f"参数或服务端数据格式不合法: {e}", 2)
