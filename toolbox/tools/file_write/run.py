"""file_write：覆盖写入文本文件，父目录自动创建。
路径约定：不带盘符的相对路径（含 /tmp 这类 Unix 风格）一律落会话工作区
workspace/<会话>/files/，返回绝对路径。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:                       # 带盘符才是真绝对路径
        return path
    root = WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files"
    return root / path


def main(params: dict) -> None:
    p = resolve(params["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    data = params["content"]
    p.write_text(data, encoding="utf-8")
    print(f"已写入 {p}（{len(data.encode('utf-8'))} 字节，{data.count(chr(10)) + 1} 行）")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
