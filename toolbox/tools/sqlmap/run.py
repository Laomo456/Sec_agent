"""sqlmap SQL 注入检测/利用。参数收敛为安全子集（检测+枚举+导出），禁止任意 flag；
始终 --batch 非交互，输出目录落到会话工作区，参数走数组、不用 shell。
sqlmap 入口定位：自带 bin/sqlmap.py > venv console script(sqlmap.exe) > PATH。"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).resolve().parent
WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"

IDENT_RE = re.compile(r"^[a-zA-Z0-9_.]+$")   # db/table 标识符白名单
DBMS_RE = re.compile(r"^[a-zA-Z0-9 ,]+$")    # --dbms 值白名单
TAMPER_RE = re.compile(r"^[a-zA-Z0-9_,]+$")  # --tamper 脚本名列表白名单


def resolve_sqlmap() -> list:
    """定位 sqlmap 入口：自带 bin/ 优先，其次 venv console script，最后 PATH。"""
    for cand in (TOOL_DIR / "bin" / "sqlmap.py",
                 TOOL_DIR / "bin" / "sqlmap" / "sqlmap.py"):
        if cand.exists():
            return [sys.executable, str(cand)]
    script = Path(sys.executable).parent / "sqlmap.exe"
    if script.exists():
        return [str(script)]
    if shutil.which("sqlmap"):
        return ["sqlmap"]
    return []


def output_dir() -> Path:
    """sqlmap 会话/导出文件落到会话工作区，不污染用户目录。"""
    d = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "sqlmap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(params: dict) -> None:
    url = str(params.get("url", ""))
    if not url.startswith(("http://", "https://")):
        print(f"url 必须以 http:// 或 https:// 开头: {url!r}", file=sys.stderr)
        sys.exit(2)

    cmd = resolve_sqlmap()
    if not cmd:
        print("sqlmap 未安装。任选其一：\n"
              "  1) pip 安装: .venv/Scripts/python.exe -m pip install sqlmap\n"
              "  2) 自带: 将 sqlmap.py 放到本工具目录 bin/sqlmap.py（整个目录可拷走）",
              file=sys.stderr)
        sys.exit(2)

    cmd += ["-u", url, "--batch", "--disable-coloring",
            "--output-dir", str(output_dir())]

    if params.get("data"):
        cmd += ["--data", str(params["data"])]
    if params.get("cookie"):
        cmd += ["--cookie", str(params["cookie"])]
    if params.get("method"):
        cmd += ["--method", str(params["method"])]
    if params.get("level") is not None:
        cmd += ["--level", str(params["level"])]
    if params.get("risk") is not None:
        cmd += ["--risk", str(params["risk"])]
    if params.get("threads") is not None:
        cmd += ["--threads", str(params["threads"])]
    if params.get("proxy"):
        cmd += ["--proxy", str(params["proxy"])]
    if params.get("dbms"):
        dbms = str(params["dbms"])
        if not DBMS_RE.match(dbms):
            print(f"invalid dbms: {dbms!r}", file=sys.stderr)
            sys.exit(2)
        cmd += ["--dbms", dbms]
    if params.get("technique"):
        cmd += ["--technique", str(params["technique"])]
    if params.get("tamper"):
        tamper = str(params["tamper"])
        if not TAMPER_RE.match(tamper):
            print(f"invalid tamper: {tamper!r}", file=sys.stderr)
            sys.exit(2)
        cmd += ["--tamper", tamper]
    if params.get("random_agent"):
        cmd.append("--random-agent")

    db = params.get("db")
    table = params.get("table")
    for name, val in (("db", db), ("table", table)):
        if val and not IDENT_RE.match(str(val)):
            print(f"invalid {name}: {val!r}", file=sys.stderr)
            sys.exit(2)

    action = str(params.get("action", "detect"))
    if action == "detect":
        pass
    elif action == "dbs":
        cmd += ["--dbs"]
    elif action == "tables":
        cmd += ["--tables"] + (["-D", db] if db else [])
    elif action == "columns":
        if not (db and table):
            print("columns 需要同时提供 db 与 table", file=sys.stderr)
            sys.exit(2)
        cmd += ["--columns", "-D", db, "-T", table]
    elif action == "dump":
        if not (db and table):
            print("dump 需要同时提供 db 与 table", file=sys.stderr)
            sys.exit(2)
        cmd += ["--dump", "-D", db, "-T", table]
    elif action == "dump_all":
        cmd += ["--dump-all"]
    else:
        print(f"未知 action: {action}", file=sys.stderr)
        sys.exit(2)

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    sys.stdout.write(proc.stdout or "")
    if (proc.stderr or "").strip():
        sys.stdout.write("\n[stderr]\n" + proc.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
