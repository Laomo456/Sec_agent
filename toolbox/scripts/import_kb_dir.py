"""把一个目录批量收录进 RAG 知识库(供模型侧 rag 工具卡 / 知识库 UI 检索)。

用法:
  python scripts/import_kb_dir.py <目录> [--collection ctf] [--exts .md,.py]

- 扩展名白名单默认取 rag_config.yaml 的 ingest.allowed_extensions(这里再收窄到文本类)
- title = 相对目录的路径(文章多、重名多,裸文件名不够定位);source = 绝对路径
  → 检索证据卡里的 source 直接就是 file_read 可用的原始文件位置
- 幂等:同 collection 同内容哈希已存在则跳过(status=exists),重复跑不产生脏数据
- 二进制/超大文件按配置跳过,不报错
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rag_store  # noqa: E402

# 收窄到真正的文本知识;config 里的白名单还含 .csv/.html 等,按需 --exts 扩
DEFAULT_EXTS = {".md", ".markdown", ".txt", ".py", ".js"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="要收录的目录(递归)")
    ap.add_argument("--collection", default="general", help="知识库分区名")
    ap.add_argument("--exts", default=",".join(sorted(DEFAULT_EXTS)),
                    help="逗号分隔的扩展名白名单")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        sys.exit(2)
    exts = {e if e.startswith(".") else "." + e for e in args.exts.split(",") if e.strip()}
    cfg = rag_store.config()
    max_bytes = int(cfg["ingest"].get("max_file_bytes", 10 * 1024 * 1024))

    ok = exists = skipped = failed = chunks = 0
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts)
    for i, p in enumerate(files, 1):
        rel = p.relative_to(root).as_posix()
        tag = f"[{i}/{len(files)}]"
        if p.stat().st_size > max_bytes:
            print(f"{tag} SKIP 超限 {rel}")
            skipped += 1
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            r = rag_store.ingest(
                content=text, title=rel, collection=args.collection,
                source=str(p), path=rel, cfg=cfg,
            )
        except ValueError as e:              # 空文件等
            print(f"{tag} SKIP {rel}: {e}")
            skipped += 1
            continue
        except Exception as e:               # 单文件失败不中断整批
            print(f"{tag} FAIL  {rel}: {e}")
            failed += 1
            continue
        if r.get("status") == "exists":
            exists += 1
        else:
            ok += 1
            chunks += int(r.get("chunks", 0))
            if ok % 100 == 0:
                print(f"{tag} … 已入库 {ok} 篇 / {chunks} 块")
    print(f"完成: 新入库 {ok} 篇({chunks} 块), 已存在 {exists}, 跳过 {skipped}, 失败 {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
