"""katana 标准爬虫。-silent 只输出发现的 URL 列表，一行一个，正好喂归一化管道。"""
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
    p = Path(meta.get("bin") or "katana")
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

    cmd = [exe, "-u", url, "-silent", "-d",
           str(int(params.get("depth") or 2))]
    if params.get("js_crawl"):
        cmd.append("-jc")
    if params.get("known_files"):
        cmd += ["-kf", "all"]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
