"""commix 命令注入自动化。venv console script 是 python 入口脚本（非 .exe），
用 [sys.executable, 脚本路径] 调起。始终 --batch 非交互。"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def entry() -> list:
    """commix 入口：venv Scripts/commix（python 脚本）。"""
    script = Path(sys.executable).parent / "commix"
    if not script.is_file():
        print(f"commix 不在 venv Scripts: {script}（pip install commix）",
              file=sys.stderr)
        sys.exit(2)
    return [sys.executable, str(script)]


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not URL_RE.match(url):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)

    cmd = entry() + ["-u", url, "--batch"]
    if params.get("data"):
        cmd += ["--data", str(params["data"])]
    if params.get("cookie"):
        cmd += ["--cookie", str(params["cookie"])]
    if params.get("level"):
        cmd += ["--level", str(int(params["level"]))]
    if params.get("technique"):
        cmd += ["--technique", str(params["technique"])]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
