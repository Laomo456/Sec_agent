"""file_edit：原文精确替换（Claude Edit / Codex str_replace 同款机制）。

失败即报结构性错误让模型自纠错：
- 找不到 → 提示空白/缩进须逐字符一致，建议先 file_read 复制原文
- 多处匹配 → 报数量，要求加长片段或 replace_all
成功后回显改动点上下文（±3 行），模型无需重读整个文件即可确认。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


def show_context(text: str, pos: int, span: int = 3) -> str:
    lines = text.splitlines()
    # 改动发生在第几行（按替换前内容算）
    n = text[:pos].count("\n")
    lo, hi = max(0, n - span), min(len(lines), n + span + 1)
    out = []
    for i in range(lo, hi):
        mark = "→" if i == n else " "
        out.append(f"{mark}{i + 1:4}: {lines[i]}")
    return "\n".join(out)


def main(params: dict) -> None:
    p = resolve(params["path"])
    if not p.is_file():
        print(f"文件不存在: {p}", file=sys.stderr)
        sys.exit(2)
    text = p.read_text(encoding="utf-8")
    old, new = params["old_text"], params["new_text"]
    replace_all = bool(params.get("replace_all"))
    n = text.count(old)

    if n == 0:
        print("old_text 未在文件中找到。替换要求逐字符一致（含缩进、空格、换行）；"
              "请先 file_read 再从原文复制片段。", file=sys.stderr)
        sys.exit(2)
    if n > 1 and not replace_all:
        print(f"old_text 匹配到 {n} 处，不唯一。请加长片段包含更多上下文，"
              "或设 replace_all=true 全部替换。", file=sys.stderr)
        sys.exit(2)

    pos = text.find(old)
    ctx_before = show_context(text, pos)
    text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")

    print(f"已修改 {p}（替换 {n if replace_all else 1} 处，"
          f"{len(old)} → {len(new)} 字符）")
    print("---- 改动上下文（替换前）----")
    print(ctx_before)


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
