"""dnSpy .NET 逆向卡执行体:op 分发 → dnSpy MCP 扩展(:5150)。

协议实测(v1.5.1):streamable HTTP 无状态免握手——直接 POST tools/call 即应答,
响应纯 JSON(非 SSE)。留一手:若服务端返回 -32002(未初始化)则补握手后重试一次。

退出码:0=成功(含服务端文本级失败如 "Method not found",模型读文自纠);
1=连不上 dnSpy / 服务端 isError;2=op 或参数校验失败。
"""
import difflib
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = os.environ.get("DNSPY_URL", "http://127.0.0.1:5150/mcp")
RPC_ID = 0


def fail(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def rpc(method: str, params: dict, url: str, notify: bool = False) -> dict | None:
    """一次 JSON-RPC POST。notification 无 id、无响应体。"""
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
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        fail(f"dnSpy 扩展拒绝({e.code}): {e.read().decode('utf-8', 'replace')[:300]}", 1)
    except (urllib.error.URLError, OSError) as e:
        fail(f"连不上 dnSpy MCP 扩展 {url}(原因: {e})。先打开 dnSpyEX"
             f"(F:\\agg\\external\\dnspyEX,带 dnspy-mcp 扩展,打开后自动监听 :5150);"
             f"端口不同就用环境变量 DNSPY_URL 指对地址", 1)
    if not raw:                      # 204 等空响应 = notification 被接受
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"dnSpy 扩展响应不是 JSON: {e}; 原文前 200 字: {raw[:200]}", 1)


def handshake(url: str) -> None:
    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "agg-dnspy-tool", "version": "1"}}, url)
    rpc("notifications/initialized", {}, url, notify=True)


def call_tool(op: str, args: dict, url: str) -> str:
    resp = rpc("tools/call", {"name": op, "arguments": args}, url)
    err = (resp or {}).get("error")
    if err and err.get("code") == -32002:     # 服务端要求先握手(未来版本可能收紧)
        handshake(url)
        resp = rpc("tools/call", {"name": op, "arguments": args}, url)
        err = (resp or {}).get("error")
    if err:
        fail(f"dnSpy 扩展返回协议错误 [{err.get('code')}]: {err.get('message')}", 1)
    result = (resp or {}).get("result") or {}
    text = "\n".join(c.get("text", "") for c in result.get("content", [])
                     if isinstance(c, dict) and c.get("type") == "text")
    text = text.replace("\r\n", "\n").strip() or "(空输出)"
    if result.get("isError") or result.get("structuredError"):
        fail(text, 1)
    return text


OPS_TABLE = {   # op → (组, 一句用途);与 tool.yaml usage 一致,unknown op 时给近似建议
    "assembly_overview": "程序集总览", "list_loaded_assemblies": "已加载清单",
    "search_types": "搜类型", "search_methods": "搜方法", "search_strings": "搜字符串",
    "search_constants": "搜常量", "grep": "跨类型 grep", "decompile_type": "反编译类型",
    "decompile_method": "反编译方法", "get_type_members": "列成员",
    "get_type_hierarchy": "继承链", "get_fields": "字段详情",
    "get_method_signatures": "方法签名", "get_enum_values": "枚举值",
    "get_xrefs_to": "谁引用它", "get_callees": "它调用谁",
    "rename_class": "类改名", "rename_method": "方法改名", "rename_namespace": "命名空间改名",
    "update_method_body": "补丁方法体", "refresh_u_i": "刷新 dnSpy 界面",
    "get_method_il": "IL 指令", "get_il_opcodes_formatted": "格式化 IL",
    "get_method_body": "IL 字节", "get_attributes": "特性", "get_method_attributes": "方法特性",
    "get_properties": "属性详情", "assembly_list_namespaces": "命名空间清单",
    "assembly_list_types": "类型清单", "assembly_get_references": "程序集引用",
    "get_global_namespaces": "全局类型", "get_resources": "嵌入资源",
    "get_resource_data": "读资源数据", "get_metadata": "PE/元数据",
    "decompile_assembly": "整程序集反编译", "get_selected_node": "GUI 选中节点",
}


def main() -> int:
    try:
        req = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError) as e:
        fail(f"参数必须是 JSON 对象: {e}")
    op = str(req.get("op") or "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        fail("params 必须是对象", 2)
    if op not in OPS_TABLE:
        near = difflib.get_close_matches(op, OPS_TABLE, n=3)
        hint = f" 是不是想用: {'/'.join(near)}" if near else \
            f"。可用操作 {len(OPS_TABLE)} 个,完整操作表见工具卡 usage"
        fail(f"未知 op {op!r}{hint}", 2)
    text = call_tool(op, params, DEFAULT_URL)
    try:
        print(text)
    except BrokenPipeError:             # 消费方提前关管道(head):拿到即成功
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
