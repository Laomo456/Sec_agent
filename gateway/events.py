"""Gateway⇄前端事件契约 + 落盘。实时推送与历史回放共用同一格式。

事件类型（P1）：
  user_message        {content}
  skill_injected      {name, content}                    本条消息勾选的技能块（渲染在用户消息上方）
  assistant_message   {content, reasoning, iteration}   P2 再加 assistant_delta 流式变体
  tool_call           {tool, params, iteration}
  tool_result         {tool, call_id?, result(截断预览), iteration}
  done                {content}                          最终回答
  error               {detail}
  interject           {id, content}                      运行中用户插话（入队）
  interject_applied   {id, iteration}                    插话已注入对话
  file_upload         {name, size}                       文件已上传进工作区（POST /api/sessions/{sid}/files）
  subagent_state      {agent_id,name,status,detail?,text?} 子任务状态（不冒充主任务 done/error）
  assistant_delta     {iteration, content, reasoning}    live：流式增量，不落盘无 seq
  workflow_*          工作流运行事件（见 workflow.py），走 workflow 专用 bus

每事件公共字段：seq（会话内递增）、ts、session。
落盘 gateway/sessions/<id>/events.jsonl —— WS 重连时先回放再接实时。
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent / "sessions"

# tool_result 里给前端预览的长度（完整数据走 /api/outputs）
RESULT_PREVIEW_CHARS = 600


class EventBus:
    """一个会话一个 bus：append 落盘 + 广播给所有订阅者（WS）。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = SESSIONS_DIR / session_id / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 立即建文件：新会话才能出现在会话列表里。已存在就别 touch——
        # 会刷新 mtime，只是打开看一眼的旧会话会跳到列表最前
        if not self.path.exists():
            self.path.touch()
        self._subscribers: list[asyncio.Queue] = []
        # seq 从既有文件续接——进程重启后归零会产生重复 seq，
        # 前端按 seq 去重会把新事件全部丢弃（表现为"界面没反应"）
        self._seq = 0
        if self.path.stat().st_size:
            with open(self.path, encoding="utf-8") as f:
                self._seq = sum(1 for l in f if l.strip())

    def emit(self, event_type: str, **fields) -> dict:
        self._seq += 1
        evt = {"seq": self._seq,
               "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
               "session": self.session_id, "type": event_type, **fields}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        for q in list(self._subscribers):
            q.put_nowait(evt)
        return evt

    def emit_live(self, event_type: str, **fields) -> None:
        """只推不落盘的实时事件（流式增量 assistant_delta 等）：
        无 seq（前端不去重、回放不出现），最终内容以 assistant_message 为准。"""
        evt = {"seq": 0, "live": True,
               "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
               "session": self.session_id, "type": event_type, **fields}
        for q in list(self._subscribers):
            q.put_nowait(evt)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def history(self) -> list:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in
                self.path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _session_title(path: Path) -> str:
    """会话标题 = 首条用户消息（只读文件开头几行，不整读大文件）。"""
    try:
        with open(path, encoding="utf-8") as f:
            for _, line in zip(range(40), f):
                if '"user_message"' in line:
                    return (json.loads(line).get("content") or "")[:60]
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def list_sessions() -> list:
    if not SESSIONS_DIR.exists():
        return []
    out = []
    for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
        ev = d / "events.jsonl"
        if d.is_dir() and ev.exists():
            out.append({"session_id": d.name, "title": _session_title(ev),
                        "updated": int(ev.stat().st_mtime)})
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out
