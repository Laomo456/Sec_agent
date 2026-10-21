"""dirsearch Web 目录爆破。经 venv 里 pip 装的 dirsearch(python -m dirsearch)执行，
不依赖 PATH。输出多大都不用管——框架自动截断、落盘、给 read_output 钻取入口。"""
import json
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not url.startswith(("http://", "https://")):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)
    cmd = [sys.executable, "-m", "dirsearch", "-u", url, "--no-color"]
    if params.get("extensions"):
        cmd += ["-e", str(params["extensions"])]
    if params.get("wordlist"):
        cmd += ["-w", str(params["wordlist"])]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
