"""OWASP ZAP quick-scan 无头模式（等价命令版 Burp Scanner）。
直接用自带 JDK17 跑 jar（zap.bat 只认 PATH 上的 java，本机 1.8 太老——绕开）。
ZAP 状态目录 -dir 落会话工作区，不污染 ~/ZAP；报告 .md 落 outputs 供 read_output/前端看。"""
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
URL_RE = re.compile(r"^https?://[^\s\"'<>]+$")


def paths() -> tuple[str, str]:
    meta = yaml.safe_load((TOOL_DIR / "tool.yaml").read_text(encoding="utf-8"))
    jar_rel = meta.get("bin") or "bin/zap/zap-2.17.0.jar"
    jar = TOOL_DIR / jar_rel
    java = TOOL_DIR / "bin" / "jdk" / "bin" / "java.exe"
    return str(java), str(jar)


def workspace_dir() -> Path:
    sid = os.environ.get("AGENT_SESSION") or "manual"
    d = TOOL_DIR.parents[1] / "workspace" / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not URL_RE.match(url):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)

    java, jar = paths()
    for p, hint in ((java, "bin/jdk（重解压 dl/jdk17.zip）"),
                    (jar, "bin/zap（重解压 dl/zap.zip）")):
        if not Path(p).is_file():
            print(f"缺 {hint}: {p}", file=sys.stderr)
            sys.exit(2)

    ws = workspace_dir()
    report = ws / "outputs" / f"zap_{int(time.time())}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    heap = int(params.get("heap_mb") or 1024)

    cmd = [java, f"-Xmx{heap}m", "-jar", jar, "-cmd",
           "-dir", str(ws / "zap"),
           "-quickurl", url, "-quickout", str(report)]
    if params.get("recursive"):
        cmd.append("-quickrecursive")
    cmd.append("-quickprogress")

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr[-3000:])
    if report.is_file():
        text = report.read_text(encoding="utf-8", errors="replace")
        print(f"\n===== 报告预览（全文在 {report}）=====")
        print(text[:4000])
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
