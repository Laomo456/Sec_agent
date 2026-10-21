r"""popchain 垫片:gateway venv(无 torch)转发到系统 py(有 torch/CUDA)。

为什么垫片:解析/枚举纯 stdlib 但和模型共用一个项目根,模型推理依赖
D:\RAG\popchain + 系统 Python 的 torch。本脚本只做路由/超时/参数拼装,
单一事实源在 D:\RAG\popchain\src\cli.py(JSON in/JSON out)。

ops(全部映射到 cli.py 子命令):
  analyze paths|path → 多路径;chains/dynamic/score path → 单路径;
  search/status → 旗标透传。score 在模型未训练时 cli 返回 error JSON(不炸)。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("POPCHAIN_HOME", r"D:\RAG\popchain"))
CLI = HOME / "src" / "cli.py"

OPS = ("chains", "analyze", "score", "dynamic", "search", "status")
TIMEOUTS = {"chains": 240, "analyze": 120, "score": 150, "dynamic": 60,
            "search": 30, "status": 30}


def _fail(op: str, detail: str, extra: dict | None = None) -> None:
    print(json.dumps({"status": "error", "op": op, "detail": detail,
                      **(extra or {})}, ensure_ascii=False))
    sys.exit(1)


def build_argv(op: str, p: dict) -> list:
    """envelope params → cli.py 子命令 argv(可单测的纯函数)。"""
    if op == "analyze":
        paths = p.get("paths") or p.get("path")
        if isinstance(paths, str):
            paths = [paths]
        if not paths or not isinstance(paths, list):
            _fail(op, "analyze 需要 params.paths(文件/目录数组或单个 path)")
        return ["analyze", *[str(x) for x in paths]]
    if op in ("chains", "dynamic", "score"):
        path = str(p.get("path") or "").strip()
        if not path:
            _fail(op, f"{op} 需要 params.path(PHP 文件或目录)")
        argv = [op, path]
        if op == "chains":
            for k, flag in (("depth", "--depth"), ("max", "--max"), ("top", "--top")):
                if p.get(k):
                    argv += [flag, str(int(p[k]))]
            if p.get("no_save"):
                argv.append("--no-save")
        if op == "score" and p.get("top"):
            argv += ["--top", str(int(p["top"]))]
        return argv
    if op == "search":
        argv = ["search"]
        for k, flag in (("q", "--q"), ("sink", "--sink"), ("status", "--status")):
            if p.get(k):
                argv += [flag, str(p[k])]
        for k, flag in (("min_len", "--min-len"), ("max_len", "--max-len"),
                        ("limit", "--limit")):
            if p.get(k):
                argv += [flag, str(int(p[k]))]
        return argv
    return [op]                                     # status


def main() -> None:
    envelope = json.loads(sys.argv[1])
    op = str(envelope.get("op") or "")
    params = envelope.get("params") or {}
    if op not in OPS:
        _fail(op, f"未知 op {op!r},可用: {', '.join(OPS)}")
        return
    if not CLI.exists():
        _fail(op, f"popchain 项目不存在: {CLI}(POPCHAIN_HOME 可重定向)")
        return

    py = shutil.which("py")
    if not py:
        _fail(op, "系统找不到 py 启动器(popchain 依赖系统 Python 的 torch)")
        return

    argv = [py, str(CLI), *build_argv(op, params)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=str(HOME), timeout=TIMEOUTS[op], env=env)
    except subprocess.TimeoutExpired:
        _fail(op, f"{op} 超过 {TIMEOUTS[op]}s 被终止(大目录?先 analyze 单文件)")
        return

    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        _fail(op, f"{op} 退出码 {proc.returncode}",
              {"stderr_tail": (proc.stderr or "")[-800:],
               "output_tail": out[-1500:]})
        return
    print(out)                                      # cli.py 本就输出 JSON,透传


if __name__ == "__main__":
    main()
