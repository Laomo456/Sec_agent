"""nuclei 模板化漏洞扫描。-duc 关更新检查(国内连 GitHub 慢)、-silent 只出命中、
-nc 去 ANSI。模板目录优先级：参数 templates_dir > 内置 bin/templates > nuclei 官方默认。"""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")
BUNDLED_TPL = Path(__file__).parent / "bin" / "templates"


def binary() -> str:
    meta = yaml.safe_load((Path(__file__).parent / "tool.yaml")
                          .read_text(encoding="utf-8"))
    p = Path(meta.get("bin") or "nuclei")
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

    cmd = [exe, "-u", url, "-silent", "-nc", "-duc", "-c", "25"]
    tpl = str(params.get("templates_dir") or "")
    if tpl:
        if not Path(tpl).is_dir():
            print(f"templates_dir 不是目录: {tpl}", file=sys.stderr)
            sys.exit(2)
        cmd += ["-t", tpl]
    elif BUNDLED_TPL.is_dir():
        cmd += ["-t", str(BUNDLED_TPL)]
    if params.get("severity"):
        cmd += ["-severity", str(params["severity"])]
    if params.get("tags"):
        cmd += ["-tags", str(params["tags"])]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
