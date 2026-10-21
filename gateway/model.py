"""模型适配器（OpenAI 兼容）。默认接自建 llama.cpp/GLM-4.7 Flash。

换 DeepSeek/Qwen/vLLM/Ollama 只改 config.yaml。

achat() 是异步主路径：httpx.AsyncClient 让 task.cancel() 能真正打断在途请求
（旧版 to_thread + 同步 httpx 被取消后线程仍会跑完整个请求，最长 timeout_s）。
stream=True（profile 配置，默认开）时逐 token 回调 on_delta，前端实时显示；
端点不支持流式（非 SSE 响应 / 解析失败且未产出任何内容）自动退回非流式。
流停滞检测：有效输出间隔超过 profile 的 stream_read_s（默认 120s）判死流，
SSE 心跳不续期；timeout_s 限制单次请求总时长，finish 后短暂收尾 usage。
超时整轮重发一次（重试前 on_delta("reset") 让前端清掉半截输出）。
chat() 保留同步版本，供脚本与旧测试使用。
"""
import asyncio
import json
import logging
import time

import httpx


class ChatResult:
    """一轮模型输出的归一化。reasoning 单独存放，回填历史时丢弃。"""

    def __init__(self, raw_msg: dict, usage: dict, finish_reason: str):
        self.content = raw_msg.get("content") or ""
        self.reasoning = raw_msg.get("reasoning_content") or ""
        self.tool_calls = raw_msg.get("tool_calls") or []
        self.usage = usage or {}    # prompt/completion/total（含 cached_tokens 若有）
        self.finish_reason = finish_reason or ""

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


class _StreamUnsupported(Exception):
    """端点没按 SSE 返回：退回非流式重发。"""


class _RequestDeadline(httpx.ReadTimeout):
    """总预算耗尽，不在同一 profile 重发。"""


def _payload(profile: dict, messages: list, tools: list, overrides: dict | None) -> dict:
    payload = {
        "model": profile["model"],
        "messages": messages,
        "max_tokens": profile.get("max_tokens", 4000),
        "temperature": profile.get("temperature", 0.6),
    }
    if tools:                       # 空工具表不传：部分服务端对 tools=[] 报 400
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    for k, v in (overrides or {}).items():
        if v not in (None, ""):
            payload[k] = v
    return payload


def _headers(profile: dict) -> dict:
    return {"Authorization": f"Bearer {profile['api_key']}"} if profile.get("api_key") else {}


def _url(profile: dict) -> str:
    return f"{profile['base_url'].rstrip('/')}/chat/completions"


def _merge_tool_delta(acc: list, deltas: list) -> None:
    """流式 tool_calls 按 index 分片到达：id/name 首片给出，arguments 逐片拼接。"""
    for d in deltas:
        i = d.get("index", len(acc))
        while len(acc) <= i:
            acc.append({"id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
        slot = acc[i]
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


class OpenAICompatModel:
    transport = None            # 测试注入 httpx.MockTransport

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.profiles = [cfg, *cfg.get("fallbacks", [])]

    # ── 异步主路径 ──
    async def achat(self, messages: list, tools: list, on_delta=None,
                    overrides: dict | None = None) -> ChatResult:
        """按 profile 顺序故障转移；每个 profile 内部传输错误/5xx 重试 3 次。
        流式读超时（流停滞：连接活着但不再来 chunk）整轮重试一次，通常即恢复；
        非流式超时多半是生成超预算，重试只会加倍等待，维持不重试。"""
        last_error = None
        for index, profile in enumerate(self.profiles):
            if index and on_delta:
                on_delta("reset", "")
            try:
                return await self._achat_profile(profile, messages, tools,
                                                 on_delta, overrides)
            except (RuntimeError, httpx.TimeoutException) as e:
                last_error = e
        raise RuntimeError(f"all configured model profiles failed: {last_error}")

    async def _achat_profile(self, profile, messages, tools, on_delta, overrides):
        stream = bool(profile.get("stream", True)) and on_delta is not None
        payload = _payload(profile, messages, tools, overrides)
        timeout = httpx.Timeout(profile.get("timeout_s", 300), connect=15)
        last = None
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            for attempt in range(3):
                if attempt and on_delta:
                    on_delta("reset", "")
                try:
                    try:
                        async with asyncio.timeout(float(profile.get("timeout_s", 300))):
                            if stream:
                                try:
                                    return await self._stream(client, profile, payload, on_delta)
                                except _StreamUnsupported:
                                    stream = False
                                    continue
                            r = await client.post(_url(profile), headers=_headers(profile),
                                                  json=payload)
                    except TimeoutError as exc:
                        raise _RequestDeadline(
                            f"模型请求超过总时限 {profile.get('timeout_s', 300)}s") from exc
                except httpx.TimeoutException as exc:
                    # 流式读超时=流停滞（服务端不再发数据，高负载 API 常见）：
                    # 整轮重发一次通常即恢复；非流式超时=生成超预算，
                    # 重试只会加倍等待，维持直接抛出
                    if stream and attempt == 0 and not isinstance(exc, _RequestDeadline):
                        logging.getLogger(__name__).warning("模型流停滞，重发一次: %s", exc)
                        await asyncio.sleep(1.5)
                        continue
                    if not str(exc):
                        raise httpx.ReadTimeout(f"模型请求超时（{type(exc).__name__}）") from exc
                    raise
                except httpx.TransportError as e:
                    if attempt == 2:
                        raise RuntimeError(f"模型服务连接失败（已重试3次）: {e}") from e
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                except _RetryableStatus as e:
                    last = e
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code >= 500:
                    last = _RetryableStatus(r.status_code, r.text)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"模型服务 {r.status_code}：{r.text[:300]}")
                data = r.json()
                choice = data["choices"][0]
                return ChatResult(choice["message"], data.get("usage", {}),
                                  choice.get("finish_reason", ""))
        raise RuntimeError(f"模型服务连续失败：{last}")

    async def _stream(self, client, profile, payload, on_delta) -> ChatResult:
        body = {**payload, "stream": True,
                "stream_options": {"include_usage": True}}
        content, reasoning, calls = [], [], []
        usage, finish = {}, ""
        completed = False
        # 流停滞检测要跟整答预算分开：read 超时按"相邻 chunk 间隔"计，
        # 沿用 timeout_s（600s 级）会把断流挂满十分钟才报错
        timeout = httpx.Timeout(profile.get("timeout_s", 300), connect=15,
                                read=profile.get("stream_read_s", 120))
        async with client.stream("POST", _url(profile), headers=_headers(profile),
                                 json=body, timeout=timeout) as r:
            if r.status_code >= 500:
                raise _RetryableStatus(r.status_code, (await r.aread()).decode("utf-8", "replace"))
            if r.status_code >= 400:
                text = (await r.aread()).decode("utf-8", "replace")
                # 有的服务端不认 stream_options：退回非流式最稳
                if "stream" in text.lower():
                    raise _StreamUnsupported()
                raise RuntimeError(f"模型服务 {r.status_code}：{text[:300]}")
            if "text/event-stream" not in r.headers.get("content-type", ""):
                raise _StreamUnsupported()
            loop = asyncio.get_running_loop()
            idle_s = float(profile.get("stream_read_s", 120))
            deadline = loop.time() + idle_s
            lines = r.aiter_lines().__aiter__()
            while True:
                try:
                    # 同一个绝对截止时间跨越心跳、空 delta、坏 JSON 和 usage 包。
                    async with asyncio.timeout_at(deadline):
                        line = await lines.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    if completed:
                        break  # finish 已完成，usage 收尾期限已到，不重发完整答案。
                    raise httpx.ReadTimeout(
                        f"模型流 {idle_s:g}s 无有效输出（心跳不计进展）") from exc
                except httpx.TransportError:
                    if completed:
                        break
                    raise
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    completed = True
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                progressed = False
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("reasoning_content"):
                        progressed = True
                        reasoning.append(delta["reasoning_content"])
                        on_delta("reasoning", delta["reasoning_content"])
                    if delta.get("content"):
                        progressed = True
                        content.append(delta["content"])
                        on_delta("content", delta["content"])
                    if delta.get("tool_calls"):
                        progressed |= any(d.get("id") or (d.get("function") or {}).get("name")
                                          or (d.get("function") or {}).get("arguments")
                                          for d in delta["tool_calls"])
                        _merge_tool_delta(calls, delta["tool_calls"])
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                        if not completed:
                            completed = True
                            deadline = loop.time() + min(1.0, idle_s)
                if progressed and not completed:
                    deadline = loop.time() + idle_s
        if not completed:
            raise httpx.ReadError("模型流在完成标记前断开")
        for i, c in enumerate(calls):          # 个别服务端流式不给 id
            c["id"] = c["id"] or f"call_{i}_{int(time.time() * 1000)}"
        return ChatResult({"content": "".join(content),
                           "reasoning_content": "".join(reasoning),
                           "tool_calls": [c for c in calls if c["function"]["name"]]},
                          usage, finish)

    # ── 同步版本（脚本/旧测试） ──
    def chat(self, messages: list, tools: list) -> ChatResult:
        return asyncio.run(self.achat(messages, tools))


class _RetryableStatus(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(f"{code}: {text[:300]}")
        self.status_code = code
