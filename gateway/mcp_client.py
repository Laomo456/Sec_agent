"""把外部 MCP 工具服务器挂进循环（可选组件）。

config.yaml 的 tools 段配置 command/args 即接入任一 MCP stdio 服务器；
不配置则 agent 以纯对话模式运行（工具表为空）。
每个 agent 会话 spawn 一个工具服务器子进程（AGENT_SESSION=会话id），
工具侧可据此隔离各会话的输出与审计。
gateway 对工具服务器的全部认知都来自 MCP 协议本身，不 import 它的代码。

工具面两种呈现（agent.tool_surface 设置）：
  meta   仅 meta 工具（search_tools 发现 → run_tool 执行），上下文不随工具数增长
  native 全量底层工具直接进模型 tools 参数（类 Claude），一轮直达；
         执行仍统一转发 run_tool，校验/归一化/落盘/审计管道不变
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path


class ToolServer:
    """一次 MCP 会话的句柄：OpenAI 格式工具表 + call()。"""

    def __init__(self, session, listed: list):
        self._session = session
        self.tools = [{
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                # mcp 2.0 字段是 snake_case（旧版 inputSchema 已改名）
                "parameters": getattr(t, "input_schema", None)
                              or {"type": "object", "properties": {}},
            },
        } for t in listed]
        self._names = {t.name for t in listed}
        self._meta_tools = {t.name: t for t in listed}
        self._native_cache: list | None = None
        self._cards: dict | None = None

    async def cards(self) -> dict:
        """全量工具卡（name → card），dump 一次缓存。"""
        if self._cards is None:
            try:
                raw = await self.call("dump_tools", {})
                self._cards = {c["name"]: c for c in json.loads(raw).get("tools", [])}
            except Exception:
                self._cards = {}
        return self._cards

    async def danger_of(self, name: str, params: dict) -> str:
        """调用的副作用等级（tool.yaml 的 danger：readonly / intrusive …）。
        meta 面经 run_tool 转发，真实工具名在 params.tool_name 里；
        捆绑工具（如 ida）再往下看一层 op，逐操作判读/写——防兜圈不失真。"""
        args = params
        if name == "run_tool":
            name, args = str(params.get("tool_name", "")), params.get("params") or {}
        if name in ("search_tools", "read_output", "list_categories", "dump_tools",
                    "describe_tool"):
            return "readonly"
        card = (await self.cards()).get(name, {})
        return str(card.get("op_danger", {}).get(str(args.get("op") or ""))
                   or card.get("danger", ""))

    async def native_tools(self) -> list:
        """大模型直连工具面：dump_tools 全量工具卡 → OpenAI tools 格式。

        只 dump 一次缓存——工具表内容稳定，前缀缓存才命中得起来。
        read_output 不在 dump 里（它是 meta 工具），钻取大输出仍需要，从
        meta 面补进来。
        """
        if self._native_cache is None:
            cards = list((await self.cards()).values())
            table = []
            for card in cards:
                desc = card.get("description", "")
                tags = "/".join(x for x in (card.get("category"),
                                            card.get("danger")) if x)
                if tags:
                    desc = f"[{tags}] {desc}"
                if card.get("usage"):             # 用法要点（tool.yaml search_text），封顶防撑爆工具表
                    # 4000：普通工具 usage 都很短；捆绑工具（ida 操作表 ~3.4k）是唯一合法大户
                    desc += f"\n用法: {card['usage'][:4000]}"
                if card.get("example"):
                    desc += f'\n示例: {json.dumps(card["example"], ensure_ascii=False)}'
                table.append({"type": "function", "function": {
                    "name": card["name"], "description": desc,
                    "parameters": card.get("params_schema")
                                  or {"type": "object", "properties": {}},
                }})
            for meta in ("read_output",):   # 直连面也需要钻取大输出
                t = self._meta_tools.get(meta)
                if t:
                    table.append(next(x for x in self.tools
                                      if x["function"]["name"] == meta))
            self._native_cache = table
        return self._native_cache

    async def call(self, name: str, arguments: dict) -> str:
        """调用工具，返回文本。未知工具返回结构化错误文本让模型自纠错。"""
        if name not in self._names:
            return json.dumps({"status": "unknown_tool", "tool": name,
                               "hint": "工具不在当前工具表中"}, ensure_ascii=False)
        result = await self._session.call_tool(name, arguments)
        parts = [c.text for c in result.content if hasattr(c, "text")]
        return "\n".join(parts) or "(空输出)"

    async def call_surface(self, name: str, arguments: dict, surface: str) -> str:
        """按工具面执行模型发起的调用。

        native 面下模型直接调原生工具名：转发进 run_tool，
        校验/归一化/落盘/审计全部保留；meta 工具（read_output 等）直调。
        """
        if surface == "native" and name not in self._meta_tools:
            return await self.call("run_tool",
                                   {"tool_name": name, "params": arguments})
        return await self.call(name, arguments)


@asynccontextmanager
async def open_tools(tools_cfg: dict, session_id: str, toolset: str | None = None,
                     extra_env: dict | None = None):
    """拉起工具服务器子进程并完成握手，yield 一个 ToolServer 句柄。

    两个坑（都踩过）：
    - stdio_client 的 env 是整体替换：必须 {**os.environ, ...} 继承完整环境，
      否则子进程丢 PATH，工具服务器里 shutil.which 找不到外部程序
    - stdio_client / ClientSession 必须在一条 async with 链里进出；
      手动 __aenter__/__aexit__ 在异常路径会跨任务清理 anyio cancel scope，
      直接把进程变僵尸（无输出、杀不掉的那种）
    """
    # 惰性导入：不接工具服务器时无需安装 mcp 包
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "AGENT_SESSION": session_id, "PYTHONIOENCODING": "utf-8",
           # skills 只扫项目本地目录，不继承用户全局 ~/.claude/skills：
           # 本项目是通用智能体，机器上的全局（尤其是安全类）技能不进工具面
           "AGENT_SKILL_DIRS": str(Path(__file__).resolve().parent.parent
                                   / ".claude" / "skills")}
    if toolset is not None:                 # 工具集预设 → toolbox 侧过滤
        import toolsets
        env.update(toolsets.env_for(toolset))
    env.update(tools_cfg.get("extra_env") or {})
    env.update(extra_env or {})
    params = StdioServerParameters(command=tools_cfg["command"],
                                   args=list(tools_cfg.get("args", [])), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=
                float(tools_cfg.get("request_timeout_s", 930))) as session:
            # 在持有 anyio scope 的原任务内限时，不用 wait_for 创建子任务。
            import asyncio
            async with asyncio.timeout(float(tools_cfg.get("init_timeout_s", 30))):
                await session.initialize()
                listed = (await session.list_tools()).tools
            yield ToolServer(session, listed)
