"""メッシュ（STL / .inp）のメッシュ変形（モーフィング）。面の認識は recognize.py。

メッシュ変形
  表面の各節点に、その節点が乗っている面ごとの拘束をかける
    平面・円筒・円錐・球: 面の法線方向の移動量 = その面の移動量（動かさない面は 0 ＝ 面の上をすべるだけ）
      円筒は半径方向、円錐は勾配を保ったまま面に垂直に動く
    その他の面: 動かすときだけ、節点の法線方向に移動量を与える
  拘束で決まらない方向は、辺の長さで重み付けした調和補間（ラプラス方程式）でなめらかに埋める。
  係数行列は移動量によらないので、面の分割ごとに 1 回だけ分解し、スライダー操作は代入だけで解く。
  .inp の内部節点は、表面の動きを境界条件にして同じく調和補間で動かす（要素のつながりはそのまま）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from scipy.sparse.linalg import cg, splu

from .meshio_ import MeshData, MeshError, corner_tet_volumes, volume_edges
from .recognize import OTHER, PLANE, Patch, patch_infos, segment, tri_adjacency, tri_geom  # noqa: F401


# ---------------------------------------------------------------------------
# メッシュ変形
# ---------------------------------------------------------------------------

def _laplacian(nnode, edges, X):
    w = 1.0 / np.maximum(np.linalg.norm(X[edges[:, 0]] - X[edges[:, 1]], axis=1), 1e-12)
    i, j = edges[:, 0], edges[:, 1]
    W = sp.coo_matrix((np.concatenate([w, w]), (np.concatenate([i, j]), np.concatenate([j, i]))), shape=(nnode, nnode)).tocsr()
    return (sp.diags(np.asarray(W.sum(1)).ravel()) - W).tocsr(), float(w.mean())


class Morpher:
    """階層的なメッシュ変形: 角 → 面の境界線 → 面の中 → 体積の中 の順に決める。

    1. 各節点の拘束（乗っている面ごとに 1 本）で決まる成分を求める。3 面が交わる角は位置が決まる
    2. 面の境界線上の節点（2 面以上に乗る）は、境界線に沿った 1 次元の調和補間（＝線に沿って均等に伸び縮み）
    3. 面の中の節点は、その面の境界を固定して 2 次元の調和補間
    4. .inp の内部節点は、表面を固定して 3 次元の調和補間
    面全体を一度に補間すると、隣の大きな面に引っぱられて小さな面がつぶれる（裏返る）ため、この順にしている。
    """

    def __init__(self, mesh: MeshData, label: np.ndarray, patches: list[Patch]):
        self.mesh, self.label, self.patches = mesh, label, patches
        P, T = mesh.nodes, mesh.tris
        self.surf = np.unique(T)
        self.loc = -np.ones(len(P), np.int64)
        self.loc[self.surf] = np.arange(len(self.surf))
        Tl = self.loc[T]
        e = np.unique(np.sort(np.vstack([Tl[:, [0, 1]], Tl[:, [1, 2]], Tl[:, [2, 0]]]), axis=1), axis=0)
        X = P[self.surf]
        self.L, wmean = _laplacian(len(self.surf), e, X)
        self.eps = wmean * 1e-7
        # 面の境界線: 両側の三角形の面番号が違う辺
        te = np.sort(np.vstack([Tl[:, [0, 1]], Tl[:, [1, 2]], Tl[:, [2, 0]]]), axis=1)
        tl = np.tile(label, 3)
        _, inv = np.unique(te, axis=0, return_inverse=True)
        inv = inv.ravel()
        lo = np.full(inv.max() + 1, np.iinfo(np.int64).max)
        hi = np.full(inv.max() + 1, -1)
        np.minimum.at(lo, inv, tl)
        np.maximum.at(hi, inv, tl)
        ue = np.zeros((inv.max() + 1, 2), np.int64)
        ue[inv] = te
        curve = ue[lo != hi]
        self.Lc, _ = _laplacian(len(self.surf), curve, X) if len(curve) else (sp.csr_matrix((len(self.surf),) * 2), 0)
        # 節点 × 面 の所属（拘束の候補）
        n, a = tri_geom(P, T)
        nt = len(T)
        pairs = np.unique(np.column_stack([Tl.ravel(), np.repeat(label, 3)]), axis=0)
        # フィレットの節点には、隣り合う面（ボスの円筒・板の平面など）の拘束も付ける。
        # → ボスが太くなればフィレットも同じだけ外へ動き、板の上には乗ったまま（半径をほぼ保って平行移動）
        adj = tri_adjacency(T)
        lp = np.unique(np.sort(label[adj], axis=1), axis=0)
        lp = lp[lp[:, 0] != lp[:, 1]]
        extra = []
        for f in [i for i, p in enumerate(patches) if p.fillet]:
            nb = np.r_[lp[lp[:, 0] == f, 1], lp[lp[:, 1] == f, 0]]
            nb = [q for q in np.unique(nb) if not patches[q].fillet and patches[q].kind_code != OTHER]
            nodes = np.unique(Tl[patches[f].tris])
            r = patches[f].prim.r0 if patches[f].prim is not None else 0.0
            reach = max(2.5 * r, 1e-9)
            for q in nb:
                # 隣の面の拘束は、その面の近くにあるフィレットの節点にだけ付ける
                # （輪のフィレットの反対側まで、リブの側面などに縛られないように）
                qn = np.unique(Tl[patches[q].tris])
                d, _ = cKDTree(X[qn]).query(X[nodes], distance_upper_bound=reach)
                near = nodes[np.isfinite(d)]
                if len(near):
                    extra.append(np.column_stack([near, np.full(len(near), q)]))
        if extra:
            pairs = np.unique(np.vstack([pairs] + extra), axis=0)
        self.pairs = pairs                       # (節点(局所), 面)
        rows = np.zeros((len(pairs), 3))
        # フィレットは「その他」と同じく、動かさないときは拘束しない（隣の面に合わせてなめらかに追従）
        kinds = np.array([OTHER if patches[p].fillet else patches[p].kind_code for p in pairs[:, 1]])
        for p in np.unique(pairs[:, 1]):
            sel = pairs[:, 1] == p
            pat = patches[p]
            if pat.kind_code == PLANE:
                rows[sel] = pat.prim.normal
            elif pat.kind_code != OTHER and not pat.fillet:
                # 円筒・円錐・球: 節点での面の外向き法線（円筒は半径方向、円錐は勾配を保つ向き）
                rows[sel] = pat.prim.normals(X[pairs[sel, 0]])
            else:
                tt = pat.tris
                acc = np.zeros((len(self.surf), 3))
                np.add.at(acc, Tl[tt].ravel(), np.repeat(n[tt] * a[tt, None], 3, axis=0))
                vv = acc[pairs[sel, 0]]
                rows[sel] = vv / np.maximum(np.linalg.norm(vv, axis=1, keepdims=True), 1e-12)
        self.rows, self.kinds = rows, kinds
        # 2 面以上に乗る節点 = 境界線・角
        npat = np.bincount(pairs[:, 0], minlength=len(self.surf))
        self.feat = npat >= 2
        self._fact: dict[frozenset, tuple] = {}
        self._vol = None
        del nt

    # ---- 表面 -----------------------------------------------------------------
    def _factor(self, free_active: frozenset):
        if free_active in self._fact:
            return self._fact[free_active]
        use = (self.kinds != OTHER) | np.isin(self.pairs[:, 1], list(free_active))
        pr, rows = self.pairs[use], self.rows[use]
        ns = len(self.surf)
        order = np.argsort(pr[:, 0], kind="stable")
        pr, rows = pr[order], rows[order]
        starts = np.searchsorted(pr[:, 0], np.arange(ns + 1))
        # N: 自由な方向の基底、Pm: 面の移動量 → 拘束で決まる変位。拘束の数（k）ごとにまとめて SVD する
        cnt = np.diff(starts)
        Ni, Nj, Nv, Pi, Pj, Pv = [], [], [], [], [], []
        free_dirs = np.zeros(ns, np.int64)              # 節点ごとの自由な方向の数
        basis = np.zeros((ns, 3, 3))                    # 自由な方向（行ベクトル、先頭 free_dirs 個が有効）
        free_dirs[cnt == 0] = 3
        basis[cnt == 0] = np.eye(3)
        for k in np.unique(cnt[cnt > 0]):
            vs = np.flatnonzero(cnt == k)
            idx = starts[vs][:, None] + np.arange(k)[None, :]          # (m, k)
            A = rows[idx]                                              # (m, k, 3)
            U, sv, Vt = np.linalg.svd(A, full_matrices=True)           # U (m,k,k) sv (m,min) Vt (m,3,3)
            r = (sv > 0.05 * sv[:, :1]).sum(1)                         # 階数
            for rr in np.unique(r):
                sel = r == rr
                vv, Us, ss, Vs, ii = vs[sel], U[sel], sv[sel], Vt[sel], idx[sel]
                pinv = np.einsum("mqd,mq,mkq->mdk", Vs[:, :rr], 1 / ss[:, :rr], Us[:, :, :rr])  # (m,3,k)
                d_idx = np.broadcast_to(np.arange(3)[None, :, None], pinv.shape)
                Pi.append((3 * vv[:, None, None] + d_idx).ravel())
                Pj.append(np.broadcast_to(pr[ii, 1][:, None, :], pinv.shape).ravel())
                Pv.append(pinv.ravel())
                free_dirs[vv] = 3 - rr
                basis[vv, : 3 - rr] = Vs[:, rr:]
        cols = np.concatenate([[0], np.cumsum(free_dirs)])
        col = int(cols[-1])
        for q in range(3):
            vv = np.flatnonzero(free_dirs > q)
            Ni.append((3 * vv[:, None] + np.arange(3)[None, :]).ravel())
            Nj.append(np.repeat(cols[vv] + q, 3))
            Nv.append(basis[vv, q].ravel())
        cat = lambda x: np.concatenate(x) if x else np.zeros(0)
        Ni, Nj, Nv, Pi, Pj, Pv = map(cat, (Ni, Nj, Nv, Pi, Pj, Pv))
        keep = np.abs(Nv) > 1e-14
        Ni, Nj, Nv = Ni[keep], Nj[keep], Nv[keep]
        keep = np.abs(Pv) > 1e-14
        Pi, Pj, Pv = Pi[keep], Pj[keep], Pv[keep]
        N = sp.csr_matrix((Nv, (Ni, Nj)), shape=(3 * ns, col))
        Pm = sp.csr_matrix((Pv, (Pi, Pj)), shape=(3 * ns, len(self.patches)))
        col_node = np.repeat(np.arange(ns), free_dirs)
        # 境界線のうち、角（位置が決まる節点）を含むつながりだけを線に沿って補間する。
        # 角の無い閉じた線（ボス上面の円など）は両端で引っぱるものが無いので、面の中と一緒に解く
        lab = connected_components(self.Lc, directed=False)[1]
        anchored = np.zeros(lab.max() + 1, bool)
        anchored[np.unique(lab[self.feat & (free_dirs == 0)])] = True
        has_curve = np.asarray(abs(self.Lc).sum(1)).ravel() > 0
        on_curve = self.feat & has_curve & anchored[lab]
        cf, ci = np.flatnonzero(on_curve[col_node]), np.flatnonzero(~on_curve[col_node])
        I3 = sp.identity(3)

        def factor(K, cols):
            if not len(cols):
                return None, None
            Nc = N[:, cols].tocsc()
            A = (Nc.T @ K @ Nc + self.eps * sp.identity(len(cols))).tocsc()
            return Nc, splu(A, permc_spec="MMD_AT_PLUS_A", options=dict(SymmetricMode=True))

        K1 = sp.kron(self.Lc, I3).tocsr()      # 境界線に沿って
        K2 = sp.kron(self.L, I3).tocsr()       # 面の中
        self._fact[free_active] = (Pm, K1, K2, *factor(K1, cf), *factor(K2, ci))
        return self._fact[free_active]

    def surface_displacement(self, offsets: dict[int, float]) -> np.ndarray:
        free = frozenset(k for k, v in offsets.items() if abs(v) > 1e-12 and (self.patches[k].kind_code == OTHER or self.patches[k].fillet))
        Pm, K1, K2, Nf, sf, Ni, si = self._factor(free)
        o = np.zeros(len(self.patches))
        for k, v in offsets.items():
            o[int(k)] = v
        U = Pm @ o                                   # 拘束で決まる成分
        if sf is not None:                           # 境界線・角
            U = U + Nf @ sf.solve(-(Nf.T @ (K1 @ U)))
        if si is not None:                           # 面の中（境界線を固定）
            U = U + Ni @ si.solve(-(Ni.T @ (K2 @ U)))
        return U.reshape(-1, 3)

    # ---- 体積内部 ---------------------------------------------------------------
    def _vol_factor(self):
        if self._vol is not None:
            return self._vol
        blocks = self.mesh.solid_blocks
        P = self.mesh.nodes
        used = np.unique(np.concatenate([b.conn.ravel() for b in blocks]))
        interior = np.setdiff1d(used, self.surf)
        if not len(interior):
            self._vol = (interior, None, None, None)
            return self._vol
        E = volume_edges(blocks)
        L, _ = _laplacian(len(P), E, P)
        Lii = L[interior][:, interior].tocsc()
        Lis = L[interior][:, self.surf].tocsr()
        solver = splu(Lii, permc_spec="MMD_AT_PLUS_A", options=dict(SymmetricMode=True)) if len(interior) <= 600_000 else None
        self._vol = (interior, Lii, Lis, solver)
        return self._vol

    def displacement(self, offsets: dict[int, float], full: bool = True) -> np.ndarray:
        U = np.zeros_like(self.mesh.nodes)
        U[self.surf] = self.surface_displacement(offsets)
        if full and self.mesh.solid_blocks:
            interior, Lii, Lis, solver = self._vol_factor()
            if len(interior):
                rhs = -(Lis @ U[self.surf])
                for d in range(3):
                    if solver is not None:
                        U[interior, d] = solver.solve(rhs[:, d])
                    else:
                        U[interior, d], _ = cg(Lii, rhs[:, d], rtol=1e-8, maxiter=5000)
        return U

    # ---- 品質 -----------------------------------------------------------------
    def quality(self, Pnew: np.ndarray) -> dict:
        P, T = self.mesh.nodes, self.mesh.tris
        n0, a0 = tri_geom(P, T)
        n1, a1 = tri_geom(Pnew, T)
        flipped = int(((n0 * n1).sum(1) < 0).sum() + ((a1 < a0 * 1e-6) & (a0 > 0)).sum())
        q = {"flipped_tris": flipped, "inverted_elements": 0, "min_volume_ratio": None}
        if self.mesh.solid_blocks:
            v0 = corner_tet_volumes(P, self.mesh.solid_blocks)
            v1 = corner_tet_volumes(Pnew, self.mesh.solid_blocks)
            ok = np.abs(v0) > 0
            ratio = v1[ok] / v0[ok]
            q["inverted_elements"] = int((ratio <= 0).sum())
            q["min_volume_ratio"] = float(ratio.min()) if len(ratio) else None
        return q


def mesh_mass(P, T) -> dict:
    p = P[T]
    vol = float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6)
    area = float(np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1).sum() / 2)
    used = P[np.unique(T)]
    lo, hi = used.min(0), used.max(0)
    return {"volume": abs(vol), "area": area, "bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "size": (hi - lo).tolist()}


def check_quality(q: dict) -> None:
    if q["inverted_elements"]:
        raise MeshError(f"要素が {q['inverted_elements']} 個 裏返りました（動かす量が要素の大きさに対して大きすぎます）")
    if q["flipped_tris"]:
        raise MeshError(f"表面の三角形が {q['flipped_tris']} 枚 裏返りました（動かす量が大きすぎます）")
