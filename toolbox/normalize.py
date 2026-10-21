"""输出归一化管道 —— 本项目的灵魂。

工具原始输出不直接进入模型上下文：
  - 始终全量落盘（outputs/<call_id>.txt），供 read_output 钻取与 viewer 回放
  - 估算 token 超过 OUTPUT_TOKEN_BUDGET → 只返回 头部+尾部+统计+call_id
  - 未超预算 → 原样返回（附统计）

OUTPUT_TOKEN_BUDGET 是触发行级截断的估算阈值；头尾按行保留，非严格 token 上限。
"""
import re

OUTPUT_TOKEN_BUDGET = 8000   # 输出面预算（估算 token）
HEAD_LINES = 200
TAIL_LINES = 80
MAX_LINE_CHARS = 2000        # 截断信封里单行上限（与 server.read_output 一致）；
                             # 不设则"一行几 MB"的压缩 HTML/JSON 会绕过行级预算撑爆上下文
SUMMARY_MAX_CHARS = 200

_CJK = re.compile(r"[一-鿿぀-ヿ]")


def _clip_lines(lines: list) -> list:
    """逐行限长：单行超 MAX_LINE_CHARS 截断并标注。"""
    return [(l[:MAX_LINE_CHARS] + " …[行已截断]") if len(l) > MAX_LINE_CHARS else l
            for l in lines]


def estimate_tokens(text: str) -> int:
    """粗估：CJK 约 1 token/字，其余约 4 字符/token。ContextMeter 够用，标 est。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) // 4


def summarize(raw: str) -> str:
    """P0 启发式摘要：前几行非空行拼接。工具定制摘要以后在 tool.yaml 扩展 summarizer 字段。"""
    picked, total = [], 0
    for line in (l.strip() for l in raw.splitlines()):
        if not line:
            continue
        if total + len(line) > SUMMARY_MAX_CHARS or len(picked) >= 5:
            # 首行就超长（单行 JSON 很常见）：截断收下，别让摘要整个为空——
            # gateway 的 [task state] 靠摘要告诉模型"已拿到什么证据"
            if not picked:
                picked.append(line[:SUMMARY_MAX_CHARS])
            break
        picked.append(line)
        total += len(line)
    return " | ".join(picked)[:SUMMARY_MAX_CHARS]


def normalize(raw: str, call_id: str, output_path: str, duration_ms: int,
              status: str = "ok", tool: str = "", exit_code: int | None = None) -> dict:
    """统一结果信封（所有工具同形）：
      status     ok / error / timeout / validation_error / unavailable / unknown_tool
      tool       工具名
      call_id    read_output 钻取用
      exit_code  非 0 退出时才出现（旧格式 "error(exit=2)" 塞在 status 里，难以判等）
      summary / stats / output 或 output_head+output_tail+hint
    落盘路径不再返回给模型（本机绝对路径对模型没用，还泄露目录结构），只在审计里记。"""
    est = estimate_tokens(raw)
    n_lines = raw.count("\n") + 1 if raw else 0
    result = {"status": status}
    if tool:
        result["tool"] = tool
    result["call_id"] = call_id
    if exit_code not in (None, 0):
        result["exit_code"] = exit_code
    result["summary"] = summarize(raw)
    result["stats"] = {"lines": n_lines, "tokens_est": est, "duration_ms": duration_ms,
                       "truncated": est > OUTPUT_TOKEN_BUDGET}
    del output_path   # 仅保留参数兼容；路径由调用方写审计
    if est <= OUTPUT_TOKEN_BUDGET:
        result["output"] = raw
        return result
    lines = raw.splitlines()
    result["output_head"] = "\n".join(_clip_lines(lines[:HEAD_LINES]))
    tail = lines[-TAIL_LINES:] if len(lines) > HEAD_LINES + TAIL_LINES else []
    if tail:
        result["output_tail"] = "\n".join(_clip_lines(tail))
        result["stats"]["omitted_lines"] = len(lines) - HEAD_LINES - TAIL_LINES
    result["hint"] = (f"输出过大已截断(约{est} tokens)。"
                      f"用 read_output(call_id='{call_id}', grep='关键词') 查看详情")
    return result
