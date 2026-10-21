"""Generic local RAG store for toolbox tools.

The default backend is SQLite FTS5, with a small CPU token scorer fallback.
Embeddings are optional: configure a local OpenAI-compatible HTTP endpoint or
sentence-transformers. Collections are generic names, not security-specific.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "rag_config.yaml"
WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def config() -> dict:
    raw_path = Path(os.environ.get("RAG_CONFIG", str(DEFAULT_CONFIG)))
    raw = _load_yaml(raw_path)
    result = {
        "storage": {"data_dir": "workspace/rag", "database": "rag.sqlite3"},
        "search": {
            "mode": "direct", "top_k": 5, "chunk_size": 1200,
            "chunk_overlap": 160, "embedding_fallback": "direct",
        },
        "embedding": {
            "enabled": False, "provider": "none", "model": "",
            "endpoint": "http://127.0.0.1:8000/v1/embeddings",
            "api_key_env": "RAG_EMBEDDING_API_KEY", "timeout": 30,
        },
        "ingest": {
            "max_file_bytes": 10 * 1024 * 1024,
            "allowed_extensions": [
                ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml",
                ".csv", ".log", ".c", ".h", ".cpp", ".hpp", ".py", ".js",
                ".ts", ".java", ".go", ".rs", ".html", ".htm", ".xml",
            ],
        },
    }
    if isinstance(raw, dict):
        for section, values in raw.items():
            if isinstance(values, dict) and isinstance(result.get(section), dict):
                result[section].update(values)
    overrides = {
        "RAG_DATA_DIR": ("storage", "data_dir"),
        "RAG_DATABASE": ("storage", "database"),
        "RAG_SEARCH_MODE": ("search", "mode"),
        "RAG_EMBEDDING_PROVIDER": ("embedding", "provider"),
        "RAG_EMBEDDING_ENDPOINT": ("embedding", "endpoint"),
        "RAG_EMBEDDING_MODEL": ("embedding", "model"),
    }
    for env, (section, key) in overrides.items():
        if os.environ.get(env):
            result[section][key] = os.environ[env]
    if os.environ.get("RAG_EMBEDDING_ENABLED") is not None:
        result["embedding"]["enabled"] = os.environ["RAG_EMBEDDING_ENABLED"].lower() in {
            "1", "true", "yes", "on"
        }
    return result


def data_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or config()
    raw = Path(str(cfg["storage"].get("data_dir", "workspace/rag")))
    if not raw.is_absolute():
        raw = ROOT / raw
    raw.mkdir(parents=True, exist_ok=True)
    return raw


def db_path(cfg: dict | None = None) -> Path:
    cfg = cfg or config()
    return data_dir(cfg) / str(cfg["storage"].get("database", "rag.sqlite3"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(cfg: dict | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path(cfg)), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    _init(con)
    return con


def _init(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          collection TEXT NOT NULL,
          title TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          path TEXT NOT NULL DEFAULT '',
          content TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(collection, content_hash)
        );
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          chunk_index INTEGER NOT NULL,
          content TEXT NOT NULL,
          embedding_json TEXT NOT NULL DEFAULT '',
          UNIQUE(document_id, chunk_index)
        );
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          collection TEXT NOT NULL,
          query TEXT NOT NULL,
          answer TEXT NOT NULL DEFAULT '',
          rating TEXT NOT NULL DEFAULT '',
          correction TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
        CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
        """
    )
    _init_fts(con)


def _init_fts(con: sqlite3.Connection) -> None:
    """FTS5 全文索引。优先 trigram 分词器——unicode61 会把整段连续中文当成一个
    token,中文直检基本失效;trigram(SQLite 3.34+)天然支持 CJK 子串匹配。
    旧 unicode61 表自动 DROP 并从 chunks 重建(FTS 本就冗余,数据无损失);
    老 SQLite 不支持 trigram 时回退 unicode61。当前分词器由 status() 暴露。"""
    tokenizer = "trigram"
    try:
        con.execute("CREATE VIRTUAL TABLE temp._fts_probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE temp._fts_probe")
    except sqlite3.OperationalError:
        con.rollback()
        tokenizer = "unicode61"
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='chunks_fts'"
    ).fetchone()
    existing = (row["sql"] if row else "").replace('"', "'")
    if existing and f"tokenize='{tokenizer}'" in existing:
        return
    if existing:
        con.execute("DROP TABLE chunks_fts")
    try:
        con.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(content, title, collection, "
            f"tokenize='{tokenizer}')"
        )
    except sqlite3.OperationalError:
        con.rollback()
        return
    con.execute(
        "INSERT INTO chunks_fts(rowid,content,title,collection) "
        "SELECT c.id,c.content,d.title,d.collection FROM chunks c "
        "JOIN documents d ON d.id=c.document_id"
    )
    con.commit()


def fts_available(con: sqlite3.Connection) -> bool:
    try:
        con.execute("SELECT 1 FROM chunks_fts LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def fts_tokenizer(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='chunks_fts'"
    ).fetchone()
    sql = row["sql"] if row else ""
    match = re.search(r"tokenize\s*=\s*'(\w+)'", sql)
    return match.group(1) if match else ""


def _tokens(text: str) -> list[str]:
    return [x.lower() for x in WORD_RE.findall(text or "")]


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    size = max(128, int(size))
    overlap = max(0, min(int(overlap), size - 1))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
            if boundary > start + size // 2:
                end = boundary
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [x for x in chunks if x]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embedding(text: str, cfg: dict) -> list[float] | None:
    ecfg = cfg.get("embedding", {})
    if not ecfg.get("enabled") or str(ecfg.get("provider", "none")).lower() in {
        "", "none", "disabled"
    }:
        return None
    provider = str(ecfg.get("provider", "none")).lower()
    if provider == "local_http":
        payload: dict[str, Any] = {"input": text}
        if ecfg.get("model"):
            payload["model"] = ecfg["model"]
        headers = {"Content-Type": "application/json"}
        key_env = str(ecfg.get("api_key_env", "RAG_EMBEDDING_API_KEY"))
        if os.environ.get(key_env):
            headers["Authorization"] = "Bearer " + os.environ[key_env]
        request = urllib.request.Request(
            str(ecfg.get("endpoint")),
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=float(ecfg.get("timeout", 30))) as response:
            obj = json.loads(response.read().decode("utf-8"))
        data = obj.get("data") or []
        vector = data[0].get("embedding") if data and isinstance(data[0], dict) else None
        return [float(x) for x in vector] if vector else None
    if provider == "sentence_transformers":
        from sentence_transformers import SentenceTransformer
        model_name = str(ecfg.get("model") or "all-MiniLM-L6-v2")
        model = getattr(_embedding, "_model", None)
        if model is None or getattr(_embedding, "_model_name", None) != model_name:
            model = SentenceTransformer(model_name)
            _embedding._model, _embedding._model_name = model, model_name
        return [float(x) for x in model.encode(text, normalize_embeddings=True).tolist()]
    raise ValueError("unsupported embedding provider: " + provider)


def ingest(
    *, content: str, title: str = "", collection: str = "general",
    source: str = "", path: str = "", metadata: dict | None = None,
    overwrite: bool = False, cfg: dict | None = None,
) -> dict:
    cfg = cfg or config()
    collection = (collection or "general").strip() or "general"
    title = (title or Path(path).name or "Untitled").strip() or "Untitled"
    content = str(content or "")
    if not content.strip():
        raise ValueError("content is empty")
    digest = _hash(content)
    chunks = _chunk_text(
        content, cfg["search"].get("chunk_size", 1200),
        cfg["search"].get("chunk_overlap", 160),
    )
    now = _now()
    con = _connect(cfg)
    try:
        existing = con.execute(
            "SELECT id FROM documents WHERE collection=? AND content_hash=?",
            (collection, digest),
        ).fetchone()
        if existing and not overwrite:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE document_id=?",
                (existing["id"],),
            ).fetchone()
            return {
                "status": "exists", "document_id": existing["id"],
                "collection": collection, "chunks": row["n"],
            }
        if existing:
            con.execute("DELETE FROM documents WHERE id=?", (existing["id"],))
        cur = con.execute(
            "INSERT INTO documents(collection,title,source,path,content,content_hash,metadata_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                collection, title, source, path, content, digest,
                json.dumps(metadata or {}, ensure_ascii=False), now, now,
            ),
        )
        doc_id = cur.lastrowid
        has_fts = fts_available(con)
        for index, chunk in enumerate(chunks):
            vector_text = ""
            try:
                vector = _embedding(chunk, cfg)
                vector_text = json.dumps(vector, separators=(",", ":")) if vector else ""
            except Exception:
                if str(cfg["search"].get("mode", "direct")).lower() == "embedding":
                    raise
            chunk_cur = con.execute(
                "INSERT INTO chunks(document_id,chunk_index,content,embedding_json) VALUES (?,?,?,?)",
                (doc_id, index, chunk, vector_text),
            )
            if has_fts:
                con.execute(
                    "INSERT INTO chunks_fts(rowid,content,title,collection) VALUES (?,?,?,?)",
                    (chunk_cur.lastrowid, chunk, title, collection),
                )
        con.commit()
        return {
            "status": "ok", "document_id": doc_id, "collection": collection,
            "title": title, "chunks": len(chunks), "content_hash": digest,
        }
    finally:
        con.close()


def _result(row: sqlite3.Row, score: float, similarity: float | None = None) -> dict:
    output = {
        "document_id": row["document_id"], "chunk_id": row["id"],
        "chunk_index": row["chunk_index"], "title": row["title"],
        "collection": row["collection"], "source": row["source"],
        "path": row["path"], "content": row["content"], "score": round(score, 6),
    }
    if similarity is not None:
        output["similarity"] = round(similarity, 6)
    return output


def _direct_search(con: sqlite3.Connection, query: str, collection: str, top_k: int) -> list[dict]:
    rows: list[sqlite3.Row] = []
    if fts_available(con):
        terms = _tokens(query)
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms) or '""'
        sql = (
            "SELECT c.id,c.document_id,c.chunk_index,c.content,d.title,d.collection,"
            "d.source,d.path,bm25(chunks_fts) AS score FROM chunks_fts "
            "JOIN chunks c ON c.id=chunks_fts.rowid JOIN documents d ON d.id=c.document_id "
            "WHERE chunks_fts MATCH ?"
        )
        args: list[Any] = [match]
        if collection:
            sql += " AND d.collection=?"
            args.append(collection)
        sql += " ORDER BY score LIMIT ?"
        args.append(max(top_k, top_k * 4))
        try:
            rows = con.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        sql = (
            "SELECT c.id,c.document_id,c.chunk_index,c.content,d.title,d.collection,"
            "d.source,d.path FROM chunks c JOIN documents d ON d.id=c.document_id"
        )
        args = []
        if collection:
            sql += " WHERE d.collection=?"
            args.append(collection)
        candidates = con.execute(sql, args).fetchall()
        query_terms = _tokens(query)
        count = max(1, len(candidates))
        scored = []
        all_words = [_tokens(row["content"]) for row in candidates]
        for row, words in zip(candidates, all_words):
            if not words:
                continue
            score = sum(words.count(term) / len(words) for term in query_terms)
            if score:
                document_frequency = sum(term in words for words in all_words for term in query_terms)
                score *= math.log(1 + count / (1 + document_frequency))
                scored.append((score, row))
        rows = [row for _, row in sorted(scored, key=lambda pair: pair[0], reverse=True)[:max(top_k, top_k * 4)]]
    return [_result(row, float(row["score"]) if "score" in row.keys() else 0.0)
            for row in rows[:top_k]]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _embedding_search(con: sqlite3.Connection, query: str, collection: str, top_k: int, cfg: dict) -> list[dict]:
    vector = _embedding(query, cfg)
    if not vector:
        raise RuntimeError("embedding backend returned no vector")
    sql = (
        "SELECT c.id,c.document_id,c.chunk_index,c.content,c.embedding_json,d.title,"
        "d.collection,d.source,d.path FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE c.embedding_json != ''"
    )
    args = []
    if collection:
        sql += " AND d.collection=?"
        args.append(collection)
    scored = []
    for row in con.execute(sql, args):
        try:
            similarity = _cosine(vector, json.loads(row["embedding_json"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        scored.append((similarity, row))
    return [_result(row, similarity, similarity)
            for similarity, row in sorted(scored, key=lambda pair: pair[0], reverse=True)[:top_k]]


def _truncate_results(results: list[dict], max_chars: int) -> list[dict]:
    """证据卡 content 截断,保护模型上下文(normalize 输出面预算 1500 est-tokens)。
    max_chars<=0 表示不截断。"""
    if max_chars <= 0:
        return results
    for item in results:
        content = str(item.get("content") or "")
        if len(content) > max_chars:
            item["content"] = content[:max_chars].rstrip() + " …"
            item["truncated"] = True
    return results


def search(query: str, *, collection: str = "", mode: str = "", top_k: int = 0,
           max_chars: int = 0, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is empty")
    mode = (mode or str(cfg["search"].get("mode", "direct"))).lower()
    top_k = max(1, min(int(top_k or cfg["search"].get("top_k", 5)), 50))
    con = _connect(cfg)
    try:
        if mode == "direct":
            results = _direct_search(con, query, collection, top_k)
        elif mode == "embedding":
            try:
                results = _embedding_search(con, query, collection, top_k, cfg)
            except Exception as exc:
                if str(cfg["search"].get("embedding_fallback", "direct")) != "direct":
                    raise RuntimeError("embedding search unavailable: " + str(exc))
                results = _direct_search(con, query, collection, top_k)
                return {
                    "status": "ok", "mode": "direct_fallback", "query": query,
                    "collection": collection, "results": _truncate_results(results, max_chars),
                    "count": len(results), "warning": str(exc),
                }
        elif mode == "hybrid":
            direct = _direct_search(con, query, collection, max(top_k, 10))
            try:
                embedded = _embedding_search(con, query, collection, max(top_k, 10), cfg)
            except Exception:
                embedded = []
            merged: dict[int, dict] = {}
            for rank, item in enumerate(direct):
                merged[item["chunk_id"]] = {**item, "score": 1.0 / (rank + 1)}
            for rank, item in enumerate(embedded):
                target = merged.setdefault(item["chunk_id"], item.copy())
                target["score"] = target.get("score", 0.0) + 1.0 / (rank + 1)
                if "similarity" in item:
                    target["similarity"] = item["similarity"]
            results = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
        else:
            raise ValueError("mode must be direct, embedding, or hybrid")
        results = _truncate_results(results, max_chars)
        return {
            "status": "ok", "mode": mode, "query": query, "collection": collection,
            "results": results, "count": len(results),
        }
    finally:
        con.close()


def status(cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    con = _connect(cfg)
    try:
        collections = [
            dict(row) for row in con.execute(
                "SELECT collection,COUNT(*) AS documents,"
                "(SELECT COUNT(*) FROM chunks c WHERE c.document_id IN "
                "(SELECT id FROM documents WHERE collection=d.collection)) AS chunks "
                "FROM documents d GROUP BY collection ORDER BY collection"
            )
        ]
        documents = con.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        chunks = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return {
            "status": "ok", "database": str(db_path(cfg)), "fts5": fts_available(con),
            "fts_tokenizer": fts_tokenizer(con),
            "documents": documents, "chunks": chunks, "collections": collections,
            "config": cfg,
        }
    finally:
        con.close()


def feedback(
    *, query: str, answer: str = "", rating: str = "", correction: str = "",
    collection: str = "feedback", metadata: dict | None = None, cfg: dict | None = None,
) -> dict:
    cfg = cfg or config()
    con = _connect(cfg)
    try:
        cursor = con.execute(
            "INSERT INTO feedback(collection,query,answer,rating,correction,metadata_json,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                collection or "feedback", query, answer, rating, correction,
                json.dumps(metadata or {}, ensure_ascii=False), _now(),
            ),
        )
        feedback_id = cursor.lastrowid
        con.commit()
    finally:
        con.close()
    indexed_id = None
    if correction.strip():
        result = ingest(
            content=correction, title="Feedback: " + query[:80],
            collection=collection or "feedback", source="rag_feedback",
            metadata={"feedback_id": feedback_id}, cfg=cfg,
        )
        indexed_id = result.get("document_id")
    return {"status": "ok", "feedback_id": feedback_id, "indexed_document_id": indexed_id}


def _session_root() -> Path:
    return ROOT / "workspace" / os.environ.get("AGENT_SESSION", "default")


def resolve_workspace_path(value: str) -> Path:
    raw = Path(value)
    workspace = ROOT / "workspace"
    session = _session_root()
    allowed = [session / "files", session, workspace]
    if raw.is_absolute():
        candidate = raw.resolve()
        if not any(candidate == root.resolve() or root.resolve() in candidate.parents for root in allowed):
            raise ValueError("path must be inside the workspace")
        return candidate
    if any(part == ".." for part in raw.parts):
        raise ValueError("path traversal is not allowed")
    for root in (session / "files", session, workspace):
        candidate = (root / raw).resolve()
        if candidate.is_file() and (candidate == root.resolve() or root.resolve() in candidate.parents):
            return candidate
    raise FileNotFoundError(value)


def ingest_path(
    path: str, *, title: str = "", collection: str = "general", source: str = "",
    metadata: dict | None = None, overwrite: bool = False, cfg: dict | None = None,
) -> dict:
    cfg = cfg or config()
    resolved = resolve_workspace_path(path)
    max_bytes = int(cfg["ingest"].get("max_file_bytes", 10 * 1024 * 1024))
    if resolved.stat().st_size > max_bytes:
        raise ValueError("file exceeds max_file_bytes (" + str(max_bytes) + ")")
    allowed = {str(x).lower() for x in cfg["ingest"].get("allowed_extensions", [])}
    if allowed and resolved.suffix.lower() not in allowed:
        raise ValueError("unsupported file extension: " + resolved.suffix)
    text = resolved.read_text(encoding="utf-8", errors="replace")
    return ingest(
        content=text, title=title or resolved.name, collection=collection,
        source=source or str(resolved), path=str(path), metadata=metadata,
        overwrite=overwrite, cfg=cfg,
    )


# ═══════════════ 管理面(gateway 知识库 UI 用;模型侧工具只用上面) ═══════════════


def list_documents(collection: str = "", q: str = "", limit: int = 50,
                   offset: int = 0, cfg: dict | None = None) -> dict:
    """文档分页列表(不含正文)。q 在 title/source/path 上做 LIKE 过滤。"""
    cfg = cfg or config()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where, args = [], []
    if collection:
        where.append("collection=?")
        args.append(collection)
    if q:
        where.append("(title LIKE ? OR source LIKE ? OR path LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    tail = (" WHERE " + " AND ".join(where)) if where else ""
    con = _connect(cfg)
    try:
        total = con.execute(f"SELECT COUNT(*) AS n FROM documents{tail}", args).fetchone()["n"]
        rows = con.execute(
            "SELECT d.id,d.collection,d.title,d.source,d.path,length(d.content) AS size,"
            "d.created_at,d.updated_at,"
            "(SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunks "
            f"FROM documents d{tail} ORDER BY d.updated_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return {"status": "ok", "total": total,
                "documents": [dict(row) for row in rows]}
    finally:
        con.close()


def get_document(document_id: int, max_chars: int = 20000, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    con = _connect(cfg)
    try:
        row = con.execute("SELECT * FROM documents WHERE id=?", (int(document_id),)).fetchone()
        if not row:
            raise KeyError(f"document {document_id} not found")
        content = row["content"]
        truncated = False
        if max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars].rstrip() + " …"
            truncated = True
        chunks = con.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE document_id=?", (row["id"],)
        ).fetchone()["n"]
        return {
            "status": "ok", "id": row["id"], "collection": row["collection"],
            "title": row["title"], "source": row["source"], "path": row["path"],
            "content": content, "truncated": truncated, "chunks": chunks,
            "content_hash": row["content_hash"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
    finally:
        con.close()


def delete_document(document_id: int, cfg: dict | None = None) -> dict:
    """删文档;chunks 靠外键级联,FTS 冗余表按 rowid 同步删。"""
    cfg = cfg or config()
    con = _connect(cfg)
    try:
        row = con.execute(
            "SELECT id,title FROM documents WHERE id=?", (int(document_id),)
        ).fetchone()
        if not row:
            raise KeyError(f"document {document_id} not found")
        if fts_available(con):
            con.execute(
                "DELETE FROM chunks_fts WHERE rowid IN "
                "(SELECT id FROM chunks WHERE document_id=?)",
                (row["id"],),
            )
        con.execute("DELETE FROM documents WHERE id=?", (row["id"],))
        con.commit()
        return {"status": "ok", "deleted": row["id"], "title": row["title"]}
    finally:
        con.close()


_CONFIG_HEADER = """\
# RAG 知识库配置 —— 唯一真相源(gateway 知识库管理界面改的也是这个文件)。
# storage:   索引存放位置(默认 toolbox/workspace/rag/rag.sqlite3)
# search:    mode = direct(关键词,FTS5 trigram,中文子串可检) / embedding(向量) / hybrid(混合)
#            embedding_fallback = direct:向量后端不可用时自动回退关键词检索
# embedding: 默认关闭。开启方式二选一:
#            provider: local_http —— 指向本机 OpenAI 兼容端点,如
#              endpoint: http://127.0.0.1:11434/v1/embeddings  (Ollama)
#              model: bge-m3            (或多语 small 模型,CPU 可跑,不占 LLM 显存)
#            provider: sentence_transformers —— 本地加载,需 pip install sentence-transformers
# ingest:    max_file_bytes 单文件上限;allowed_extensions 允许入库的扩展名
# 环境变量覆盖:RAG_DATA_DIR / RAG_DATABASE / RAG_SEARCH_MODE /
#   RAG_EMBEDDING_PROVIDER / RAG_EMBEDDING_ENDPOINT / RAG_EMBEDDING_MODEL / RAG_EMBEDDING_ENABLED
"""


def save_config(cfg: dict, path: Path | None = None) -> dict:
    """把(部分)配置深合并到当前配置后整体写回 yaml。注释由头部模板重新生成。"""
    import yaml
    path = path or Path(os.environ.get("RAG_CONFIG", str(DEFAULT_CONFIG)))
    merged = config()
    if isinstance(cfg, dict):
        for section, values in cfg.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section].update(values)
    text = _CONFIG_HEADER + yaml.safe_dump(merged, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return {"status": "ok", "path": str(path)}
