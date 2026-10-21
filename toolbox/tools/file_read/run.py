"""file_read：读文本文件，Claude Read 风格。相对路径落会话工作区（同 file_write）。

- 流式按行读，只物化 offset~limit 窗口，大文件不整体载入内存（旧实现 read_text 会 OOM）
- 单行超 MAX_LINE_CHARS 截断并标注（压缩 JS/JSON 的"一行几 MB"不撑爆上下文）
- 二进制探测：首 8KB 含 NUL 字节 → 不吐乱码，提示改用 pe_unpack/hexdump
- 可选 pattern：内建 grep，只回匹配行（正则，非法则退化为子串）
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"
MAX_LINE_CHARS = 2000   # 与 server.read_output 的单行上限一致


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


def clip(line: str) -> str:
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS] + f" …[行过长，截断 {len(line) - MAX_LINE_CHARS} 字符]"
    return line


def grep_mode(f, rx, limit: int) -> None:
    """只回匹配行（含行号），最多 limit 条。"""
    hits, scanned = [], 0
    for i, line in enumerate(f):
        scanned = i + 1
        line = line.rstrip("\n")
        if rx.search(line):
            hits.append(f"{i + 1}: {clip(line)}")
            if len(hits) >= limit:
                print(f"匹配 {len(hits)} 行（达上限 {limit}，扫描至第 {scanned} 行，可能还有更多）")
                print("\n".join(hits))
                return
    if not hits:
        print(f"无匹配（共扫描 {scanned} 行）")
        return
    print(f"匹配 {len(hits)} 行（共扫描 {scanned} 行）")
    print("\n".join(hits))


def main(params: dict) -> None:
    p = resolve(params["path"])
    if not p.is_file():
        print(f"文件不存在: {p}", file=sys.stderr)
        sys.exit(2)

    # 二进制探测：首 8KB 出现 NUL 字节即判为非文本
    with p.open("rb") as fb:
        if b"\x00" in fb.read(8192):
            print(f"[二进制文件] {p}（{p.stat().st_size} 字节）。file_read 只读文本，"
                  f"二进制请用 pe_unpack / packer_detect / fw_unpack，或 run_cmd 跑 hexdump 查看。")
            return

    pattern = params.get("pattern") or ""
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))   # 非法正则 → 当子串
        limit = min(2000, max(1, int(params.get("limit", 200))))
        with p.open("r", encoding="utf-8", errors="replace") as f:
            grep_mode(f, rx, limit)
        return

    offset = max(0, int(params.get("offset", 0)))
    limit = min(2000, max(1, int(params.get("limit", 200))))
    out, shown, last_idx, total, more = [], 0, -1, None, False
    with p.open("r", encoding="utf-8", errors="replace") as f:
        i = -1
        for i, line in enumerate(f):
            if i < offset:
                continue
            if shown >= limit:
                more = True
                break
            out.append(f"{i + 1}: {clip(line.rstrip(chr(10)))}")
            shown, last_idx = shown + 1, i
        else:
            total = i + 1   # 循环完整结束（未因 limit 跳出）= 读到了文件末尾

    if shown == 0:
        tail = f"共 {total} 行" if total is not None else ""
        print(f"{p}（offset={offset} 超出范围或文件为空）{tail}".rstrip())
        return
    if total is not None:
        print(f"{p} 共 {total} 行，返回第 {offset}-{last_idx} 行")
    else:
        print(f"{p} 返回第 {offset}-{last_idx} 行，还有更多（用 offset={last_idx + 1} 续读）")
    print("\n".join(out))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
