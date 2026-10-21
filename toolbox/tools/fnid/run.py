r"""fnid 垫片：gateway venv（无 torch）转发到系统 py（有 torch/CUDA）。

为什么垫片：模型推理依赖 D:\RAG\fnid 项目 + 系统 Python 的 torch cu126，
gateway venv 两者皆无。本脚本只做路由/超时/stdin 拼装，不做业务逻辑——
单一事实源在 D:\RAG\fnid\src\identify.py（识别类）和原始脚本（语料/训练类）。

ops:
  identify/predict/normalize/vocab/status → identify.py，stdout 本就是 JSON，原样透传
  extract_go/extract_c/train              → 原始脚本，进度文本包一层 {"output_tail": ...}

每个 op 有独立超时（比 tool.yaml 的全局 600s 紧），模型加载卡死不会白烧满额。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FNID = Path(os.environ.get("FNID_HOME", r"D:\RAG\fnid"))
IDENTIFY = FNID / "src" / "identify.py"

# op → (类型, 目标, 秒)。model 类走 identify.py；script 类走原始脚本（cwd=FNID）
MODEL_OPS = ("identify", "predict", "normalize", "vocab", "status")
SCRIPT_OPS = {"extract_go": "src/extract_go.py", "extract_c": "src/extract_c.py"}
TIMEOUTS = {"identify": 150, "predict": 150, "normalize": 30, "vocab": 60,
            "status": 60, "extract_go": 550, "extract_c": 550, "train": 550}
TAIL = 4000          # script 类进度文本只留尾巴（训练日志几 MB 没意义）


def _py() -> str:
    exe = shutil.which("py")
    if not exe:
        print(json.dumps({"status": "error",
                          "detail": "系统找不到 py 启动器（torch 装在系统 Python，不在本 venv）"}))
        sys.exit(3)
    return exe


def _payload(op: str, params: dict) -> str | None:
    """identify/normalize 吃 disasm 文本；predict 吃 JSONL（字符串或数组均可）。"""
    if op in ("identify", "normalize"):
        text = params.get("disasm")
        if not str(text or "").strip():
            print(json.dumps({"status": "error",
                              "detail": f"{op} 需要 params.disasm（从 IDA 复制的函数反汇编文本）"}))
            sys.exit(2)
        return str(text)
    if op == "predict":
        tokens = params.get("tokens")
        if isinstance(tokens, list):      # 数组：元素是记录 dict 或裸 token 列表
            records = [t if isinstance(t, dict) else {"tokens": t} for t in tokens]
            if not records or any(not r.get("tokens") for r in records):
                print(json.dumps({"status": "error",
                                  "detail": "predict.tokens 数组每项要有非空 tokens"}))
                sys.exit(2)
            return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
        if not str(tokens or "").strip():
            print(json.dumps({"status": "error",
                              "detail": "predict 需要 params.tokens（JSONL 字符串或记录数组）"}))
            sys.exit(2)
        return str(tokens)
    return None


def _train_target(params: dict) -> str:
    model = str(params.get("model") or "cnn").lower()
    if model in ("cnn", "textcnn"):
        return "src/train_cnn.py"
    if model in ("logreg", "lr"):
        return "src/train_model.py"
    print(json.dumps({"status": "error",
                      "detail": f"train.model 只认 cnn|logreg，收到 {model!r}"}))
    sys.exit(2)


def _fail(op: str, proc: subprocess.CompletedProcess | subprocess.TimeoutExpired,
          timeout: int) -> None:
    if isinstance(proc, subprocess.TimeoutExpired):
        detail = f"{op} 超过 {timeout}s 被终止（模型加载/语料扫描卡住？先跑 status 自检）"
        out = ""
    else:
        detail = f"{op} 退出码 {proc.returncode}"
        out = (proc.stdout or "").strip()[-TAIL:]
    err = (proc.stderr or "").strip()[-800:] if getattr(proc, "stderr", None) else ""
    print(json.dumps({"status": "error", "op": op, "detail": detail,
                      "output_tail": out, "stderr_tail": err}, ensure_ascii=False))
    sys.exit(1)


def main() -> None:
    envelope = json.loads(sys.argv[1])
    op = str(envelope.get("op") or "")
    params = envelope.get("params") or {}   # 内层参数（schema: {op, params} 信封）
    known = (*MODEL_OPS, *SCRIPT_OPS, "train")
    if op not in known:
        print(json.dumps({"status": "error",
                          "detail": f"未知 op {op!r}，可用: {', '.join(known)}"}))
        sys.exit(2)

    py = _py()
    timeout = TIMEOUTS[op]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    if op in MODEL_OPS:
        argv = [py, str(IDENTIFY), op]
        top = params.get("top")
        if op in ("identify", "predict") and top:
            argv += ["--top", str(int(top))]
        stdin = _payload(op, params)
    else:
        target = _train_target(params) if op == "train" else SCRIPT_OPS[op]
        argv = [py, str(FNID / target)]
        stdin = None                            # 语料/训练不吃 stdin

    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=str(FNID), timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        _fail(op, e, timeout)

    if proc.returncode != 0 or not (proc.stdout or "").strip():
        _fail(op, proc, timeout)

    if op in MODEL_OPS:
        # identify.py 的 stdout 本就是一行 JSON，透传（stderr 丢弃：torch 的
        # UserWarning 会混进来污染 JSON）
        print(proc.stdout.strip())
        return

    # script 类：进度文本 → 结构化尾巴
    print(json.dumps({"status": "ok", "op": op,
                      "output_tail": proc.stdout.strip()[-TAIL:]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
