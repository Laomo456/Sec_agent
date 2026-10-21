r"""fw_unpack 固件解包卡:op 分发 → SquashFS(含厂商 LZMA 变体)解析。

契约(runner.py):argv[1] = 参数 JSON(op 顶层 + params 嵌套);stdout 打可读文本;
退出码 0=ok,2=参数/校验错,1=解析失败。纯 stdlib 零依赖。

实证布局(源自 bin1.bin 逆向,全部泛化为自适配):
  - 块载荷可能是 [props u8][dict u32][LZMA1 流],2 字节元数据头压缩标志与数据块
    bit24 均不可靠 → payload() 先试 LZMA 再 deflate 再当裸数据;
  - frag_table_start 指向 u64 索引数组,索引才指向 fragment 元数据块;
  - file nblk: 带 fragment 时 = size // block_size(尾块在 fragment 里);
  - dir inode listing 位置: f1*8192+off 越界时回退 8192+off(3927 类厂商魔数);
  - root_inode u64 块字段: 命中某元数据块的压缩偏移 → 按压缩偏移定位,否则按块号。
"""
import difflib
import json
import lzma
import os
import re
import struct
import sys
import zlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WS_ROOT = Path(__file__).resolve().parents[2] / "workspace"
MAX_MAGIC_SCAN = 64 * 1024 * 1024      # 魔数扫描上限(64MB 内的固件够用)
TREE_LINES_CAP = 400                   # tree 输出截断
VALID_T = {1, 2, 3, 4, 5, 6, 7, 8, 9}
SUSPICIOUS_RE = re.compile(r"backdoor|webshell|_install|\.sh4|gnomerc", re.I)


def resolve(p: str) -> Path:
    path = Path(p)
    if path.drive:
        return path
    return WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / path


class SqfsError(Exception):
    pass


def _lzma_props(body: bytes, max_out: int) -> bytes | None:
    if len(body) < 5 or body[0] >= 225:
        return None
    props = body[0]
    ds = struct.unpack("<I", body[1:5])[0]
    lc, r = props % 9, props // 9
    lp, pb = r % 5, r // 5
    try:
        out = lzma.decompress(body[5:], format=lzma.FORMAT_RAW,
                              filters=[{"id": lzma.FILTER_LZMA1,
                                        "dict_size": ds, "lc": lc,
                                        "lp": lp, "pb": pb}])
    except (lzma.LZMAError, struct.error):
        return None
    return out if 0 < len(out) <= max_out else None


def _payload(body: bytes, max_out: int) -> bytes:
    """厂商变体核心:压缩标志不可靠,按 LZMA → raw deflate → 裸数据 依次试。"""
    out = _lzma_props(body, max_out)
    if out is not None and len(out) <= max_out:
        return out
    try:
        out = zlib.decompress(body, wbits=-15)
        if 0 < len(out) <= max_out:
            return out
    except zlib.error:
        pass
    return body


class FwImage:
    """一个固件文件里的 SquashFS 镜像(全部解析在内存完成)。"""

    def __init__(self, data: bytes):
        self.data = data
        self.off = self._find_super()
        sq = data[self.off:]
        self.sq = sq                       # 表区偏移一律 sq 相对,读原文件才 +self.off
        (magic, inodes, mkfs, block_size, fragments, comp, block_log, flags,
         no_ids, s_major, s_minor, root_inode, bytes_used, id_table_start,
         xattr_start, inode_table_start, dir_table_start, frag_table_start,
         lookup_start) = struct.unpack("<5I6H8Q", sq[:96])
        if s_major != 4:
            raise SqfsError(f"只支持 squashfs 4.x(实测 v{s_major}.{s_minor}),"
                            f"v3 布局不同需要另走 sasquatch")
        if comp not in (1, 2):
            raise SqfsError(f"压缩算法 {comp} 不在支持面(zlib/lzma)")
        (self.inodes, self.block_size, self.fragments, self.comp,
         self.root_inode, self.bytes_used, self.inode_table_start,
         self.dir_table_start, self.frag_table_start) = (
            inodes, block_size, fragments, comp, root_inode, bytes_used,
            inode_table_start, dir_table_start, frag_table_start)
        # SquashFS 4.0 元数据块固定 8KB(与 block_log 无关);根定位按它换算
        self.meta_size = 8192
        self.blocks = self._meta_blocks()
        self._quirk_lzma = self._probe_quirk()
        self.frags = self._load_frags()
        self.inode_tab = self._chain(inode_table_start, dir_table_start)
        self.dir_tab = self._chain(dir_table_start,
                                   min(self.frag_idx_ends))
        self.nodes = self._walk()
        self.by_ino = {n["ino"]: n for n in self.nodes}
        self.dirs, self.dir_children = self._parse_dirs()
        self.root = self._find_root()

    # ── 定位与解块 ──

    def _find_super(self) -> int:
        if len(self.data) > MAX_MAGIC_SCAN:
            raise SqfsError(f"文件 {len(self.data) // 1048576}MB 超过魔数扫描上限,"
                            f"先用 file_edit 切片")
        hits = []
        for m in re.finditer(re.escape(b"hsqs"), self.data):
            off = m.start()
            if off + 96 > len(self.data):
                continue
            (magic, inodes, _mkfs, bs, frags, comp, blog, *_r) = \
                struct.unpack("<5I6H8Q", self.data[off:off + 96])[:7]
            if not (0 < inodes <= 2_000_000 and 4096 <= bs <= 1 << 20
                    and 12 <= blog <= 20 and (1 << blog) == bs
                    and comp in (1, 2)):
                continue
            hits.append(off)
        if not hits:
            raise SqfsError("未找到有效 SquashFS 魔数(hsqs)——加密固件或外壳格式"
                            "(TRX/cFE)不在支持面;先用 packer_detect 看看类型")
        if len(hits) > 1:
            raise SqfsError(f"发现 {len(hits)} 个候选超级块 @{[hex(h) for h in hits]},"
                            f"暂不支持多分区自动选择")
        return hits[0]

    def _meta_hdr(self, off: int) -> tuple[bytes, int]:
        hdr = struct.unpack("<H", self.sq[off:off + 2])[0]
        size = hdr & 0x7FFF
        if off + 2 + size > len(self.sq):
            raise SqfsError(f"元数据块 @{hex(off)} 越界——镜像截断?")
        return _payload(self.sq[off + 2:off + 2 + size],
                        self.meta_size * 2 + 16), off + 2 + size

    def _chain(self, start: int, end: int) -> bytes:
        out, off = b"", start
        while off < end:
            blk, off = self._meta_hdr(off)
            out += blk
        return out

    def _meta_blocks(self) -> list[int]:
        offs, off = [], self.inode_table_start
        while off < self.dir_table_start:
            offs.append(off)
            hdr = struct.unpack("<H", self.sq[off:off + 2])[0]
            off += 2 + (hdr & 0x7FFF)
        return offs

    def _probe_quirk(self) -> bool:
        """厂商变体信号:首个元数据块头部说未压缩,但 LZMA 能解。"""
        hdr = struct.unpack("<H", self.sq[self.inode_table_start:
                                          self.inode_table_start + 2])[0]
        raw = self.sq[self.inode_table_start + 2:
                      self.inode_table_start + 2 + (hdr & 0x7FFF)]
        return not (hdr & 0x8000) and _lzma_props(raw, self.meta_size * 2 + 16) is not None

    def _load_frags(self) -> list[tuple[int, int]]:
        n_idx = (self.fragments * 16 + 8191) // 8192
        idx = struct.unpack(f"<{n_idx}Q",
                            self.sq[self.frag_table_start:
                                    self.frag_table_start + 8 * n_idx])
        self.frag_idx_ends = list(idx)
        raw = b"".join(self._meta_hdr(i)[0] for i in idx)
        return [(struct.unpack("<Q", raw[i:i + 8])[0],
                 struct.unpack("<I", raw[i + 8:i + 12])[0])
                for i in range(0, self.fragments * 16, 16)]

    # ── 数据读取 ──

    def data_block(self, abs_off: int, bsize: int) -> bytes:
        n = bsize & ~(1 << 24) if (bsize & (1 << 24)) else bsize
        return _payload(self.data[abs_off:abs_off + n],
                        self.block_size + 65536)

    def read_file(self, node: dict) -> bytes:
        out, off = b"", self.off + node["sb"]
        for bsz in node["bl"]:
            out += self.data_block(off, bsz)
            off += bsz & ~(1 << 24) if (bsz & (1 << 24)) else bsz
        if node["frag"] != 0xFFFFFFFF and node["size"] > len(out):
            fstart, fsize = self.frags[node["frag"]]
            fdata = self.data_block(self.off + fstart, fsize)
            out += fdata[node["foff"]:node["foff"] + (node["size"] - len(out))]
        return out[:node["size"]]

    # ── inode 走表 ──

    def _listing_pos(self, f1: int, offset: int) -> int:
        cand = f1 * 8192 + offset
        if cand < len(self.dir_tab):
            return cand
        return 8192 + offset          # 厂商魔数(如 3927)= 第 2 块基址

    def _walk(self) -> list[dict]:
        tab = self.inode_tab
        nodes, pos = [], 0
        while pos < len(tab):
            got = self._try_inode(tab, pos)
            if not got:
                raise SqfsError(f"inode 走表失败 @{pos}/{len(tab)}"
                                f"(已解析 {len(nodes)})——未知布局变体")
            node, end = got
            if end <= pos:
                raise SqfsError(f"inode @{pos} 长度为 0")
            node["_len"] = end - pos          # _find_root 按解压位置索引要用
            nodes.append(node)
            pos = end
        return nodes

    def _try_inode(self, tab: bytes, pos: int) -> tuple[dict, int] | None:
        if pos + 16 > len(tab):
            return None
        t, mode = struct.unpack("<2H", tab[pos:pos + 4])
        if t not in VALID_T or mode & ~0o17777:   # 允许 S_IFMT 类型位
            return None
        ino = struct.unpack("<I", tab[pos + 12:pos + 16])[0]
        if not (0 < ino <= self.inodes + 64):
            return None
        mtime = struct.unpack("<I", tab[pos + 8:pos + 12])[0]
        q = pos + 16
        if t == 2:
            if q + 16 > len(tab):
                return None
            sb, frag, foff, fsz = struct.unpack("<4I", tab[q:q + 16])
            if sb > self.bytes_used or fsz > 0x8000000:
                return None
            if frag != 0xFFFFFFFF and frag >= self.fragments:
                return None
            nblk = fsz // self.block_size if frag != 0xFFFFFFFF else \
                (fsz + self.block_size - 1) // self.block_size
            if q + 16 + 4 * nblk > len(tab):
                return None
            bl = list(struct.unpack(f"<{nblk}I", tab[q + 16:q + 16 + 4 * nblk]))
            for b in bl:
                if b > self.block_size + 65536 and not (b & (1 << 24)):
                    return None
            return {"t": "file", "sb": sb, "frag": frag, "foff": foff,
                    "size": fsz, "bl": bl, "ino": ino, "mtime": mtime}, \
                q + 16 + 4 * nblk
        if t == 3:
            if q + 8 > len(tab):
                return None
            _nlink, slen = struct.unpack("<2I", tab[q:q + 8])
            if slen > 512 or q + 8 + slen > len(tab):
                return None
            if not all(32 <= c < 127 for c in tab[q + 8:q + 8 + slen]):
                return None
            return {"t": "link", "target": tab[q + 8:q + 8 + slen].decode(),
                    "ino": ino, "mtime": mtime}, q + 8 + slen
        if t == 1:
            if q + 16 > len(tab):
                return None
            f1, _nl, f3, _f4 = struct.unpack("<4I", tab[q:q + 16])
            size = f3 & 0xFFFF
            start = self._listing_pos(f1, f3 >> 16)
            if start > len(self.dir_tab) or size > len(self.dir_tab) + 3:
                return None
            return {"t": "dir", "start": start, "size": size,
                    "ino": ino, "mtime": mtime}, q + 16
        if t in (4, 5, 6, 7):
            if q + 8 > len(tab):
                return None
            return {"t": "dev", "ino": ino, "mtime": mtime}, q + 8
        return {"t": f"type{t}", "ino": ino, "mtime": mtime}, q + 4

    # ── dir 表与根 ──

    def _parse_dirs(self) -> tuple[dict, dict]:
        dt = self.dir_tab
        dirs, children = {}, {}
        p = 0
        while p + 12 <= len(dt):
            count, _sb, base_ino = struct.unpack("<3I", dt[p:p + 12])
            if count > 254:
                break
            hp = p
            p += 12
            ents, ok = [], True
            for _ in range(count + 1):
                if p + 8 > len(dt):
                    ok = False
                    break
                _doff, delta, etype, nsz = struct.unpack("<4H", dt[p:p + 8])
                if nsz > 255:
                    ok = False
                    break
                nm = dt[p + 8:p + 8 + nsz + 1].decode("utf-8", "replace")
                child = base_ino + (delta - 65536 if delta > 32767 else delta)
                ents.append(nm)
                children[(hp, nm)] = (child, etype)
                p += 8 + nsz + 1
            if not ok:
                break
            dirs[hp] = ents
        return dirs, children

    def list_dir(self, node: dict) -> list[tuple[str, dict]]:
        out = []
        for hp in sorted(self.dirs):
            if node["start"] <= hp < node["start"] + node["size"] - 3:
                for nm in self.dirs[hp]:
                    if nm in (".", ".."):
                        continue
                    child = self.dir_children.get((hp, nm))
                    if child and child[0] in self.by_ino:
                        out.append((nm, self.by_ino[child[0]]))
        return out

    def _find_root(self) -> dict:
        # 自适配:root_inode 块字段可能是块号,也可能是元数据块压缩偏移(厂商)
        rb_off, root_off = self.root_inode >> 16, self.root_inode & 0xFFFF
        by_pos = {}
        pos = 0
        for n in self.nodes:
            by_pos[pos] = n
            pos += n["_len"]
        for i, o in enumerate(self.blocks):
            if o - self.inode_table_start == rb_off:
                cand = by_pos.get(i * self.meta_size + root_off)
                if cand and cand["t"] == "dir":
                    return cand
        cand = by_pos.get(rb_off * self.meta_size + root_off)
        if cand and cand["t"] == "dir":
            return cand
        # 兜底:listing 同时含 bin+etc 的目录
        for n in self.nodes:
            if n["t"] != "dir":
                continue
            names = set()
            for hp in sorted(self.dirs):
                if n["start"] <= hp < n["start"] + n["size"] - 3:
                    names.update(self.dirs[hp])
            if {"bin", "etc"} <= names:
                return n
        raise SqfsError("定位根 inode 失败")

    # ── 树 ──

    def tree(self) -> dict[str, dict]:
        out: dict[str, dict] = {}

        def rec(node, prefix, depth=0):
            if depth > 12:
                return
            for nm, ch in self.list_dir(node):
                path = prefix + nm
                if path in out:
                    continue
                out[path] = ch
                if ch["t"] == "dir":
                    rec(ch, path + "/", depth + 1)

        rec(self.root, "/")
        return out


def _try_open(file_param: str) -> tuple[Path, FwImage]:
    f = resolve(file_param)
    if not f.is_file():
        print(f"文件不存在: {f}", file=sys.stderr)
        sys.exit(2)
    try:
        img = FwImage(f.read_bytes())
    except SqfsError as e:
        print(f"解析失败: {e}", file=sys.stderr)
        sys.exit(1)
    return f, img


def op_probe(p: dict) -> None:
    f, img = _try_open(p["file"])
    tree = img.tree()
    n_files = sum(1 for v in tree.values() if v["t"] == "file")
    n_links = sum(1 for v in tree.values() if v["t"] == "link")
    top = sorted(nm for nm, _ in img.list_dir(img.root))
    print(f"固件: {f}  ({f.stat().st_size:,}B)")
    print(f"SquashFS 偏移: {hex(img.off)}  版本 4.0  压缩 "
          f"{'lzma' if img.comp == 2 else 'zlib'}  块大小 {img.block_size:,}")
    print(f"inode {img.inodes} (走表 {len(img.nodes)})  fragment {img.fragments}"
          f"  bytes_used {hex(img.bytes_used)}")
    print(f"厂商 LZMA 变体: {'★ 是(标志不可靠,已自动按 [props][dict][LZMA1] 解)'
          if img._quirk_lzma else '否'}")
    print(f"目录树: {len(tree)} 项 = {n_files} 文件 + {n_links} 链接 "
          f"+ {sum(1 for v in tree.values() if v['t'] == 'dev')} 设备")
    print(f"顶层: {' '.join(top)}")
    sus = [(k, v.get("size")) for k, v in tree.items()
           if SUSPICIOUS_RE.search(k)]
    print(f"可疑路径: {sus if sus else '(无 backdoor/_install 类命中)'}")
    shadow = tree.get("/etc/shadow")
    if shadow and shadow["t"] == "file":
        hashes = re.findall(rb"root:[^:]*", img.read_file(shadow))
        val = hashes[0][:80].decode("ascii", "replace") if hashes \
            else "(root 无哈希)"
        print(f"/etc/shadow: {val}")
    print("\n下一步: tree 看全量目录树 / extract 落盘拿文件")


def op_tree(p: dict) -> None:
    _f, img = _try_open(p["file"])
    tree = img.tree()
    lines = []
    for path in sorted(tree):
        n = tree[path]
        if n["t"] == "file":
            lines.append(f"{n['size']:>10,}  {path}")
        elif n["t"] == "link":
            lines.append(f"{'->':>10}  {path} -> {n['target']}")
        elif n["t"] == "dir":
            lines.append(f"{'<dir>':>10}  {path}/")
        else:
            lines.append(f"{'<dev>':>10}  {path}")
    cap = lines[:TREE_LINES_CAP]
    print("\n".join(cap))
    if len(lines) > TREE_LINES_CAP:
        print(f"...(共 {len(lines)} 行,截断至 {TREE_LINES_CAP};"
              f"全量清单用 extract 的 listing.txt)")


def op_extract(p: dict) -> None:
    f, img = _try_open(p["file"])
    out = resolve(p["output"]) if p.get("output") else \
        WS_ROOT / os.environ.get("AGENT_SESSION", "default") / "files" / \
        (f.name + ".sqfs")
    out.mkdir(parents=True, exist_ok=True)
    tree = img.tree()
    bad, written, links = 0, 0, 0
    listing = []
    for path in sorted(tree):
        node = tree[path]
        rel = path.lstrip("/")
        if node["t"] == "file":
            listing.append(f"{node['size']:>10,}  {path}")
            try:
                content = img.read_file(node)
            except Exception as e:
                bad += 1
                print(f"  !! {path}: {type(e).__name__} {e}", file=sys.stderr)
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_bytes(content)
                written += 1
            except OSError as e:
                bad += 1
                print(f"  !! {path}: {e}", file=sys.stderr)
        elif node["t"] == "link":
            listing.append(f"{'->':>10}  {path} -> {node['target']}")
            dest = out / (rel + ".lnk")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_text(node["target"], encoding="utf-8")
                links += 1
            except OSError:
                bad += 1
        else:
            listing.append(f"{'<dev>':>10}  {path}")
    listing_file = out / "listing.txt"
    listing_file.write_text("\n".join(listing) + "\n", encoding="utf-8")
    sus = [(k, tree[k].get("size")) for k in sorted(tree)
           if SUSPICIOUS_RE.search(k)]
    print(f"提取完成 → {out}")
    print(f"文件 {written} + 链接 {links} + 设备节点跳过, 失败 {bad}")
    print(f"全量清单: {listing_file}")
    if sus:
        print(f"可疑路径: {sus}")
        print("(注意: 样本文件可能被 Windows Defender 按名没收,信息以 listing.txt 为准)")


OPS = {"probe": op_probe, "tree": op_tree, "extract": op_extract}


def main() -> int:
    try:
        req = json.loads(sys.argv[1])
    except (IndexError, json.JSONDecodeError) as e:
        print(f"参数必须是 JSON 对象: {e}", file=sys.stderr)
        return 2
    op = str(req.get("op") or "")
    params = req.get("params") or {}
    if not isinstance(params, dict):
        print("params 必须是对象", file=sys.stderr)
        return 2
    if op not in OPS:
        near = difflib.get_close_matches(op, OPS, n=3)
        hint = f" 是不是想用: {'/'.join(near)}" if near else \
            f"。可用操作: {'/'.join(OPS)}"
        print(f"未知 op {op!r}{hint}", file=sys.stderr)
        return 2
    if not str(params.get("file") or "").strip():
        print("params.file 不能为空", file=sys.stderr)
        return 2
    try:
        OPS[op](params)
    except BrokenPipeError:
        pass
    except struct.error as e:
        print(f"解析失败(结构错位): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
