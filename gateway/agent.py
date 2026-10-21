"""自研 agent 循环 —— 智能体框架的大脑躯干。

    模型 → tool_calls(一轮可多个，全执行) → 工具服务器(MCP，可选) → 结果回填 → 直到无调用

上下文纪律（三层记录设计）：
  - 模型记忆持久化 sessions/<sid>/context.json：新消息追加而非重开，跨消息延续
  - 普通结束/取消保留完整历史；手动压缩时才生成交接摘要
  - 连续失败 / 连续无进展 → 截断回失败起点，留"墓碑"防重蹈覆辙
    （每任务最多回滚 2 次，超限退化为提示注入）；回滚不会吞掉用户插话
  - 框架注入的 user 消息带 _sys 标记（tombstone/nudge/interject），不算任务边界，
    发给模型前由 context_store.to_wire 剥掉
  - 中途插话（Claude Code 式）：任务运行中用户再发消息进 inbox，下一轮模型调用前
    注入对话；模型已给出最终回答但 inbox 非空时不收尾，继续响应插话
  - 流式：模型支持时逐 token 推 assistant_delta（不落盘的 live 事件）
工具面两档（agent.tool_surface）：meta=search→run 两段式 / native=全量工具直进 tools
"""
import asyncio
import contextlib
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import context_store
from context_reads import FileReadCache
from events import RESULT_PREVIEW_CHARS
from model import OpenAICompatModel

# meta 面（两段式）：工具经 search_tools 发现、run_tool 执行
# 知识库查阅纪律（meta/native 共用）：分区会随项目增长，不在这里枚举分区——
# 模型自己 collections() 发现，具体打法钩子挂在各工具卡 search_text 里
KNOWLEDGE_DISCIPLINE = (
    "本地知识库纪律：遇到领域方法论/实战经验/工具打法类问题，先检索 rag 工具的"
    "知识库再作答——里面有历史沉淀的打法与踩坑记录；知识按项目分区(collection)，"
    "不确定有什么分区就先查分区清单或留空跨区搜，命中证据卡后按 source 路径读"
    "原文全文，不要只凭片段和记忆下结论。"
    "动手任务遇阻(报错/闪退/被反制)时，同样先搜知识库找同类问题的已知打法再换路线。"
)

SYSTEM_PROMPT = (
    "你是一个通用智能体助手。需要动手操作时先用 search_tools 搜索可用工具，"
    "再按返回的工具卡用 run_tool 执行；参数不符会有明确报错，改正后重试。"
    "大输出被截断时用 read_output 分页查看全量。"
    "任务完成就直接给出最终回答，不再调用工具。"
    "你有迭代预算上限，接近上限时基于已有信息总结作答，不要继续探索。"
    "对话会延续此前的记忆，可直接引用之前的结论，无需重复操作。"
    "用户可能在你执行过程中插话（标记为[用户插话]），插话优先级高于原计划，据此调整。"
    + KNOWLEDGE_DISCIPLINE
)

# native 面（大模型直连）：全部工具已在工具表中，无需 search→run 两段式
NATIVE_SYSTEM_PROMPT = (
    "你是一个通用智能体助手。全部工具已直接列在工具表中，按 schema 直接调用即可，"
    "只使用工具表中列出的工具、直接用原生工具名，不要用 run_tool/search_tools "
    "之类的包装调用。参数不符会有明确报错；"
    "输出过大被截断时用 read_output 分页查看全量。"
    "任务完成就直接给出最终回答，不再调用工具。"
    "你有迭代预算上限，接近上限时基于已有信息总结作答，不要继续探索。"
    "对话会延续此前的记忆，可直接引用之前的结论，无需重复操作。"
    "用户可能在你执行过程中插话（标记为[用户插话]），插话优先级高于原计划，据此调整。"
    + KNOWLEDGE_DISCIPLINE
)

# 失败判定特征：空结果 + 各类错误状态（工具按 {status:...} 约定返回时生效）
ERROR_MARKS = ('"status":"validation_error"', '"status":"error"',
               '"status":"unavailable"', '"status":"unknown_tool"',
               '"status":"not_found"', '"status":"timeout"')
FAIL_STREAK_LIMIT = 3         # 连续失败 N 次触发回滚
MAX_ROLLBACKS = 2             # 每任务最多回滚次数
NO_PROGRESS_STREAK_LIMIT = 2  # 同一成功调用重复 N 次后触发回滚
REPEAT_CALL_LIMIT = 2         # 同名同参调用在一个任务里最多真实执行 N 次（不论读写，兜底防兜圈）
EMPTY_ANSWER_RETRIES = 1      # 空正文（思考链耗尽输出预算）时的催答次数
TOOL_TEXT_RETRIES = 1
DELTA_FLUSH_S = 0.08          # 流式增量推送节流
DELTA_FLUSH_CHARS = 80


def _parse_arguments(raw) -> dict:
    try:
        args = json.loads(raw or "{}")
        return args if isinstance(args, dict) else {"_error": "arguments 不是对象"}
    except json.JSONDecodeError as e:
        return {"_error": f"arguments JSON 解析失败: {e}"}


def _parse_text_tool_request(content: str, tools: list) -> dict | None:
    """Recognize only a complete request for an available tool."""
    text = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1)
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    names = {t.get("function", {}).get("name") for t in tools}
    if (isinstance(value, dict) and set(value) <= {"name", "arguments"}
            and isinstance(value.get("name"), str)
            and value.get("name") in names
            and isinstance(value.get("arguments"), (dict, str))):
        return value
    return None


def _tool_request_in_text(content: str, tools: list) -> bool:
    return _parse_text_tool_request(content, tools) is not None


def _recover_text_tool_call(content: str, tools: list) -> dict:
    """Opt-in compatibility still uses the registered schema and normal gateway."""
    import jsonschema
    request = _parse_text_tool_request(content, tools)
    if request is None:
        raise ValueError("未识别到可用工具的完整调用 JSON")
    params = request["arguments"]
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ValueError("正文工具调用的 arguments 不是合法 JSON") from exc
    if not isinstance(params, dict):
        raise ValueError("正文工具调用的 arguments 必须是对象")
    tool = next(t["function"] for t in tools if t["function"]["name"] == request["name"])
    try:
        jsonschema.validate(params, tool.get("parameters") or {"type": "object"})
    except jsonschema.ValidationError as exc:
        raise ValueError(f"正文工具调用参数无效: {exc.message}") from exc
    return {"id": "compat_" + uuid.uuid4().hex[:16], "type": "function",
            "function": {"name": request["name"],
                         "arguments": json.dumps(params, ensure_ascii=False)}}


def _is_failure(result: str) -> bool:
    """协议信封优先：status 非 ok 即失败；非 JSON 才退回长度启发式。"""
    try:
        value = json.loads(result)
        if isinstance(value, dict) and "status" in value:
            return str(value["status"]) not in ("ok", "no_progress")
    except (json.JSONDecodeError, TypeError):
        pass
    return len(result) < 60 or any(m in result for m in ERROR_MARKS)


def _call_key(name: str, params: dict) -> str:
    """Stable identity for an attempted tool call within one task."""
    return f"{name}:{json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


def _no_progress_result(name: str, params: dict) -> str:
    """Return a protocol-valid tool result without rerunning a duplicate call."""
    return json.dumps({
        "status": "no_progress",
        "tool": name,
        "params": params,
        "detail": "Identical successful call already ran in this task; no new execution was performed.",
        "hint": "Use the existing result, choose different parameters, or answer now.",
    }, ensure_ascii=False)


def _repeat_failure_result(name: str, params: dict, runs: int) -> str:
    """同名同参且从未成功过的调用触顶 REPEAT_CALL_LIMIT：不能叫模型"用已有结果"
    （它没有结果），要明确禁止原样重试并给出换路方向。"""
    return json.dumps({
        "status": "no_progress",
        "tool": name,
        "params": params,
        "detail": f"同一调用已执行 {runs} 次且从未成功，不再执行。",
        "hint": "禁止原样重试：换 op 或改参数；先 describe_tool 看该工具的操作表；或基于已有信息作答。",
    }, ensure_ascii=False)


def _evidence_key(result: str) -> str:
    """Normalize volatile call metadata before comparing tool evidence."""
    try:
        value = json.loads(result)
        if isinstance(value, dict):
            for key in ("call_id", "full_output", "duration_ms", "output_path"):
                value.pop(key, None)
            if isinstance(value.get("stats"), dict):
                value["stats"] = {k: v for k, v in value["stats"].items()
                                  if k != "duration_ms"}
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        payload = result
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _task_state(task: str, history: list, iteration: int, max_iterations: int) -> str:
    """Compact planner state, rebuilt from durable history each model round."""
    facts, attempts = [], []
    goal = task
    continuation = r'(?:继续(?:执行|分析|汇总|整理|解题|汇报)?|汇报(?:目前)?(?:结果|进展)|(?:目前|现在)(?:什么情况|分析现状)|分析现状)[。！!\s]*'
    if re.fullmatch(continuation, task.strip()):
        goal = next((m['content'] for m in reversed(history)
                     if context_store.is_task_start(m) and m.get('content')
                     and not re.fullmatch(continuation, m['content'].strip())), task)
    for message in history:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            attempts.extend(tc["function"]["name"] for tc in message["tool_calls"])
        elif message.get("role") == "tool":
            try:
                value = json.loads(message.get("content", ""))
                summary = value.get("summary") if isinstance(value, dict) else ""
                if summary:
                    facts.append(str(summary)[:240])
            except json.JSONDecodeError:
                pass
    return ("\n\n[task state]\n"
            f"goal: {goal[:300]}\n"
            f"current request: {task[:300]}\n"
            f"iteration: {iteration}/{max_iterations}\n"
            f"confirmed evidence: {facts[-4:] or ['none']}\n"
            f"attempted tools: {attempts[-8:] or ['none']}\n"
            "completion rule: answer when the evidence resolves the goal; otherwise request only an action that can add distinct evidence.")


def _tombstone(history: list, ckpt: int, reason: str = "failed") -> str:
    """回滚墓碑：记下试过什么、为何失败，防止模型重蹈覆辙。"""
    tried = []
    for m in history[ckpt:]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tried += [tc["function"]["name"] for tc in m["tool_calls"]]
    fails = [str(m.get("content", ""))[:80] for m in history[ckpt:]
             if m.get("role") == "tool"][:3]
    label = "连续失败" if reason == "failed" else "连续无新进展"
    return (f"[系统提示] 刚才的操作路径{label}，已回滚。"
            f"已尝试: {tried}；结果摘要: {' | '.join(fails)}。"
            "请更换工具或参数思路，或基于已有信息直接作答，不要重复相同调用。")


def _preview(result: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    """给前端的结果预览。只截 output 字段，信封保持合法 JSON——
    直接切字符串会切断 JSON，前端解析失败就什么都显示不出来。"""
    if len(result) <= limit:
        return result
    try:
        env = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result[:limit]
    if not isinstance(env, dict):
        return result[:limit]
    # 大输出信封是 output_head(+output_tail)：同样只截文本字段
    fields = [k for k in ("output", "output_head", "output_tail")
              if isinstance(env.get(k), str)]
    if fields:
        head = json.dumps({**env, **{k: "" for k in fields}}, ensure_ascii=False)
        room = max(200, limit - len(head))
        env = dict(env)
        for k in fields:
            if len(env[k]) > room:
                env[k] = env[k][:room]
                env["preview_truncated"] = True
            room = max(0, room - len(env[k]))
        env = {k: v for k, v in env.items() if v != "" or k not in fields}
    return json.dumps(env, ensure_ascii=False)


def _extract_call_id(result: str) -> str:
    try:
        return json.loads(result).get("call_id", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return ""


def sys_msg(content: str, kind: str) -> dict:
    """框架注入的 user 消息：_sys 标记让它不被当成任务边界。"""
    return {"role": "user", "content": content, "_sys": kind}


@dataclass
class LoopGuard:
    """单任务内的防兜圈状态：失败连击、无进展连击、回滚额度、已见证据。"""
    fail_streak: int = 0
    fail_ckpt: int | None = None
    np_streak: int = 0
    np_ckpt: int | None = None
    rollbacks: int = 0
    successful: set = field(default_factory=set)
    evidence: set = field(default_factory=set)
    # 调用次数不随 invalidate 清零：danger 未知的外部工具（按写处理）每次都会清空
    # successful/evidence，只靠它们永远判不出"同一调用反复执行"
    runs: dict = field(default_factory=dict)

    def mark(self, *, failed: bool, no_progress: bool, ckpt: int) -> None:
        if failed:
            self.fail_streak += 1
            self.fail_ckpt = ckpt if self.fail_ckpt is None else self.fail_ckpt
        else:
            self.fail_streak, self.fail_ckpt = 0, None
        if no_progress:
            self.np_streak += 1
            self.np_ckpt = ckpt if self.np_ckpt is None else self.np_ckpt
        elif not failed:
            self.np_streak, self.np_ckpt = 0, None

    def invalidate(self) -> None:
        """有副作用的调用成功后，外部状态可能变了：之前"读过"的不再算重复
        （否则 读→改→再读 会把第二次读判成无进展，模型拿不到新内容）。"""
        self.successful.clear()
        self.evidence.clear()


def rollback(history: list, ckpt: int, note: str) -> None:
    """截断回检查点并留墓碑；检查点之后的用户插话原样保留，不能被回滚吞掉。"""
    kept = [m for m in history[ckpt:] if m.get("_sys") == "interject"]
    del history[ckpt:]
    history.extend(kept)
    history.append(sys_msg(note, "tombstone"))


class _DeltaPump:
    """流式增量节流器：攒到一定字数或时间再推一条 live 事件，避免 WS 被刷爆。"""

    def __init__(self, bus, iteration: int):
        self.bus, self.iteration = bus, iteration
        self.buf = {"content": [], "reasoning": []}
        self.size, self.last = 0, time.monotonic()

    def __call__(self, kind: str, text: str) -> None:
        if kind == "reset":          # 传输重试：前端清空本轮已显示的增量
            self.buf = {"content": [], "reasoning": []}
            self.size = 0
            self._emit(reset=True)
            return
        self.buf[kind].append(text)
        self.size += len(text)
        if self.size >= DELTA_FLUSH_CHARS or time.monotonic() - self.last >= DELTA_FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if self.size:
            self._emit(content="".join(self.buf["content"]),
                       reasoning="".join(self.buf["reasoning"]))
            self.buf = {"content": [], "reasoning": []}
            self.size = 0
        self.last = time.monotonic()

    def _emit(self, **fields) -> None:
        live = getattr(self.bus, "emit_live", None)
        if live:
            live("assistant_delta", iteration=self.iteration, **fields)


class _NoTools:
    """未配置工具服务器时的空工具面：保持循环代码路径统一。"""

    tools: list = []

    async def native_tools(self) -> list:
        return []

    async def danger_of(self, name: str, params: dict) -> str:
        return ""

    async def call_surface(self, name: str, arguments: dict, surface: str) -> str:
        return await self.call(name, arguments)

    async def call(self, name: str, arguments: dict) -> str:
        return json.dumps({"status": "unknown_tool", "tool": name,
                           "hint": "当前未配置工具服务器"}, ensure_ascii=False)


def build_system(cfg: dict, session_id: str, extra_system: str = "") -> str:
    """内置默认 + 用户自定义（追加，不替换——替换会丢掉工具使用指引）+ 预设。"""
    tools_cfg = cfg.get("tools", {}) or {}
    surface = cfg["agent"].get("tool_surface", "native")
    system = NATIVE_SYSTEM_PROMPT if surface == "native" else SYSTEM_PROMPT
    custom = (cfg["agent"].get("system_prompt") or "").strip()
    if custom:
        system += f"\n\n[用户自定义指令]\n{custom}"
    if tools_cfg.get("command") and tools_cfg.get("workspace"):   # 接了工具才注入
        ws_dir = Path(tools_cfg["workspace"]) / session_id / "files"
        system += (f"\n\n[运行环境] Windows。会话工作区: {ws_dir}"
                   "\n文件工具（file_read/file_write/file_list/file_edit）的相对路径"
                   "一律落在会话工作区，写入成功会返回绝对路径；"
                   "路径必须带盘符（如 F:/x）才算绝对路径。")
    if extra_system:
        system += f"\n\n[附加指令]\n{extra_system}"
    return system


async def _model_round(model, msgs: list, tools: list, pump, overrides=None):
    """异步模型优先（可流式、可被 cancel 打断）；只有同步 chat 的替身走线程。"""
    if hasattr(model, "achat"):
        return await model.achat(msgs, tools, on_delta=pump, overrides=overrides)
    return await asyncio.to_thread(model.chat, msgs, tools)


def _drain(inbox: list | None, history: list, bus, iteration: int, consumed=None) -> int:
    """把排队的插话注入对话。返回注入条数。"""
    if not inbox:
        return 0
    n = 0
    while inbox:
        item = inbox.pop(0)
        if item.get('kind') == 'subagent':
            history.append({**sys_msg(item['content'], 'subagent_notice'), '_notice_id': item['id']})
            bus.emit('subagent_notice_applied', id=item['id'], iteration=iteration)
            if consumed is not None:
                consumed.append(item['id'])
        else:
            history.append(sys_msg(f"[用户插话] {item['content']}", "interject"))
            bus.emit("interject_applied", id=item["id"], iteration=iteration)
        n += 1
    return n


async def run_agent(session_id: str, task: str, cfg: dict,
                    bus, cancel: asyncio.Event,
                    extra_system: str = "", prompt_name: str = "",
                    inbox: list | None = None, tools_box=None,
                    persist: bool = True, emit_user: bool = True,
                    toolset: str | None = None, on_notifications_saved=None,
                    context_control: dict | None = None) -> str:
    """跑完一个任务，返回最终回答。事件经 bus 推送落盘；记忆入 context.json。

    extra_system：提示词预设，仅作用于本次任务（system 不入记忆）。
    inbox：插话队列（server 持有同一个 list 引用，运行中 append 即可）。
    tools_box：外部传入已打开的工具句柄（工作流复用同一个 MCP 子进程）。
    persist=False：不读写会话记忆（工作流里的 agent 节点是一次性上下文）。
    """
    model = OpenAICompatModel(cfg["model"])
    tools_cfg = cfg.get("tools", {}) or {}
    surface = cfg["agent"].get("tool_surface", "native")
    max_it = cfg["agent"]["max_iterations"]
    preset = None
    if toolset is not None and tools_cfg.get("command"):
        import toolsets
        preset = toolsets.get_preset(toolset)
        if preset.get("system"):              # 工具集自带的用法说明
            extra_system = "\n\n".join(x for x in (extra_system, preset["system"]) if x)
    if emit_user:
        bus.emit("user_message", content=task, prompt=prompt_name,
                 **({"toolset": preset["name"]} if preset and preset["id"] != "all" else {}))
    system = build_system(cfg, session_id, extra_system)

    # ── 记忆装载：延续 + 补漏 + 追加本任务 ──
    workspace = Path(tools_cfg['workspace']) if tools_cfg.get('workspace') else None
    history = context_store.load(session_id, workspace) if persist else []
    context_store.finalize_dangling(history)
    history.append({"role": "user", "content": task})
    # The budget is advisory. History compaction is explicitly requested by the
    # user; approaching the budget must not intercept normal requests.
    task_start = next(i for i in range(len(history) - 1, -1, -1)
                      if context_store.is_task_start(history[i]))

    guard = LoopGuard()
    read_cache = FileReadCache(workspace, session_id, history)
    empty_answers = 0
    tool_text_retries = 0
    last_answer = last_reasoning = ""
    consumed = []
    try:
        if tools_box is not None:
            box_ctx = contextlib.nullcontext(tools_box)
        elif tools_cfg.get("command") and not (preset and preset.get("tools") == []):
            # 惰性取模块属性：测试 patch mcp_client.open_tools 才生效
            import mcp_client
            box_ctx = mcp_client.open_tools(tools_cfg, session_id, toolset=toolset) \
                if toolset is not None else mcp_client.open_tools(tools_cfg, session_id)
        else:
            box_ctx = contextlib.nullcontext(_NoTools())
        async with box_ctx as box:
            async with asyncio.timeout(float(tools_cfg.get("init_timeout_s", 30))):
                import subagent
                box = await subagent.bind(box, cfg, session_id, bus, cancel)
                tools = await box.native_tools() if surface == "native" else box.tools
            i = 0
            while i < max_it:
                i += 1
                if cancel.is_set():
                    bus.emit("error", detail="已取消")
                    return ""
                _drain(inbox, history, bus, i, consumed)
                if persist:
                    context_store.publish_usage(bus, history, cfg, context_control)
                round_start = len(history)
                bus.emit("model_call", iteration=i, surface=surface)
                # native 面把每轮变化的规划器状态放对话末尾（临时消息，不落盘）：
                # system 与 tools 前缀恒定，prefix cache 才命中得住
                state = _task_state(task, history, i, max_it)
                wire = context_store.to_wire(history)
                if surface == "native":
                    msgs = [{"role": "system", "content": system}] + wire \
                        + [{"role": "user", "content": state}]
                else:
                    msgs = [{"role": "system", "content": system + state}] + wire
                pump = _DeltaPump(bus, i)
                try:
                    resp = await _model_round(model, msgs, tools, pump)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    bus.emit("error", detail=f"模型调用失败: {e}")
                    return ""
                pump.flush()

                tool_text = not resp.tool_calls and _tool_request_in_text(resp.content, tools)
                if tool_text and cfg["agent"].get("tool_call_compat", False):
                    try:
                        resp.tool_calls = [_recover_text_tool_call(resp.content, tools)]
                    except ValueError as exc:
                        bus.emit("error", detail=str(exc))
                        return ""
                    resp.content = ""
                    tool_text = False
                bus.emit("assistant_message", content=resp.content,
                         reasoning=resp.reasoning, iteration=i,
                         tokens=resp.total_tokens)
                if tool_text:
                    if tool_text_retries >= TOOL_TEXT_RETRIES or i >= max_it:
                        bus.emit("error", detail="模型把工具调用写进了回答正文，未通过 tool_calls 执行。"
                                 "请检查模型服务的工具调用兼容性。")
                        return ""
                    tool_text_retries += 1
                    history.append({"role": "assistant", "content": resp.content})
                    history.append(sys_msg(
                        "[系统提示] 上一段 JSON 只是正文，没有执行任何工具。"
                        "需要工具时请通过 API 的原生 tool_calls 字段调用工具，"
                        "不要在正文或代码块中模拟调用；无需工具时直接回答用户。", "nudge"))
                    continue
                if resp.content.strip():
                    last_answer = resp.content
                last_reasoning = resp.reasoning

                if not resp.tool_calls:
                    if resp.content.strip():
                        history.append({"role": "assistant", "content": resp.content})
                        # 模型想收尾，但用户这轮里插了话：不收尾，继续响应插话
                        if inbox:
                            continue
                        bus.emit("done", content=resp.content)
                        return resp.content
                    # 思考型模型把输出预算耗在思考链上：正文空、无调用。
                    # 催答一次；仍空则把思考进展交给用户，绝不发空 done
                    empty_answers += 1
                    if empty_answers <= EMPTY_ANSWER_RETRIES:
                        history.append(sys_msg(
                            "[系统提示] 上一轮你只输出了思考，没有给出回答，"
                            "也没有调用工具（可能输出长度限制截断了正文）。"
                            "请基于已有进展直接给出简洁的最终回答；"
                            "确需更多信息再调用工具。", "nudge"))
                        continue
                    progress = (resp.reasoning or "").strip()[-1500:]
                    if not progress:
                        bus.emit("error", detail="模型连续返回空回答，没有可用输出")
                        return ""
                    summary = "(模型未输出正文，以下为思考进展摘录)\n" + progress
                    history.append({"role": "assistant", "content": summary})
                    bus.emit("done", content=summary)
                    return summary
                empty_answers = 0

                history.append({"role": "assistant", "content": resp.content,
                                "tool_calls": resp.tool_calls})
                outcome = await _run_tool_calls(resp.tool_calls, box, surface, history,
                                                guard, bus, cancel, i, round_start,
                                                tool_timeout=float(tools_cfg.get("request_timeout_s", 930)),
                                                read_cache=read_cache)
                if outcome == "timed_out":
                    bus.emit("error", detail="工具请求超时，执行状态未确认，已停止任务；请检查工具后再继续")
                    return ""
                if outcome == "cancelled":
                    bus.emit("error", detail="已取消")
                    return ""

            # 达到迭代上限：把最后进展交给用户
            progress = last_answer.strip() or (last_reasoning or "").strip()[-1500:]
            summary = (f"【已达到最大迭代 {max_it}，任务未正式完结——以下为最后进展】\n\n"
                       + progress)
            bus.emit("done", content=summary)
            return summary
    except asyncio.CancelledError:
        # task.cancel() 硬取消：异步 httpx / MCP 调用在 await 点被打断
        bus.emit("error", detail="已取消")
        return ""
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        bus.emit("error", detail=f"智能体工具连接/执行失败: {detail}")
        return ""
    finally:
        # Completion/cancellation preserves full tool evidence for the next turn.
        if persist:
            context_store.finalize_dangling(history)
            context_store.save(session_id, history)
            context_store.publish_usage(bus, history, cfg, context_control, live=True)
            if on_notifications_saved and consumed:
                on_notifications_saved(consumed)


async def _run_tool_calls(calls, box, surface, history, guard: LoopGuard,
                          bus, cancel, i: int, round_start: int,
                          tool_timeout: float = 930, read_cache: FileReadCache | None = None) -> str:
    """执行一轮的全部 tool_calls。返回 ok / rolled / cancelled。"""
    for tc in calls:
        if cancel.is_set():
            return "cancelled"
        name = tc["function"]["name"]
        params = _parse_arguments(tc["function"].get("arguments"))
        bus.emit("tool_call", tool=name, params=params, iteration=i, tc_id=tc["id"])
        key = _call_key(name, params)
        no_progress = False
        timed_out = False
        read_request = read_cache.request(name, params) if read_cache is not None else None
        cached = read_cache.lookup(read_request) if read_request is not None else None
        if cached is not None:
            result, no_progress = cached, True
            guard.successful.add(key)
        elif read_request is None and key in guard.successful:
            result, no_progress = _no_progress_result(name, params), True
        elif read_request is None and guard.runs.get(key, 0) >= REPEAT_CALL_LIMIT:
            result, no_progress = _repeat_failure_result(name, params, guard.runs[key]), True
        else:
            guard.runs[key] = guard.runs.get(key, 0) + 1
            try:
                async with asyncio.timeout(tool_timeout):
                    result = await box.call_surface(name, params, surface)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                timed_out = True
                result = json.dumps({"status": "error", "detail":
                    f"工具 {name} 超过请求时限 {tool_timeout:g}s，执行状态未确认；不自动重发"},
                    ensure_ascii=False)
            except Exception as e:
                # MCP 请求超时也可能发生在框架看门狗之前。
                timed_out = "timeout" in str(e).lower() or "timed out" in str(e).lower()
                result = json.dumps({"status": "error", "detail": f"工具调用异常: {e}"},
                                    ensure_ascii=False)
            if not _is_failure(result):
                danger = ""
                if hasattr(box, "danger_of"):
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(30):
                            danger = await box.danger_of(name, params)
                if danger and danger != "readonly":
                    guard.invalidate()          # 写/执行类：之前的读结果作废
                else:
                    guard.successful.add(key)
                    ek = _evidence_key(result)
                    if read_request is None and ek in guard.evidence:
                        result, no_progress = _no_progress_result(name, params), True
                    guard.evidence.add(ek)
        failed = _is_failure(result)
        guard.mark(failed=failed, no_progress=no_progress, ckpt=round_start)

        if not timed_out and guard.fail_streak >= FAIL_STREAK_LIMIT:
            if guard.rollbacks < MAX_ROLLBACKS:
                _do_rollback(history, guard, guard.fail_ckpt, "failed", bus, i)
                return "rolled"
            result += "\n[框架提示] 已连续多次失败。请更换思路，或基于已有信息给出最终回答。"

        bus.emit("tool_result", tool=name, call_id=_extract_call_id(result),
                 result=_preview(result), iteration=i, tc_id=tc["id"])
        message = {"role": "tool", "tool_call_id": tc["id"], "content": result}
        snapshot = read_cache.remember(read_request, result) if read_cache is not None else None
        if snapshot is not None:
            message['_file_read'] = snapshot
        history.append(message)
        if timed_out:
            return "timed_out"

        if guard.np_streak >= NO_PROGRESS_STREAK_LIMIT:
            if guard.rollbacks < MAX_ROLLBACKS:
                _do_rollback(history, guard, guard.np_ckpt, "no_progress", bus, i)
                return "rolled"
            history[-1]["content"] += ("\n[framework] Repeated calls produced no new "
                                       "information. Answer from the evidence already available.")
    return "ok"


def _do_rollback(history, guard: LoopGuard, ckpt: int, reason: str, bus, i: int) -> None:
    guard.rollbacks += 1
    note = _tombstone(history, ckpt, reason)
    rollback(history, ckpt, note)
    bus.emit("rollback", to_iteration=i, note=note[:200])
    guard.fail_streak = guard.np_streak = 0
    guard.fail_ckpt = guard.np_ckpt = None
