"""interactsh 带外回连客户端（有状态包装）。

模型用法（三步）：
  start → 返回一次性回调 URL（客户端常驻后台轮询 oast 服务器）
  poll  → 打印已捕获的交互（DNS/HTTP/SMTP），可 match 过滤
  stop  → 杀后台进程

状态落 workspace/<会话>/interactsh/（pid/url/out.json/log），跨调用存活，
会话隔离。exe 用 tool.yaml 的 bin 相对路径。"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).parent
URL_LINE_RE = re.compile(r"https?://[a-zA-Z0-9.-]+\.[a-z]+")


def binary() -> str:
    meta = yaml.safe_load((TOOL_DIR / "tool.yaml").read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "interactsh-client")
    return str(p if p.is_absolute() else TOOL_DIR / p)


def state_dir() -> Path:
    sid = os.environ.get("AGENT_SESSION") or "manual"
    d = TOOL_DIR.parents[1] / "workspace" / sid / "interactsh"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


def do_start(params: dict) -> None:
    sd = state_dir()
    old = sd / "pid"
    if old.is_file() and pid_alive(int(old.read_text().strip())):
        url = (sd / "url").read_text().strip() if (sd / "url").is_file() else "?"
        print(f"已有客户端在跑(pid={old.read_text().strip()})，URL: {url}")
        print(f"要换新地址先 action=stop。")
        return

    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}", file=sys.stderr)
        sys.exit(2)

    n = int(params.get("count") or 1)
    for f in ("out.json", "log", "url"):
        (sd / f).unlink(missing_ok=True)

    log = open(sd / "log", "wb")
    proc = subprocess.Popen(
        [exe, "-json", "-o", str(sd / "out.json"), "-duc",
         "-pi", "5", "-n", str(n)],
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    (sd / "pid").write_text(str(proc.pid))

    # 等服务器注册、地址打印（interactsh 启动 1-3s 出 URL）
    for _ in range(30):
        time.sleep(0.5)
        text = (sd / "log").read_text(encoding="utf-8", errors="replace")
        m = URL_LINE_RE.search(text)
        if m and "oast" in m.group(0):
            (sd / "url").write_text(m.group(0))
            print(f"回调 URL: {m.group(0)}")
            print("把该 URL（或其任意子域前缀）嵌进 payload 触发出网，"
                  "之后 action=poll 查看命中，用完 action=stop。")
            return
    print("启动后 15s 未见回调 URL（oast 服务器连不上？），log 尾部：")
    print(text[-800:])
    sys.exit(2)


def do_poll(params: dict) -> None:
    sd = state_dir()
    pid = (sd / "pid").read_text().strip() if (sd / "pid").is_file() else ""
    if pid and pid_alive(int(pid)):
        print(f"客户端运行中 (pid={pid})")
    else:
        print("客户端未在运行（先 action=start）")
    url = (sd / "url").read_text().strip() if (sd / "url").is_file() else "(无)"
    print(f"回调 URL: {url}\n")

    match = str(params.get("match") or "").lower()
    data = (sd / "out.json").read_text(encoding="utf-8", errors="replace") \
        if (sd / "out.json").is_file() else ""
    hits = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            it = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = json.dumps(it, ensure_ascii=False).lower()
        if match and match not in blob:
            continue
        hits += 1
        proto = it.get("protocol") or "?"
        host = it.get("full-id") or it.get("q") or it.get("destination") or "?"
        src = it.get("source-ip") or it.get("remote-address") or ""
        ts = it.get("timestamp") or ""
        print(f"[{ts}] {proto} {host} {src}".rstrip())
        detail = it.get("raw-request") or it.get("raw") or ""
        if detail:
            print("  " + str(detail).replace("\n", "\n  ")[:300])
    print(f"\n共 {hits} 条交互" + (f"（过滤: {match}）" if match else ""))


def do_stop() -> None:
    sd = state_dir()
    pid = (sd / "pid").read_text().strip() if (sd / "pid").is_file() else ""
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                       capture_output=True)
        print(f"已停止 pid={pid}")
    else:
        print("无运行中的客户端")
    (sd / "pid").unlink(missing_ok=True)


def main(params: dict) -> None:
    action = params.get("action")
    if action == "start":
        do_start(params)
    elif action == "poll":
        do_poll(params)
    elif action == "stop":
        do_stop()
    else:
        print(f"unknown action: {action!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
