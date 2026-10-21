r"""rag 工具入口:通用本地知识库(图书馆)——检索/收录/反馈。

契约(runner.py):argv[1] = {"op":..., "params":{...}} JSON;stdout 打紧凑 JSON;
退出码 0=ok,2=参数错,1=运行错。rag_store 纯 stdlib(embedding 走 HTTP 或可选
sentence_transformers),gateway venv 直接 import 即可,不需要 fnid 那种垫片。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import rag_store  # noqa: E402

MODEL_MAX_CHARS = 300   # 证据卡片段默认长度:5 张卡 × 300 CJK ≈ 1500 est-tokens,顶满输出预算


def _fail(detail: str, code: int = 1) -> int:
    print(json.dumps({"status": "error", "detail": detail}, ensure_ascii=False))
    return code


def main() -> int:
    try:
        req = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except (json.JSONDecodeError, IndexError):
        return _fail("参数必须是 JSON:{\"op\": ..., \"params\": {...}}", 2)
    op = str(req.get("op") or "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        return _fail("params 必须是对象", 2)

    try:
        if op == "search":
            params.setdefault("max_chars", MODEL_MAX_CHARS)
            result = rag_store.search(str(params.pop("query", "")), **params)
        elif op == "status":
            result = rag_store.status()
        elif op == "collections":
            result = {"status": "ok",
                      "collections": rag_store.status()["collections"]}
        elif op == "list_docs":
            result = rag_store.list_documents(
                collection=str(params.get("collection") or ""),
                q=str(params.get("q") or ""),
                limit=int(params.get("limit") or 50),
                offset=int(params.get("offset") or 0))
        elif op == "get_doc":
            result = rag_store.get_document(
                int(params.get("document_id") or 0),
                max_chars=int(params.get("max_chars") or 20000))
        elif op == "ingest_text":
            result = rag_store.ingest(
                content=str(params.get("content") or ""),
                title=str(params.get("title") or ""),
                collection=str(params.get("collection") or "general"),
                source=str(params.get("source") or ""),
                metadata=params.get("metadata") if isinstance(params.get("metadata"), dict) else None,
                overwrite=bool(params.get("overwrite")))
        elif op == "ingest_file":
            result = rag_store.ingest_path(
                str(params.get("path") or ""),
                title=str(params.get("title") or ""),
                collection=str(params.get("collection") or "general"),
                overwrite=bool(params.get("overwrite")))
        elif op == "delete_doc":
            result = rag_store.delete_document(int(params.get("document_id") or 0))
        elif op == "feedback":
            result = rag_store.feedback(
                query=str(params.get("query") or ""),
                answer=str(params.get("answer") or ""),
                rating=str(params.get("rating") or ""),
                correction=str(params.get("correction") or ""),
                collection=str(params.get("collection") or "feedback"))
        else:
            return _fail(f"未知 op: {op!r}。可用: search/status/collections/list_docs/"
                         "get_doc/ingest_text/ingest_file/delete_doc/feedback", 2)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        return _fail(str(exc), 2)
    except Exception as exc:
        return _fail(f"{type(exc).__name__}: {exc}")

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
