"""mitmdump 抓包代理。跑 duration 秒自动收工：stdout 是流摘要(方法/URL/状态)，
flows 文件落 workspace 供 mitmproxy 工具链后续读。venv Scripts/mitmdump.exe。"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def entry() -> str:
    exe = Path(sys.executable).parent / "mitmdump.exe"
    if not exe.is_file():
        print(f"mitmdump 不在 venv Scripts: {exe}（pip install mitmproxy）",
              file=sys.stderr)
        sys.exit(2)
    return str(exe)


def workspace_dir() -> Path:
    sid = os.environ.get("AGENT_SESSION") or "manual"
    d = Path(__file__).parents[2] / "workspace" / sid / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(params: dict) -> None:
    mode = params.get("mode")
    port = int(params.get("listen_port") or 8888)
    duration = min(int(params.get("duration") or 30), 300)
    flows = workspace_dir() / f"flows_{int(time.time())}.mitm"

    cmd = [entry(), "-p", str(port), "-w", str(flows)]
    if mode == "reverse":
        target = str(params.get("reverse_target") or "")
        if not URL_RE.match(target):
            print(f"reverse 模式必须给合法 reverse_target: {target!r}",
                  file=sys.stderr)
            sys.exit(2)
        cmd += ["--mode", f"reverse:{target}"]
    elif mode != "regular":
        print(f"unknown mode: {mode!r}", file=sys.stderr)
        sys.exit(2)

    print(f"代理监听 127.0.0.1:{port}（{mode}），抓 {duration}s 后收工…")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    try:
        out, _ = proc.communicate(timeout=duration)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = ""
    print(out or "(无流量)")
    print(f"\nflows: {flows}（mitmproxy 可复读）")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
