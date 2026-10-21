"""OneForAll 子域收集一把梭（shim 卡，fnid 模式）。

OneForAll 依赖 2022 年代的包（SQLAlchemy 1.3 等），gateway venv(3.14) 装不上，
自带 3.10 venv 在 home/OneForAll/.venv —— run.py 只做参数白名单校验 + 转发 + 结果精简。
ONEFORALL_HOME 环境变量可把项目根指到别处（便携副本用）。
domain 走保守白名单；bool 开关映射 fire 的 True/False；结果导 JSON 后重排成紧凑行。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$")

# 比 tool.yaml 的 timeout(1500) 小一截：让自己先优雅收尾，别被 runner 硬杀
SUB_TIMEOUT = 1440


def main(params: dict) -> None:
    domain = str(params.get("domain", "")).strip().lower()
    if not domain or not DOMAIN_RE.match(domain) or domain.startswith("http"):
        print(f"invalid domain: {params.get('domain')!r}（裸根域名，如 example.com）",
              file=sys.stderr)
        sys.exit(2)

    home = Path(os.environ.get("ONEFORALL_HOME") or
                Path(__file__).resolve().parent / "home" / "OneForAll")
    py = home / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        print(f"OneForAll 环境缺失: {py}（重装见 home/ 下安装说明或 ONEFORALL_HOME）",
              file=sys.stderr)
        sys.exit(2)

    def flag(name: str, default: bool, schema_name: str = "") -> list:
        """OneForAll 的 CLI bool 值拆成独立 argv 元素；schema 参数名可不同于 CLI 名。"""
        val = bool(params.get(schema_name or name, default))
        return [f"--{name}", str(val)]

    out = home / "results" / f"ofa_{domain}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(py), "oneforall.py", "--target", domain,
           *flag("brute", True), *flag("dns", True), *flag("req", True),
           *flag("takeover", False), *flag("alive", False, "alive_only"),
           "--fmt", "json", "--path", str(out), "run"]
    mods = [n for n, d in (("brute", True), ("dns", True), ("req", True),
                           ("takeover", False))
            if bool(params.get(n, d))]

    try:
        proc = subprocess.run(cmd, cwd=str(home), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=SUB_TIMEOUT,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired as e:
        tail = (e.stdout or "") if isinstance(e.stdout, str) else ""
        print(f"[超时] OneForAll 跑满 {SUB_TIMEOUT // 60} 分钟被停。被动来源已尽量收集但结果未导出；"
              f"重试建议 brute=False（纯被动，约 4~8 分钟）或改用 subfinder。")
        if tail:
            print("\n[最后日志]\n" + "\n".join(tail.splitlines()[-15:]))
        sys.exit(3)

    if proc.returncode != 0:
        # loguru 正常也往 stderr 打 INFO——失败判定只看退出码
        print(f"[退出码 {proc.returncode}] 日志尾部:\n" +
              "\n".join((proc.stderr or proc.stdout or "").splitlines()[-20:]))
        sys.exit(proc.returncode or 1)

    report(domain, mods, out, (proc.stderr or "") + (proc.stdout or ""))


def report(domain: str, mods: list, out: Path, log_text: str = "") -> None:
    """JSON 结果重排成紧凑行：统计头 + 每行 subdomain/状态/来源/IP + 来源模块小结。"""
    try:
        rows = json.loads(out.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"结果文件读取失败 {out}: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(rows, list):
        rows = [rows]

    resolved = [r for r in rows if r.get("resolve") or r.get("ip")]
    alive = [r for r in rows if r.get("alive")]
    print(f"[OneForAll] {domain} | 模块: {'+'.join(mods) if mods else '被动'} | "
          f"共 {len(rows)} 条，已解析 {len(resolved)}，存活 {len(alive)}")
    for r in sorted(rows, key=lambda x: str(x.get("subdomain", ""))):
        sub = str(r.get("subdomain", ""))
        marks = "存活" if r.get("alive") else ("已解析" if r.get("resolve") else "未解析")
        src = str(r.get("sources") or "-")
        ip = str(r.get("ip") or "-")
        port = str(r.get("port") or "")
        line = f"{sub}  [{marks}]  来源={src}  ip={ip}"
        if port and port not in ("80", "-"):
            line += f"  port={port}"
        if r.get("takeover"):
            line += "  ⚠takeover"
        print(line)
    print(f"\n完整字段(subdomain/sources/cnames/resolve/public/ip/port/banner/takeover) "
          f"→ {out}")
    summary = source_summary(log_text)
    if summary:
        print(f"来源模块: {summary}")


def source_summary(log_text: str) -> str:
    """从 loguru 日志提炼各收集模块战果：'RobtexQuery 2 | GoogleQuery 超时'。"""
    got, failed = {}, []
    for line in log_text.splitlines():
        m = re.search(r"module:\d+ - (\w+) module took [\d.]+ seconds found (\d+) subdomains",
                      line)
        if m:
            got[m.group(1)] = int(m.group(2))
        elif "[ERROR]" in line:
            h = re.search(r"host='([^']+)'", line)
            if h and h.group(1) not in failed:
                failed.append(h.group(1))
    parts = [f"{k} {v}" for k, v in got.items() if v > 0]
    if failed:
        parts.append(f"失败来源: {', '.join(failed[:6])}" + ("…" if len(failed) > 6 else ""))
    return " | ".join(parts)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
