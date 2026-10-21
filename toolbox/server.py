"""toolbox —— 工具网关 MCP server。

默认暴露 5 个 meta 工具（工具面约 400 token），工具本体一个都不直接暴露：
内置工具（tools/ 目录）、外部 MCP 工具（mcp.json 桥接，见 mcp_bridge.py）、
Claude skills（SKILL.md 编目，见 skill_bridge.py）三类来源统一经
search_tools 发现、run_tool 执行、read_output 钻取。
模型上下文贡献不随工具数量增长。另加 dump_tools：全量工具卡出口，
供网关的"大模型直连"模式组装原生工具表（执行仍走 run_tool 管道）。
"""
import asyncio
import json
import re
import time

from mcp.server.mcpserver import MCPServer

import audit
import mcp_bridge
import normalize
import registry
import runner
import skill_bridge

mcp = MCPServer("toolbox")
_bridge = mcp_bridge.McpBridge()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


async def _all_tools(filtered: bool = True) -> dict:
    """内置 + skill + 外部 MCP 的合并视图（重名时内置优先），再按网关下发的工具集过滤。
    filtered=False 仅供 dump_tools(admin=True) 给管理页列全量。"""
    await _bridge.ensure()
    tools = registry.load_tools()
    for name, t in skill_bridge.catalog().items():
        tools.setdefault(name, t)
    for name, t in _bridge.catalog().items():
        tools.setdefault(name, t)
    return registry.apply_toolset(tools) if filtered else tools


@mcp.tool()
async def list_categories() -> str:
    """列出全部工具分类及各类工具名。不确定用什么工具时先看这个。"""
    tools = await _all_tools()
    cats = registry.categories(tools)
    audit.log_event("search", query="(list_categories)",
                    results={k: len(v) for k, v in cats.items()}, tokens_est=0,
                    tools_seen=sorted(tools.keys()))
    return _json({"categories": cats})


@mcp.tool()
async def dump_tools(admin: bool = False) -> str:
    """返回全部工具的完整工具卡（含参数 schema），供网关组装"大模型直连"工具面。仅供网关调用。
    admin=true 时忽略工具集过滤并附带管理字段（关键词/超时/来源/可否编辑），供工具管理页使用。"""
    tools = await _all_tools(filtered=not admin)
    ordered = sorted(tools.values(), key=lambda t: (t.category, t.name))
    cards = [registry.admin_card(t) if admin else registry.card(t, detail=True) for t in ordered]
    if not admin:
        audit.log_event("search", query="(dump_tools)",
                        results=[t.name for t in ordered], tokens_est=0)
    return _json({"tools": cards})


@mcp.tool()
async def describe_tool(tool_name: str) -> str:
    """查看单个工具的完整说明：参数 JSON Schema、用法要点、示例。search_tools 返回的是紧凑卡，参数有疑问时用它。"""
    tool = (await _all_tools()).get(tool_name)
    if tool is None:
        return _json({"status": "unknown_tool", "tool": tool_name,
                      "hint": "未知工具。先 search_tools 查找。"})
    return _json({"status": "ok", **registry.card(tool, detail=True)})


@mcp.tool()
async def search_tools(query: str, category: str = "", limit: int = 5) -> str:
    """按关键词搜索工具，返回紧凑工具卡：sig 为调用签名（? 表示可选参数），example 为可直接照抄的参数。中英文均可，如'写文件'、'edit file'；query 留空 = 列出全部。需要完整参数说明时用 describe_tool。"""
    hits = registry.search(query, category, await _all_tools(), limit=max(1, min(limit, 20)))
    resp = {"results": [registry.card(t) for t, _ in hits]}
    if not hits:
        resp["hint"] = "无匹配。换中英文关键词重试，或 query 留空列出全部。"
    audit.log_event("search", query=query, category=category,
                    results=[t.name for t, _ in hits],
                    tokens_est=normalize.estimate_tokens(_json(resp)))
    return _json(resp)


@mcp.tool()
async def run_tool(tool_name: str, params: dict) -> str:
    """执行工具。tool_name 与 params 来自 search_tools 的工具卡（sig/example）；参数不符 schema 会被拒绝并指明错误字段，改正后重试。返回统一信封 {status, tool, call_id, summary, output|output_head+hint}；输出过大时用 read_output 查看全量。"""
    builtins = registry.load_tools()
    if tool_name in builtins:
        tool = registry.apply_toolset(builtins).get(tool_name)
    else:
        tool = (await _all_tools()).get(tool_name)
    if tool is None:
        audit.log_event("validation_error", tool=tool_name, reason="unknown_tool")
        return _json({"status": "unknown_tool", "tool": tool_name,
                      "hint": "未知工具。先 search_tools 查找。"})
    call_id = audit.next_call_id()

    if tool.source == "skill":     # Claude skill：执行 = 把 SKILL.md 正文喂给模型
        started = time.time()
        try:
            raw = skill_bridge.body_of(tool)
            status = "ok"
        except Exception as e:
            raw, status = f"skill 读取失败: {e}", "error"
        return _json(_finalize_remote(tool, params, call_id, raw, status, started))

    if tool.source != "builtin":   # 外部 MCP 工具：走桥接，输出同样归一化落盘
        if (err := runner.validate(tool, params)):
            return _json(err)
        src = tool.source.removeprefix("mcp:")
        # 捆绑源（如 ida）：拆 {op, params} 再转发；信封/审计记 ida.op，
        # 模型和前端看到的就是实际执行的操作
        call_name, call_params, display = tool_name, params, tool.name
        if mcp_bridge.is_bundled(src):
            try:
                op, call_params = mcp_bridge.unpack_bundle(params, _bridge.op_names(src))
                call_name, display = op, f"{tool.name}.{op}"
            except ValueError as e:
                audit.log_event("validation_error", tool=tool.name, params=params,
                                reason="bad_op", detail=str(e))
                return _json({"status": "validation_error", "tool": tool.name,
                              "field": "op", "detail": str(e)})
        started = time.time()
        try:
            raw = await _bridge.call(src, call_name, call_params)
            status, reason = "ok", ""
        except mcp_bridge.RemoteToolError as e:
            # 远端报错（isError 或 JSON 里的 error/Traceback）：原文照常落盘，
            # 信封标 error 并把一行原因放进 summary，模型第一眼就知道哪里错了
            raw, status, reason = e.raw, "error", e.reason
        except Exception as e:
            raw, status, reason = f"bridge error: {e}", "error", ""
        # 审计参数记外层 {op, params} 原样（重放保真），信封工具名带操作
        result = _finalize_remote(tool, params, call_id, raw, status, started, name=display)
        if reason:
            result["summary"] = f"失败: {reason}"
            hint = _ERROR_HINTS.get(call_name)
            if hint:
                result["hint"] = hint
        return _json(result)

    return _json(await asyncio.to_thread(runner.execute, tool, params, call_id))


# 外部工具失败时附给模型的纠错提示（按工具名）
_ERROR_HINTS = {
    "py_eval": "py_eval 运行在 IDA 进程内：没有 stdin（不能用 input()），没有终端；"
               "只用 print 输出。读数据用 ida_bytes.get_dword/get_byte，不要把字节手抄进代码。",
    "py_exec_file": "脚本运行在 IDA 进程内：没有 stdin，不能用 input()；只用 print 输出。",
}


def _finalize_remote(tool, params, call_id, raw, status, started, name: str = "") -> dict:
    """外部工具结果与内置工具同一套归一化/落盘/审计管道。
    name 覆盖信封里的工具名（捆绑源传 ida.op，让模型/前端看到实际操作）。"""
    name = name or tool.name
    duration_ms = int((time.time() - started) * 1000)
    out_path = audit.output_path(call_id)
    out_path.write_text(raw, encoding="utf-8")
    result = normalize.normalize(raw, call_id, str(out_path), duration_ms, status,
                                 tool=name)
    audit.log_event("tool_call", tool=f"{tool.source}/{name}", call_id=call_id,
                    params=params, status=status, duration_ms=duration_ms,
                    tokens_est=result["stats"]["tokens_est"],
                    normalized={"summary": result["summary"],
                                "truncated": result["stats"]["truncated"]})
    return result


@mcp.tool()
async def read_output(call_id: str, offset: int = 0, limit: int = 50, grep: str = "") -> str:
    """分页查看某次调用(call_id)的完整落盘输出。offset/limit 按行翻页(行号从0计)；grep 为正则(忽略大小写)过滤，如 grep='open'。超长行（如压缩过的 HTML）会截断显示。"""
    path = audit.output_path(call_id)
    if not path.exists():
        return _json({"status": "not_found", "call_id": call_id,
                      "hint": "无此调用记录。call_id 来自 run_tool 的返回。"})
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep:
        try:
            rx = re.compile(grep, re.IGNORECASE)
            lines = [l for l in lines if rx.search(l)]
        except re.error as e:
            return _json({"status": "validation_error", "field": "grep",
                          "detail": str(e)})
    total = len(lines)
    limit = max(1, min(limit, 200))
    MAX_LINE_CHARS = 2000   # 压缩 HTML 单行可达数万字符，不截断会撑爆上下文
    page = [f"{offset + i + 1}: "
            + ((l[:MAX_LINE_CHARS] + " …[行已截断]") if len(l) > MAX_LINE_CHARS else l)
            for i, l in enumerate(lines[offset:offset + limit])]
    resp = {"status": "ok", "call_id": call_id, "total_lines": total,
            "offset": offset, "returned": len(page), "lines": page}
    if offset + limit < total:
        resp["next_offset"] = offset + limit
    audit.log_event("output_read", call_id=call_id, grep=grep, offset=offset,
                    returned=len(page),
                    tokens_est=normalize.estimate_tokens(_json(resp)))
    return _json(resp)


if __name__ == "__main__":
    mcp.run()
