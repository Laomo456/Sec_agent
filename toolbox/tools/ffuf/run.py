"""ffuf Web 模糊测试。url 必须带 FUZZ 关键字标注入点；参数收敛为安全子集。
exe 用 tool.yaml 的 bin 相对路径（tools/ffuf/bin/ffuf.exe），不依赖 PATH。
输出走 -s 静默模式：一行一个命中(URL:状态:大小:词:行)。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")
DEFAULT_WL = Path(__file__).parent / "wordlist-common.txt"


def binary() -> str:
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "ffuf")
    return str(p if p.is_absolute() else Path(__file__).parent / p)


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not URL_RE.match(url):
        print(f"url 必须是合法 http(s) URL: {url!r}", file=sys.stderr)
        sys.exit(2)
    if "FUZZ" not in url and "FUZZ" not in str(params.get("data", "")):
        print("url 或 data 里必须包含 FUZZ 关键字标注入点，"
              "如 http://target/FUZZ 或 http://target/?FUZZ=1", file=sys.stderr)
        sys.exit(2)

    wl = str(params.get("wordlist") or DEFAULT_WL)
    if not Path(wl).is_file():
        print(f"字典不存在: {wl}（不传 wordlist 用内置字典）", file=sys.stderr)
        sys.exit(2)

    exe = binary()
    if not Path(exe).exists():
        print(f"bin 路径不存在: {exe}（改 tool.yaml 的 bin 或重装）", file=sys.stderr)
        sys.exit(2)

    # 注意 ffuf 2.1.0-dev 的 flag 面：无 -nc（颜色默认关，-c 才是开色）、
    # maxtime 拼写无连字符；-noninteractive 显式关交互控制台
    cmd = [exe, "-s", "-noninteractive", "-w", wl, "-u", url, "-t", "50"]
    if params.get("match_status"):
        cmd += ["-mc", str(params["match_status"])]
    if params.get("method"):
        cmd += ["-X", str(params["method"])]
    if params.get("data"):
        cmd += ["-d", str(params["data"])]
    if params.get("auto_filter"):
        cmd.append("-ac")
    if params.get("follow_redirects"):
        cmd.append("-r")
    if params.get("max_time"):
        cmd += ["-maxtime", str(int(params["max_time"]))]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
