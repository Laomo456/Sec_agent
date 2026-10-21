"""dalfox XSS 扫描。默认 -S 静默(只出 PoC 与进度)，--waf-evasion 规避模式单独暴露。
exe 用 tool.yaml 的 bin 相对路径，不依赖 PATH。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def binary() -> str:
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "dalfox")
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not URL_RE.match(url):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)

    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}（改 tool.yaml 的 bin 或重装）", file=sys.stderr)
        sys.exit(2)

    cmd = [exe, "url", url, "-S"]
    if params.get("param"):
        cmd += ["-p", str(params["param"])]
    if params.get("data"):
        cmd += ["-d", str(params["data"])]
    if params.get("blind"):
        b = str(params["blind"])
        if not re.match(r"^[a-zA-Z0-9._:/-]+$", b):
            print(f"invalid blind url: {b!r}", file=sys.stderr)
            sys.exit(2)
        cmd += ["-b", b]
    if params.get("waf_evasion"):
        cmd.append("--waf-evasion")
    if params.get("follow_redirects"):
        cmd.append("-F")

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
