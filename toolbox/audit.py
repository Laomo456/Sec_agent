"""append-only 审计日志 + 会话工作区。

每个 server 进程一个 session 目录（环境变量 AGENT_SESSION 可覆盖）：
  workspace/<session>/audit.jsonl   事件流——viewer 与"模型为什么这么决策"调试的数据源
  workspace/<session>/outputs/      每次工具调用的全量输出落盘
"""
import json
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE = BASE_DIR / "workspace"
SESSION = os.environ.get("AGENT_SESSION", datetime.now().strftime("s%Y%m%d-%H%M%S"))

_call_seq = 0


def session_dir() -> Path:
    d = WORKSPACE / SESSION
    (d / "outputs").mkdir(parents=True, exist_ok=True)
    return d


def outputs_dir() -> Path:
    return session_dir() / "outputs"


def output_path(call_id: str) -> Path:
    return outputs_dir() / f"{call_id}.txt"


def next_call_id() -> str:
    global _call_seq
    _call_seq += 1
    return f"{os.environ.get('AGENT_CALL_PREFIX', '')}c{_call_seq:03d}"


def log_event(event_type: str, **fields) -> None:
    """追加一行事件。type ∈ {tool_call, search, output_read, validation_error}。

    约定字段：tool / params / call_id / status / duration_ms / tokens_est / normalized。
    """
    evt = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
           "session": SESSION, "type": event_type, **fields}
    with open(session_dir() / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(evt, ensure_ascii=False) + "\n")
