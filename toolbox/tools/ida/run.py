"""IDA Pro 逆向卡执行体:op 分发 → ida-pro-mcp HTTP 服务(IDA 插件,默认 :13337/mcp)。

协议(读 ida-pro-mcp 0.1.0 zeromcp 源码实证):POST /mcp 纯 JSON 响应(SSE 只在
GET /sse 老通道);require_streamable_http_session=False → 无需 initialize 握手,
直接 tools/call 即应答。成功时 content[0].text = 工具返回值的紧凑 JSON;
超限输出会追加第二个 content 文本(下载提示),一并透传给模型。

错误形态三种都要接:
  ① HTTP 层:IDA 没开 → ConnectionRefused,报错带启动提示
  ② JSON-RPC isError=true:未知工具/参数校验(-32601/-32602)被包成 content 文本
  ③ isError=false 但结果 JSON 里塞错误:{"error": ...} 全员皆错 / {"stderr": "Traceback ..."}
     (py_eval 类)——老捆绑卡时代的老毛病,这代服务端仍存在,照 mcp_bridge.remote_error 移植

退出码:0=成功;1=连不上/isError/结果内嵌错误(原文照吐 stdout,一行原因进 stderr);
2=op 校验失败(带近似建议;探活连不上也归这里,不被网络劫持)。
"""
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = os.environ.get("IDA_URL", "http://127.0.0.1:13337/mcp")
RPC_ID = 0

# op → (组, 用途)。与 tool.yaml 的 usage 分组一致;分组只是排版,危险等级看 tool.yaml op_danger。
# 相比老捆绑卡 _IDA_OPS 的差异(按 0.1.0 源码核对):剔除 open_file/select_instance/
# list_instances/server_warmup(idalib/headless 侧或已除名,IDA 插件这代没有),
# 新收 add_bookmark/set_op_type/make_data/delete_stack/force_recompile。
_GROUPS = ("常用·只读", "写操作", "不常用")
_OPS = {
    # 常用·只读 —— 逆向 90% 的动作
    "survey_binary":     ("常用·只读", "二进制总览:架构/段/入口/导入/字符串/热点函数,分析第一步(函数>10k 用 minimal)", ["detail_level?"]),
    "decompile":         ("常用·只读", "反编译函数为 C 伪代码", ["addr", "include_addresses?"]),
    "disasm":            ("常用·只读", "反汇编,offset/max_instructions 分页", ["addr", "max_instructions?", "offset?", "include_total?"]),
    "xrefs_to":          ("常用·只读", "查地址/函数被谁引用(批量)", ["addrs", "limit?"]),
    "xref_query":        ("常用·只读", "交叉引用富查询:方向/类型/分页/去重", ["queries"]),
    "get_bytes":         ("常用·只读", "读内存原始字节(regions 批量)", ["regions"]),
    "get_string":        ("常用·只读", "读地址处字符串(批量)", ["addrs"]),
    "get_int":           ("常用·只读", "按类型读整数:i8/u32/u64/le/be 批量", ["queries"]),
    "get_global_value":  ("常用·只读", "按名或地址读全局变量值", ["queries"]),
    "find":              ("常用·只读", "搜字符串/立即数/对地址的引用", ["type", "targets", "limit?", "offset?"]),
    "find_regex":        ("常用·只读", "正则搜字符串(忽略大小写)", ["pattern", "limit?", "offset?"]),
    "find_bytes":        ("常用·只读", "字节模式搜索,?? 通配", ["patterns", "limit?", "offset?"]),
    "search_text":       ("常用·只读", "反汇编文本搜索(字面/正则,只扫代码段)", ["pattern", "limit?", "start?", "end?", "regex?", "case_sensitive?", "include?", "code_only?"]),
    "list_funcs":        ("常用·只读", "函数清单:filter 通配/正则 + 分页", ["queries"]),
    "lookup_funcs":      ("常用·只读", "按地址或名字定位函数", ["queries"]),
    "func_query":        ("常用·只读", "函数富查询:大小/类型/名字过滤", ["queries"]),
    "imports":           ("常用·只读", "导入表(offset/count 分页)", ["offset", "count"]),
    "imports_query":     ("常用·只读", "导入富查询:按模块/名字过滤", ["queries"]),
    "entity_query":      ("常用·只读", "IDB 实体查询:functions/globals/imports/strings/names", ["queries"]),
    "py_eval":           ("常用·只读", "IDA 进程内跑 Python(万能兜底)。无 stdin、只能 print;读数据用 ida_bytes.get_dword 等,别把工具结果手抄进代码", ["code"]),
    "server_health":     ("常用·只读", "探活:MCP 连接与当前 IDB 状态", []),
    "list_ops":          ("常用·只读", "本卡元操作:列 IDA 端实际启用的操作清单(插件版本/config.html 关工具导致漂移时先看它)", []),
    # 写操作 —— 改 IDB
    "rename":            ("写操作", "批量改名:函数/全局/局部/栈变量,dry_run 可选", ["batch"]),
    "set_comments":      ("写操作", "设注释(汇编+伪代码两视图同步)", ["items"]),
    "append_comments":   ("写操作", "追加注释,同文自动去重", ["items"]),
    "patch":             ("写操作", "写字节补丁(hex)", ["patches"]),
    "patch_asm":         ("写操作", "写汇编补丁", ["items"]),
    "put_int":           ("写操作", "写整数到内存", ["items"]),
    "declare_stack":     ("写操作", "从类型声明创建栈变量", ["items"]),
    "delete_stack":      ("写操作", "删除栈变量", ["items"]),
    "declare_type":      ("写操作", "声明 C 类型进本地类型库", ["decls"]),
    "set_type":          ("写操作", "应用类型:函数签名/全局/局部/栈", ["edits"]),
    "set_op_type":       ("写操作", "设指令操作数类型(结构体偏移/offset/格式)", ["items"]),
    "make_data":         ("写操作", "建类型化数据符号(硬化符号边界)", ["items"]),
    "enum_upsert":       ("写操作", "创建/扩展枚举(幂等,不破坏已有成员)", ["queries"]),
    "infer_types":       ("写操作", "在地址处推断并应用类型", ["addrs"]),
    "diff_before_after": ("写操作", "改名/改类型前后对照,确认改动生效", ["addr", "action", "action_args"]),
    "define_func":       ("写操作", "把地址定义成函数(可指定边界)", ["items"]),
    "define_code":       ("写操作", "把字节转成代码指令", ["items"]),
    "undefine":          ("写操作", "把代码/数据转回原始字节", ["items"]),
    "add_bookmark":      ("写操作", "加书签(地址+名字)", ["addr", "name", "prefix?"]),
    "force_recompile":   ("写操作", "强制重编译伪代码(改类型后 Hex-Rays 缓存不刷新时)", ["items?"]),
    "type_apply_batch":  ("写操作", "批量应用类型改动", ["batch"]),
    "idb_save":          ("写操作", "保存 IDB(可另存路径)", ["path?"]),
    # 不常用 —— 细粒度/辅助,参数结构报错会指明缺失字段
    "analyze_batch":     ("不常用", "多函数批量分析:自选板块(反汇编/xref/调用者…)", ["queries"]),
    "analyze_component": ("不常用", "一组函数组件分析:摘要+内部调用图+共享数据", ["addrs"]),
    "analyze_function":  ("不常用", "单函数紧凑全量分析(伪码/字符串/常量/调用关系)", ["addr", "include_asm?"]),
    "basic_blocks":      ("不常用", "函数 CFG 基本块(分页)", ["addrs", "max_blocks?", "offset?"]),
    "callees":           ("不常用", "各函数的被调清单(去重)", ["addrs", "limit?"]),
    "callgraph":         ("不常用", "有界调用图:深度/节点/边数上限", ["roots", "max_depth?", "max_nodes?", "max_edges?", "max_edges_per_func?"]),
    "export_funcs":      ("不常用", "导出函数为 json / C 头 / 原型", ["addrs", "format?"]),
    "find_xref_signatures": ("不常用", "为引用目标地址的代码点生成特征码", ["addrs", "format?", "top?", "max_length?"]),
    "func_profile":      ("不常用", "函数画像:调用者/被调/字符串/常量清单", ["queries"]),
    "insn_query":        ("不常用", "指令级查询:助记符/操作数过滤,可限定范围", ["queries"]),
    "int_convert":       ("不常用", "进制换算:hex/dec/bin/ascii", ["inputs"]),
    "list_globals":      ("不常用", "全局变量清单(分页)", ["queries"]),
    "make_signature":    ("不常用", "从地址生成最短唯一特征码", ["addrs", "format?", "wildcard_operands?", "max_length?"]),
    "make_signature_for_function": ("不常用", "函数入口特征码", ["addrs", "format?", "wildcard_operands?", "max_length?"]),
    "make_signature_for_range": ("不常用", "地址范围特征码(不保证唯一)", ["start", "end", "format?", "wildcard_operands?"]),
    "py_exec_file":      ("不常用", "IDA 进程内跑脚本文件。无 stdin、只能 print", ["file_path"]),
    "read_struct":       ("不常用", "按结构体字段读内存(自动探测类型)", ["queries"]),
    "search_structs":    ("不常用", "按名搜本地结构体", ["filter"]),
    "stack_frame":       ("不常用", "函数栈帧变量表", ["addrs"]),
    "trace_data_flow":   ("不常用", "数据流多跳追踪:forward=流向 / backward=来源", ["addr", "direction?", "max_depth?"]),
    "type_inspect":      ("不常用", "类型检查:大小/成员布局", ["queries"]),
    "type_query":        ("不常用", "类型目录查询:过滤/分页/关系", ["queries"]),
    "xrefs_to_field":    ("不常用", "查结构体某字段的引用", ["queries"]),
}
# 卡面元操作(本地实现,不转发远端)
_LOCAL_OPS = {"list_ops"}
# 写操作/在 IDA 进程内执行代码的操作 → unknown(按写处理,网关防兜圈用)
_WRITE_OPS = {
    "rename", "set_comments", "append_comments", "patch", "patch_asm", "put_int",
    "declare_stack", "delete_stack", "declare_type", "set_type", "set_op_type",
    "make_data", "enum_upsert", "infer_types", "diff_before_after", "define_func",
    "define_code", "undefine", "add_bookmark", "force_recompile",
    "type_apply_batch", "idb_save", "py_eval", "py_exec_file",
}


def fail(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def rpc(method: str, params: dict, url: str, notify: bool = False,
        soft: bool = False) -> dict | None:
    """一次 JSON-RPC POST。响应恒为 JSON(zeromcp 的 POST /mcp 不走 SSE);
    留一手:万一未来版本回 text/event-stream,按 SSE 帧解析 data: 行。
    soft=True 时失败不退出进程而是抛 RuntimeError(未知 op 探活用——
    校验是客户端的事,不能被"IDA 没开"劫持成连接错误)。"""
    global RPC_ID
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if not notify:
        RPC_ID += 1
        body["id"] = RPC_ID
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
            ctype = (r.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        err = f"IDA MCP 服务拒绝({e.code}): {e.read().decode('utf-8', 'replace')[:300]}"
    except (urllib.error.URLError, OSError) as e:
        err = (f"连不上 IDA MCP 服务 {url}(原因: {e})。先打开 IDA Pro 并加载"
               f"ida-pro-mcp 插件(Edit→Plugins→MCP,监听 :13337,IDA 开着才有);"
               f"端口不同就用环境变量 IDA_URL 指对地址")
    else:
        err = ""
    if err:
        if soft:
            raise RuntimeError(err)
        fail(err, 1)
    if not raw:                      # 202 等空响应 = notification 被接受
        return None
    if "text/event-stream" in ctype:
        payload = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip() or "null")
                except json.JSONDecodeError:
                    continue
        return payload
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        err = f"IDA MCP 服务响应不是 JSON: {e}; 原文前 200 字: {raw[:200]}"
        if soft:
            raise RuntimeError(err)
        fail(err, 1)


def handshake(url: str) -> None:
    rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "agg-ida-tool", "version": "1"}}, url)
    rpc("notifications/initialized", {}, url, notify=True)


def content_texts(result: dict) -> list:
    parts = [c.get("text", "") for c in result.get("content", [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return [p for p in parts if p]


def last_error_line(text: str) -> str:
    """Traceback 的有效信息在最后一行(如 RuntimeError: lost sys.stdin)。"""
    lines = [l.strip() for l in text.replace("\\n", "\n").splitlines() if l.strip()]
    for l in reversed(lines):
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b", l):
            return l[:200]
    return (lines[-1] if lines else text)[:200]


def remote_error(value) -> str:
    """isError=false 但结果 JSON 里塞错误:{"error": ...} 全员皆错 / {"stderr": "Traceback"}。"""
    if isinstance(value, list):
        items = [v for v in value if isinstance(v, dict)]
        if not items:
            return ""
        for v in items:
            stderr = v.get("stderr")
            if isinstance(stderr, str) and "Traceback" in stderr:
                return last_error_line(stderr)
        errs = [str(v["error"]) for v in items if v.get("error")]
        if errs and len(errs) == len(items):    # 批量个别失败不算整体失败
            return errs[0][:200]
        return ""
    if isinstance(value, dict):
        stderr = value.get("stderr")
        if isinstance(stderr, str) and "Traceback" in stderr:
            return last_error_line(stderr)
        if value.get("error"):
            return str(value["error"])[:200]
    return ""


def _load_json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def call_tool(op: str, args: dict, url: str) -> str:
    resp = rpc("tools/call", {"name": op, "arguments": args}, url)
    err = (resp or {}).get("error")
    if err and err.get("code") == -32002:     # 服务端要求先握手(未来版本可能收紧)
        handshake(url)
        resp = rpc("tools/call", {"name": op, "arguments": args}, url)
        err = (resp or {}).get("error")
    if err:
        fail(f"IDA MCP 返回协议错误 [{err.get('code')}]: {err.get('message')}", 1)
    result = (resp or {}).get("result") or {}
    parts = content_texts(result)
    text = "\n".join(parts).replace("\r\n", "\n").strip() or "(空输出)"
    if result.get("isError"):
        print(text)
        fail(last_error_line(text), 1)
    # content[0] 是结果 JSON;纯字符串(如 py_eval 的 stdout)解包直吐更可读。
    # \r 全部归一成 \n:Windows 文本 stdout 会把串内 \n 再翻成 \r\n,
    # 与残留 \r 叠成 \r\r\n,读回来就是双空行
    reason = ""
    if parts:
        value = _load_json(parts[0])
        reason = remote_error(value)
        out = value if isinstance(value, str) else parts[0]
    else:
        out = text
    if isinstance(out, str):
        out = out.replace("\r\n", "\n").replace("\r", "\n")
    if parts[1:]:                             # 超限输出的下载提示等附加文本
        out = (out.rstrip() + "\n" + "\n".join(parts[1:])).rstrip("\n")
    if reason:
        print(out)
        fail(reason, 1)
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def list_ops(url: str) -> str:
    """列远端实际启用的操作(含参数提示),标注卡面是否收录——版本漂移的自查入口。"""
    resp = rpc("tools/list", {}, url)
    if (resp or {}).get("error"):
        fail(f"IDA MCP tools/list 失败: {resp['error'].get('message')}", 1)
    tools = ((resp or {}).get("result") or {}).get("tools") or []
    lines = [f"IDA 端启用 {len(tools)} 个操作([卡]=本卡已收录并带用法,[未]=远端有但卡面未收录,"
             f"仍可按 {json.dumps({'op': '...', 'params': {}})} 直调):"]
    known = set(_OPS) - _LOCAL_OPS
    ordered = [t["name"] for t in tools if t.get("name") in known]
    extra = sorted(t.get("name", "?") for t in tools if t.get("name") not in known)
    live = {t.get("name"): t for t in tools}
    for name in ordered + extra:
        t = live.get(name) or {}
        props = ((t.get("inputSchema") or {}).get("properties")) or {}
        req = set(((t.get("inputSchema") or {}).get("required")) or [])
        hint = ", ".join(k + ("" if k in req else "?") for k in list(props)[:6])
        mark = "[卡]" if name in known else "[未]"
        lines.append(f"{mark} {name}({hint})")
    dead = [n for n in known if n not in live]
    if dead:
        lines.append(f"卡面收录但远端未启用(调用会报 Method not found): {', '.join(sorted(dead))}")
    return "\n".join(lines)


def main() -> int:
    try:
        req = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError) as e:
        fail(f"参数必须是 JSON 对象: {e}")
    op = str(req.get("op") or "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        fail("params 必须是对象", 2)
    if op == "list_ops":
        text = list_ops(DEFAULT_URL)
    elif op in _OPS:
        text = call_tool(op, params, DEFAULT_URL)
    else:
        # 卡面没有的 op 不立即枪毙:远端插件版本可能更新,查一次 tools/list,
        # 远端真有就放行(版本漂移兜底)。连不上也不劫持成连接错误——
        # op 校验是客户端的事,照常给近似建议 exit 2
        live = []
        conn_note = ""
        try:
            resp = rpc("tools/list", {}, DEFAULT_URL, soft=True)
            live = [t.get("name") for t in (((resp or {}).get("result") or {}).get("tools") or [])]
        except RuntimeError as e:
            conn_note = f"\n(无法核对远端操作表: {str(e).splitlines()[0][:120]})"
        if op in live:
            text = call_tool(op, params, DEFAULT_URL)
        else:
            pool = sorted((set(_OPS) - _LOCAL_OPS) | set(live))
            near = difflib.get_close_matches(op, pool, n=3)
            hint = f" 是不是想用: {'/'.join(near)}" if near else \
                f"。可用操作 {len(pool)} 个,或 op=list_ops 看远端实际清单"
            fail(f"未知 op {op!r}{hint}{conn_note}", 2)
    try:
        print(text)
    except BrokenPipeError:             # 消费方提前关管道(head):拿到即成功
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
