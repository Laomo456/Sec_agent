"""hash_calc：纯 Python 哈希工具，无外部依赖。"""
import hashlib
import json
import sys
from pathlib import Path


def main(params: dict) -> None:
    algo = params["algo"]
    text = params.get("text")
    file = params.get("file")
    if bool(text) == bool(file):
        print("text 与 file 必须二选一", file=sys.stderr)
        sys.exit(2)
    if text is not None:
        data = str(text).encode("utf-8")
        print(f"{algo}({text!r}) = {hashlib.new(algo, data).hexdigest()}")
    else:
        p = Path(file)
        if not p.is_file():
            print(f"文件不存在: {file}", file=sys.stderr)
            sys.exit(2)
        h = hashlib.new(algo)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        print(f"{algo}({file}) = {h.hexdigest()}")


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
