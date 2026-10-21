"""file_list：列目录，Claude Glob/LS 风格。相对路径落会话工作区（同 file_write）。

- recursive：递归子目录（配 pattern = Glob 查找）
- pattern：glob 通配，如 *.py / **/*.php（recursive 时等价 rglob）
- 每项带 mtime 列，方便"最近改了啥"
- 去掉旧实现"对任意不存在的绝对路径偷偷 mkdir"的副作用——只读工具不该造目录，
  只有会话工作区根缺失才自动补（列空工作区的正常场景）
"""
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"
SCAN_CAP = 5000   # 递归扫描收集上限，防 node_modules 这类巨树拖垮
SHOW = 200        # 展示上限


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


def main(params: dict) -> None:
    p = resolve(params["path"])
    recursive = bool(params.get("recursive"))
    pattern = params.get("pattern") or ""

    if not p.is_dir():
        ws_root = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files"
        if p == ws_root:                       # 仅工作区根缺失才自动补
            p.mkdir(parents=True, exist_ok=True)
        else:
            print(f"目录不存在: {p}", file=sys.stderr)
            sys.exit(2)

    if pattern:
        it = p.rglob(pattern) if recursive else p.glob(pattern)
    elif recursive:
        it = p.rglob("*")
    else:
        it = p.iterdir()

    entries, capped = [], False
    for e in it:
        entries.append(e)
        if len(entries) >= SCAN_CAP:
            capped = True
            break
    entries.sort(key=lambda e: (e.is_file(), str(e).lower()))

    rel = recursive or bool(pattern)   # 递归/通配时显示相对路径，否则只显示名字
    tags = "".join(t for t in ("[递归]" if recursive else "",
                               f"[匹配 {pattern}]" if pattern else "") if t)
    note = f"（扫描达上限 {SCAN_CAP}，结果不完整）" if capped else ""
    print(f"{p} {tags} 共 {len(entries)} 项{note}".replace("  ", " "))

    for e in entries[:SHOW]:
        try:
            st = e.stat()
            kind = "d" if e.is_dir() else "f"
            size = "-" if e.is_dir() else st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            kind, size, mtime = "?", "-", "----------------"
        name = str(e.relative_to(p)) if rel else e.name
        print(f"{kind} {size:>12} {mtime} {name}")
    if len(entries) > SHOW:
        print(f"... 其余 {len(entries) - SHOW} 项省略")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
