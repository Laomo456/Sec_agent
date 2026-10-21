"""arjun 隐藏参数发现。跑在 secbox venv（runner 用 venv python 调 run.py，
sys.executable 即 venv python，Scripts/arjun.exe 是其配套入口，不依赖 PATH）。"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def binary() -> str:
    return str(Path(sys.executable).parent / "arjun.exe")


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not URL_RE.match(url):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)

    exe = binary()
    if not Path(exe).exists():
        print(f"arjun 不在 venv Scripts: {exe}（pip install arjun）", file=sys.stderr)
        sys.exit(2)

    cmd = [exe, "-u", url, "-t", "15"]
    if params.get("method"):
        cmd += ["-m", str(params["method"])]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
