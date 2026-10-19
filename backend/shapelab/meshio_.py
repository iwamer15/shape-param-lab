"""メッシュの読み書き（STL / Abaqus・CalculiX の .inp）。

.inp は節点（*NODE）と要素（*ELEMENT）を読み、表面の三角形を取り出す。
書き出しは元のファイルの *NODE の座標だけを書き換え、セット・材料・ステップなど他の行はそのまま残す
（要素のつながりが変わらないので、そのまま CAE に流せる）。

対応要素（D 始まりの熱伝導要素 DC3D… も同じ扱い）
  ソリッド: C3D4 / C3D10 / C3D8(R,I,H) / C3D20(R) / C3D6 / C3D15
  シェル・膜: S3 / STRI3 / M3D3 / S4(R) / M3D4 / S6 / STRI65 / S8(R) / M3D8 …（表面としてだけ扱う）
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class MeshError(Exception):
    """利用者に見せる日本語メッセージ付きのエラー。"""


# ---------------------------------------------------------------------------
# 要素の定義（Abaqus の節点順）
# ---------------------------------------------------------------------------

_TET_F = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]
_TET_E = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
_HEX_F = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
_HEX_E = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
_WED_F = [(0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)]
_WED_E = [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)]

# 反転チェック用の角の四面体（元の符号と比べるので向きの約束は問わない）
_TET_C = [(0, 1, 2, 3)]
_HEX_C = [(0, 1, 3, 4), (1, 2, 0, 5), (2, 3, 1, 6), (3, 0, 2, 7), (4, 7, 5, 0), (5, 4, 6, 1), (6, 5, 7, 2), (7, 6, 4, 3)]
_WED_C = [(0, 1, 2, 3), (1, 2, 0, 4), (2, 0, 1, 5), (3, 5, 4, 0), (4, 3, 5, 1), (5, 4, 3, 2)]


def _mids(pairs, start):
    return {p: start + i for i, p in enumerate(pairs)}


ETYPES = {
    "tet4": dict(n=4, faces=_TET_F, edges=_TET_E, mids=None, corner_tets=_TET_C, solid=True),
    "tet10": dict(n=10, faces=_TET_F, edges=_TET_E, mids=_mids(_TET_E, 4), corner_tets=_TET_C, solid=True),
    "hex8": dict(n=8, faces=_HEX_F, edges=_HEX_E, mids=None, corner_tets=_HEX_C, solid=True),
    "hex20": dict(n=20, faces=_HEX_F, edges=_HEX_E, mids=_mids(_HEX_E, 8), corner_tets=_HEX_C, solid=True),
    "wedge6": dict(n=6, faces=_WED_F, edges=_WED_E, mids=None, corner_tets=_WED_C, solid=True),
    "wedge15": dict(n=15, faces=_WED_F, edges=_WED_E, mids=_mids(_WED_E, 6), corner_tets=_WED_C, solid=True),
    "tri3": dict(n=3, faces=[(0, 1, 2)], edges=[(0, 1), (1, 2), (2, 0)], mids=None, corner_tets=[], solid=False),
    "tri6": dict(n=6, faces=[(0, 1, 2)], edges=[(0, 1), (1, 2), (2, 0)], mids=_mids([(0, 1), (1, 2), (2, 0)], 3),
                 corner_tets=[], solid=False),
    "quad4": dict(n=4, faces=[(0, 1, 2, 3)], edges=[(0, 1), (1, 2), (2, 3), (3, 0)], mids=None, corner_tets=[], solid=False),
    "quad8": dict(n=8, faces=[(0, 1, 2, 3)], edges=[(0, 1), (1, 2), (2, 3), (3, 0)],
                  mids=_mids([(0, 1), (1, 2), (2, 3), (3, 0)], 4), corner_tets=[], solid=False),
}


def abaqus_type(name: str) -> str | None:
    t = name.upper().strip()
    if t.startswith("DC3D"):
        t = t[1:]
    rules = [
        (r"C3D4", "tet4"), (r"C3D10", "tet10"), (r"C3D8", "hex8"), (r"C3D20", "hex20"),
        (r"C3D6", "wedge6"), (r"C3D15", "wedge15"),
        (r"(S3|STRI3|M3D3|DS3|SFM3D3)", "tri3"), (r"(S6|STRI65|M3D6|DS6|SFM3D6)", "tri6"),
        (r"(S4|M3D4|DS4|SFM3D4)", "quad4"), (r"(S8|M3D8|DS8|SFM3D8)", "quad8"),
    ]
    for pat, et in rules:
        if re.fullmatch(pat + r"[A-Z]*", t):
            return et
    return None


def _face_templates(et: str):
    """面ごとに、表面三角形のテンプレート（要素内の節点番号）を 正向き・逆向き の 2 通り作る。"""
    d = ETYPES[et]
    mids = d["mids"]

    def tris(c):
        if mids is None:
            return [(c[0], c[1], c[2])] if len(c) == 3 else [(c[0], c[1], c[2]), (c[0], c[2], c[3])]
        pair = lambda a, b: mids[(a, b)] if (a, b) in mids else mids[(b, a)]
        m = [pair(c[i], c[(i + 1) % len(c)]) for i in range(len(c))]
        if len(c) == 3:
            return [(c[0], m[0], m[2]), (m[0], c[1], m[1]), (m[2], m[1], c[2]), (m[0], m[1], m[2])]
        return [(c[0], m[0], m[3]), (m[0], c[1], m[1]), (m[1], c[2], m[2]), (m[2], c[3], m[3]),
                (m[0], m[1], m[2]), (m[0], m[2], m[3])]

    out = []
    for f in d["faces"]:
        rev = (f[0],) + tuple(reversed(f[1:]))
        out.append((f, tris(f), tris(rev)))
    return out


# ---------------------------------------------------------------------------
# 入れ物
# ---------------------------------------------------------------------------

@dataclass
class ElemBlock:
    etype: str
    conn: np.ndarray        # (E, n) 節点インデックス
    name: str = ""          # 元の要素タイプ名


@dataclass
class InpDoc:
    lines: list[str]
    node_lines: list[tuple[int, int, str, int]]   # (行番号, 節点インデックス, 元の節点番号, 座標の数)
    newline: str = "\n"
    encoding: str = "utf-8"


@dataclass
class MeshData:
    nodes: np.ndarray                 # (N, 3) float64
    tris: np.ndarray                  # (T, 3) 表面三角形（外向き）
    blocks: list[ElemBlock] = field(default_factory=list)
    source: str = "stl"               # stl / inp
    inp: InpDoc | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def solid_blocks(self):
        return [b for b in self.blocks if ETYPES[b.etype]["solid"]]


# ---------------------------------------------------------------------------
# STL
# ---------------------------------------------------------------------------

def read_stl(path: str | Path) -> MeshData:
    raw = Path(path).read_bytes()
    if len(raw) >= 84:
        n = struct.unpack("<I", raw[80:84])[0]
        is_bin = len(raw) == 84 + 50 * n
    else:
        is_bin = False
    if is_bin:
        rec = np.frombuffer(raw, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")], count=n, offset=84)
        v = rec["v"].astype(np.float64).reshape(-1, 3)
    else:
        txt = raw.decode("utf-8", errors="replace")
        nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", txt)
        if not nums:
            raise MeshError("STL を読めませんでした（三角形がありません）")
        v = np.array(nums, dtype=np.float64)
    if len(v) == 0:
        raise MeshError("STL に三角形がありません")
    nodes, tris = weld(v)
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 2] != tris[:, 0])]
    if signed_volume(nodes, tris) < 0:   # 内向きなら外向きに揃える
        tris = tris[:, ::-1].copy()
    return MeshData(nodes, tris, source="stl")


def weld(v: np.ndarray):
    """STL の三角形ごとの頂点を、同じ位置どうしでまとめる。"""
    span = float(np.ptp(v, axis=0).max()) or 1.0
    tol = span * 1e-7
    q = np.round(v / tol).astype(np.int64)
    uniq, inv = np.unique(q, axis=0, return_inverse=True)
    nodes = np.zeros((len(uniq), 3))
    np.add.at(nodes, inv.ravel(), v)
    cnt = np.bincount(inv.ravel(), minlength=len(uniq))
    return nodes / cnt[:, None], inv.reshape(-1, 3)


def write_stl(nodes: np.ndarray, tris: np.ndarray, path: str | Path) -> None:
    v = nodes[tris].astype(np.float64)
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(v), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, v
    with open(path, "wb") as f:
        f.write(b"binary stl".ljust(80, b" "))
        f.write(struct.pack("<I", len(v)))
        f.write(rec.tobytes())


def signed_volume(nodes, tris) -> float:
    p = nodes[tris]
    return float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6)


# ---------------------------------------------------------------------------
# INP
# ---------------------------------------------------------------------------

def _decode(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1"), "latin-1"


def _keyword(s: str) -> tuple[str, dict]:
    parts = [p.strip() for p in s.split(",")]
    opts = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            opts[k.strip().upper()] = v.strip()
        elif p:
            opts[p.upper()] = ""
    return re.sub(r"\s+", " ", parts[0].upper()), opts


def read_inp(path: str | Path) -> MeshData:
    raw = Path(path).read_bytes()
    text, enc = _decode(raw)
    newline = "\r\n" if "\r\n" in text[:5000] else "\n"
    lines = text.splitlines()
    index: dict[tuple[str, int], int] = {}
    coords: list[tuple[float, float, float]] = []
    node_lines: list[tuple[int, int, str, int]] = []
    raw_blocks: list[tuple[str, str, str, list[list[int]]]] = []   # (etype, 元の名前, part, rows)
    warnings: list[str] = []
    skipped: dict[str, int] = {}
    mode, part, etype, ename, need = None, "", None, "", 0
    pending: list[int] = []

    for no, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("**"):
            continue
        if s.startswith("*"):
            kw, opts = _keyword(s)
            mode = None
            if kw == "*NODE":
                mode = "node"
            elif kw == "*ELEMENT":
                ename = opts.get("TYPE", "")
                etype = abaqus_type(ename)
                if etype is None:
                    skipped[ename] = skipped.get(ename, 0) + 1
                    mode = "skip"
                else:
                    need = ETYPES[etype]["n"]
                    raw_blocks.append((etype, ename, part, []))
                    mode = "elem"
                    pending = []
            elif kw == "*PART":
                part = opts.get("NAME", "")
            elif kw == "*END PART":
                part = ""
            elif kw == "*INCLUDE":
                raise MeshError(f"*INCLUDE（別ファイル {opts.get('INPUT', '')} の読み込み）には未対応です。"
                                "1 つのファイルにまとめた .inp を使ってください。")
            elif kw == "*INSTANCE" and part == "":
                warnings.append("*INSTANCE の移動・回転は反映していません（部品の座標のまま扱います）")
            continue
        if mode == "node":
            vals = [x.strip() for x in s.split(",")]
            try:
                nid = int(vals[0])
                xyz = [float(x) for x in vals[1:4] if x != ""]
            except ValueError:
                raise MeshError(f"{no + 1} 行目の節点を読めません: {s[:60]}")
            nc = len(xyz)
            while len(xyz) < 3:
                xyz.append(0.0)
            key = (part, nid)
            if key in index:
                warnings.append(f"節点 {nid} が重複しています（後の定義を使用）")
                coords[index[key]] = tuple(xyz)
            else:
                index[key] = len(coords)
                coords.append(tuple(xyz))
            node_lines.append((no, index[key], vals[0], nc))
        elif mode == "elem":
            pending += [int(x) for x in s.split(",") if x.strip()]
            if len(pending) >= need + 1:
                raw_blocks[-1][3].append(pending[: need + 1])
                pending = []

    if not coords:
        raise MeshError("節点（*NODE）がありません")
    nodes = np.array(coords, dtype=np.float64)
    blocks: list[ElemBlock] = []
    missing = 0
    for et, en, pt, rows in raw_blocks:
        if not rows:
            continue
        conn = []
        for r in rows:
            try:
                conn.append([index[(pt, n)] for n in r[1:]])
            except KeyError:
                missing += 1
        if conn:
            blocks.append(ElemBlock(et, np.array(conn, dtype=np.int64), en))
    if missing:
        warnings.append(f"存在しない節点を参照する要素 {missing} 個を無視しました")
    for k, v in skipped.items():
        warnings.append(f"未対応の要素タイプ {k} は無視しました（{v} ブロック）")
    if not blocks:
        raise MeshError("対応している要素がありません（ソリッド C3D4/C3D10/C3D8/C3D20/C3D6/C3D15 やシェル S3/S4 など）")
    tris = surface_tris(nodes, blocks)
    if not len(tris):
        raise MeshError("表面を取り出せませんでした")
    return MeshData(nodes, tris, blocks, "inp", InpDoc(lines, node_lines, newline, enc), warnings)


def surface_tris(nodes: np.ndarray, blocks: list[ElemBlock]) -> np.ndarray:
    """ソリッド要素の外側の面（1 回しか出てこない面）とシェル要素を、外向きの三角形にする。"""
    keys, recs = [], []   # recs: (block, 面番号, 要素行)
    for bi, b in enumerate(blocks):
        if not ETYPES[b.etype]["solid"]:
            continue
        for fi, (f, _, _) in enumerate(_face_templates(b.etype)):
            c = np.sort(b.conn[:, list(f)], axis=1)
            if c.shape[1] == 3:
                c = np.hstack([np.full((len(c), 1), -1), c])
            keys.append(c)
            recs.append(np.column_stack([np.full(len(c), bi), np.full(len(c), fi), np.arange(len(c))]))
    out = []
    if keys:
        K = np.vstack(keys)
        R = np.vstack(recs)
        _, inv, cnt = np.unique(K, axis=0, return_inverse=True, return_counts=True)
        bnd = R[cnt[inv.ravel()] == 1]
        for bi, b in enumerate(blocks):
            if not ETYPES[b.etype]["solid"]:
                continue
            tmpl = _face_templates(b.etype)
            cent = nodes[b.conn[:, : _ncorner(b.etype)]].mean(axis=1)
            for fi, (f, fwd, rev) in enumerate(tmpl):
                rows = bnd[(bnd[:, 0] == bi) & (bnd[:, 1] == fi), 2]
                if not len(rows):
                    continue
                cn = b.conn[rows][:, list(f)]
                p = nodes[cn]
                if len(f) == 3:
                    nrm = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
                else:
                    nrm = np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
                outward = np.einsum("ij,ij->i", nrm, p.mean(axis=1) - cent[rows]) >= 0
                for sel, t in ((outward, fwd), (~outward, rev)):
                    if sel.any():
                        out.append(b.conn[rows[sel]][:, np.array(t)].reshape(-1, 3))
    for b in blocks:
        if ETYPES[b.etype]["solid"]:
            continue
        _, fwd, _ = _face_templates(b.etype)[0]
        out.append(b.conn[:, np.array(fwd)].reshape(-1, 3))
    return np.vstack(out).astype(np.int64) if out else np.zeros((0, 3), np.int64)


def _ncorner(et: str) -> int:
    return {"tet4": 4, "tet10": 4, "hex8": 8, "hex20": 8, "wedge6": 6, "wedge15": 6}.get(et, ETYPES[et]["n"])


def volume_edges(blocks: list[ElemBlock]) -> np.ndarray:
    """要素の辺（2 次要素は 角–中間–角 に分ける）。体積内部の節点をなめらかに動かすのに使う。"""
    out = []
    for b in blocks:
        d = ETYPES[b.etype]
        for a, c in d["edges"]:
            if d["mids"] is None:
                out.append(b.conn[:, [a, c]])
            else:
                m = d["mids"][(a, c)]
                out.append(b.conn[:, [a, m]])
                out.append(b.conn[:, [m, c]])
    e = np.sort(np.vstack(out), axis=1)
    return np.unique(e, axis=0)


def corner_tet_volumes(nodes: np.ndarray, blocks: list[ElemBlock]) -> np.ndarray:
    """反転チェック用: 各ソリッド要素の角の四面体の体積（符号付き）を並べたもの。"""
    vols = []
    for b in blocks:
        for t in ETYPES[b.etype]["corner_tets"]:
            p = nodes[b.conn[:, list(t)]]
            vols.append(np.einsum("ij,ij->i", p[:, 1] - p[:, 0], np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 0])) / 6)
    return np.concatenate(vols) if vols else np.zeros(0)


def write_inp(doc: InpDoc, nodes: np.ndarray, path: str | Path) -> None:
    lines = list(doc.lines)
    for no, idx, nid, nc in doc.node_lines:
        xyz = nodes[idx][:nc] if nc else nodes[idx]
        lines[no] = f"{nid}, " + ", ".join(f"{v:.10g}" for v in xyz)
    Path(path).write_text(doc.newline.join(lines) + doc.newline, encoding=doc.encoding, newline="")
