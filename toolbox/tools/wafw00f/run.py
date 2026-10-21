"""wafw00f Web 防火墙(WAF)指纹。经 venv 里 pip 装的 wafw00f 执行。
wafw00f 无 __main__，走 venv Scripts 里的 console script；不用 shell，URL 校验 scheme。"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def wafw00f_exe() -> Path:
    """venv Scripts 下的 console script（与 run.py 同一个解释器）。"""
    return Path(sys.executable).parent / "wafw00f.exe"


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not url.startswith(("http://", "https://")):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)
    exe = wafw00f_exe()
    if not exe.exists():
        print(f"wafw00f 未安装: {exe}（.venv/Scripts/python.exe -m pip install wafw00f）",
              file=sys.stderr)
        sys.exit(2)
    cmd = [str(exe), url]
    if params.get("findall"):
        cmd.append("-a")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
