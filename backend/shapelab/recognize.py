"""メッシュ（STL / .inp の表面）から 面（平面・円筒・円錐・球・トーラス・その他）を認識する。

考え方: 「面の式を当てはめながら広げる」（領域成長）
  1. 平面: 平らな三角形（隣とほぼ同じ向き）を種に、同じ平面に乗る三角形だけを広げる。大きい平面から順に取る
  2. 曲面: 残った三角形を種に、近くの三角形へ 円筒・円錐・球 を当てはめ、
     頂点がその式の上（許容誤差以内）に乗る三角形だけを広げる。広がるたびに頂点への最小二乗で式を調整する
  3. 小さな半径の円筒に見えて短いもの（ボスの付け根の輪のフィレット）は、トーラスとして当てはめ直して広げる
  4. 隣り合う同じ種類の面は、まとめて当てはめて合えば 1 つにする（途中で分かれた面を戻す）
  5. 残り（自由曲面など）は、つながりごとに「その他」
折れ角で区切るだけだと、フィレットでなめらかにつながったボス・リブ・板が 1 つの面になってしまう。
頂点が式の上に乗っているかで判定すると、フィレットの頂点は円筒・平面から外れるので境目で止まる。

前提: 頂点が実際の形状の上にある（CAD から作った STL、CAD 形状に節点を載せた解析メッシュ）。
スキャンデータのように頂点がばらつく場合は、許容誤差（tol）を大きくする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.optimize import least_squares
from scipy.sparse.csgraph import connected_components

from .cad import FaceInfo

PLANE, CYLINDER, CONE, SPHERE, TORUS, OTHER = 0, 1, 2, 3, 4, 10
NAMES = {PLANE: "平面", CYLINDER: "円筒面", CONE: "円錐面", SPHERE: "球面", TORUS: "トーラス面", OTHER: "その他"}


def tri_geom(P, T):
    c = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    a = np.linalg.norm(c, axis=1) / 2
    n = np.divide(c, 2 * a[:, None], out=np.zeros_like(c), where=a[:, None] > 0)
    return n, a


def tri_adjacency(T):
    """辺を共有する三角形の組（ちょうど 2 枚で共有する辺だけ）。"""
    e = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
    tid = np.tile(np.arange(len(T)), 3)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, tid = e[order], tid[order]
    same = np.all(e[1:] == e[:-1], axis=1)
    trip = np.zeros(len(same), bool)
    if len(same) > 1:
        trip[1:] |= same[1:] & same[:-1]
        trip[:-1] |= same[1:] & same[:-1]
    ok = same & ~trip
    return np.column_stack([tid[:-1][ok], tid[1:][ok]])


def _perp(a):
    u = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(a, u)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(a, e1)


def _unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-30)


# ---------------------------------------------------------------------------
# 面の式（プリミティブ）
# ---------------------------------------------------------------------------

@dataclass
class Prim:
    kind: int
    normal: np.ndarray | None = None      # 平面: 外向き法線
    point: np.ndarray | None = None       # 平面: 面上の点 / 球・トーラス: 中心 / 円筒・円錐: 軸上の点
    axis: np.ndarray | None = None        # 円筒・円錐・トーラス: 軸の向き
    r0: float = 0.0                       # 円筒・円錐: point の高さでの半径 / 球: 半径 / トーラス: 管の半径
    k: float = 0.0                        # 円錐: 軸方向に 1 進んだときの半径の増え方（tan 半頂角）
    R: float = 0.0                        # トーラス: 中心円の半径
    sign: float = 1.0                     # +1: 外側（ボス・凸）/ -1: 内側（穴・凹）

    def _raw(self, X):
        """(距離, 符号なし法線)"""
        if self.kind == PLANE:
            return np.abs((X - self.point) @ self.normal), np.broadcast_to(self.normal, X.shape)
        if self.kind == SPHERE:
            d = X - self.point
            ln = np.linalg.norm(d, axis=1)
            return np.abs(ln - self.r0), d / np.maximum(ln, 1e-30)[:, None]
        rel = X - self.point
        h = rel @ self.axis
        radial = rel - np.outer(h, self.axis)
        rho = np.linalg.norm(radial, axis=1)
        e = radial / np.maximum(rho, 1e-30)[:, None]
        if self.kind == TORUS:
            d = X - (self.point + self.R * e)
            ln = np.linalg.norm(d, axis=1)
            return np.abs(ln - self.r0), d / np.maximum(ln, 1e-30)[:, None]
        s = math.sqrt(1 + self.k * self.k)
        return np.abs(rho - (self.r0 + self.k * h)) / s, (e - self.k * self.axis[None, :]) / s

    def dist(self, X):
        return self._raw(X)[0]

    def normals(self, X):
        """点での外向き法線（ソリッドの外向き）。"""
        if self.kind == PLANE:
            return np.broadcast_to(self.normal, X.shape).copy()
        return self.sign * self._raw(X)[1]


def _set_sign(pr: Prim, cent, n, a):
    raw = pr._raw(cent)[1]
    pr.sign = 1.0 if float((raw * n).sum(1) @ a) >= 0 else -1.0
    return pr


def fit_plane(X, n, a):
    m = (n * a[:, None]).sum(0)
    ln = np.linalg.norm(m)
    if ln == 0:
        return None
    return Prim(PLANE, normal=m / ln, point=X.mean(0))


def _lsq(fun, x0, X):
    """頂点への最小二乗で式のパラメータを調整する（頂点が多いときは間引く）。"""
    if len(X) > 1500:
        X = X[np.random.default_rng(0).choice(len(X), 1500, replace=False)]
    if len(X) <= len(x0):
        return x0
    try:
        return least_squares(lambda p: fun(p, X), x0, method="lm", max_nfev=300).x
    except Exception:
        return x0


def _axis_from(a0, p):
    e1, e2 = _perp(a0)
    a = a0 + p[0] * e1 + p[1] * e2
    return a / np.linalg.norm(a), e1, e2


def refine_axisym(pr: Prim, X) -> Prim:
    a0, c0 = pr.axis.copy(), pr.point.copy()
    cone = pr.kind == CONE

    def unpack(p):
        a, e1, e2 = _axis_from(a0, p)
        return a, c0 + p[2] * e1 + p[3] * e2, p[4], (p[5] if cone else 0.0)

    def fun(p, Y):
        a, c, r0, k = unpack(p)
        rel = Y - c
        h = rel @ a
        rho = np.linalg.norm(rel - np.outer(h, a), axis=1)
        return (rho - (r0 + k * h)) / math.sqrt(1 + k * k)

    x0 = np.r_[0, 0, 0, 0, pr.r0] if not cone else np.r_[0, 0, 0, 0, pr.r0, pr.k]
    a, c, r0, k = unpack(_lsq(fun, x0, X))
    if r0 <= 0:
        return pr
    return Prim(pr.kind, point=c, axis=a, r0=float(r0), k=float(k), sign=pr.sign)


def refine_sphere(pr: Prim, X) -> Prim:
    p = _lsq(lambda p, Y: np.linalg.norm(Y - p[:3], axis=1) - p[3], np.r_[pr.point, pr.r0], X)
    return Prim(SPHERE, point=p[:3], r0=float(abs(p[3])), sign=pr.sign)


def refine_torus(pr: Prim, X) -> Prim:
    a0, c0 = pr.axis.copy(), pr.point.copy()

    def unpack(p):
        a, e1, e2 = _axis_from(a0, p)
        return a, c0 + p[2] * e1 + p[3] * e2 + p[4] * a, p[5], p[6]

    def fun(p, Y):
        a, c, R, r = unpack(p)
        rel = Y - c
        h = rel @ a
        rho = np.linalg.norm(rel - np.outer(h, a), axis=1)
        return np.sqrt((rho - R) ** 2 + h ** 2) - abs(r)

    a, c, R, r = unpack(_lsq(fun, np.r_[0, 0, 0, 0, 0, pr.R, pr.r0], X))
    return Prim(TORUS, point=c, axis=a, R=float(R), r0=float(abs(r)), sign=pr.sign)


def fit_axisym(X, cent, n, a):
    """円筒・円錐の初期推定（軸の向きは法線の広がりから、中心・半径・勾配は頂点から）→ 最小二乗で調整。"""
    if len(X) < 6:
        return None
    w = a / a.sum()
    mn = (n * w[:, None]).sum(0)
    C = ((n - mn) * w[:, None]).T @ (n - mn)
    ev, V = np.linalg.eigh(C)
    if ev[-1] < 1e-6:
        return None
    cands = [V[:, 0], V[:, 1]] if ev[1] < 0.1 * ev[-1] else [V[:, 0]]
    best = None
    kmin = math.tan(math.radians(0.3))
    for ax in cands:
        e1, e2 = _perp(ax)
        B = np.stack([e1, e2], 1)
        q, t = X @ B, X @ ax
        t0 = float(t.mean())
        rhs = (q * q).sum(1)
        sol, *_ = np.linalg.lstsq(np.column_stack([2 * q, np.ones(len(q))]), rhs, rcond=None)
        c2, k = sol[:2], 0.0
        r0 = math.sqrt(max(sol[2] + c2 @ c2, 0))
        res_cyl = np.abs(np.linalg.norm(q - c2, axis=1) - r0).max()
        if len(np.unique(t.round(9))) >= 3:
            tt = t - t0
            sol2, *_ = np.linalg.lstsq(np.column_stack([2 * q, np.ones(len(q)), 2 * tt, tt * tt]), rhs, rcond=None)
            c2b = sol2[:2]
            r0b = math.sqrt(max(sol2[2] + c2b @ c2b, 1e-30))
            kb = sol2[3] / r0b
            if np.abs(np.linalg.norm(q - c2b, axis=1) - (r0b + kb * tt)).max() < 0.5 * res_cyl:
                c2, r0, k = c2b, r0b, kb
        if r0 <= 0:
            continue
        cone = abs(k) >= kmin
        pr = Prim(CONE if cone else CYLINDER, point=B @ c2 + ax * t0, axis=ax, r0=float(r0), k=float(k) if cone else 0.0)
        pr = refine_axisym(pr, X)
        if pr.kind == CONE and abs(pr.k) < kmin:
            pr = refine_axisym(Prim(CYLINDER, point=pr.point, axis=pr.axis, r0=pr.r0), X)
        if pr.r0 <= 0 or abs(pr.k) > math.tan(math.radians(80)):
            continue
        _set_sign(pr, cent, n, a)
        res = float(pr.dist(X).max())
        if best is None or res < best[1]:
            best = (pr, res)
    return best[0] if best else None


def fit_sphere(X, cent, n, a):
    M = np.eye(3) * len(n) - n.T @ n
    w = np.linalg.eigvalsh(M)
    if w[0] <= 0.05 * w[-1]:
        return None
    c = np.linalg.solve(M, (cent - n * (cent * n).sum(1, keepdims=True)).sum(0))
    pr = refine_sphere(Prim(SPHERE, point=c, r0=float(np.linalg.norm(X - c, axis=1).mean())), X)
    return _set_sign(pr, cent, n, a)


def torus_from_tube(pr_cyl: Prim, X, cent, n, a):
    """小さな半径の円筒（＝管の一部）から、トーラス（輪のフィレット）を推定する。
    管の中心 = 頂点から外向き法線の逆へ管の半径だけ戻った点 → それが乗る円が トーラスの中心円。"""
    r = pr_cyl.r0
    raw = pr_cyl._raw(X)[1]                       # 円筒の軸から離れる向き
    q = X - r * raw
    c0 = q.mean(0)
    _, _, Vt = np.linalg.svd(q - c0)
    ax, e1, e2 = Vt[2], Vt[0], Vt[1]
    x, y = (q - c0) @ e1, (q - c0) @ e2
    sol, *_ = np.linalg.lstsq(np.column_stack([2 * x, 2 * y, np.ones_like(x)]), x * x + y * y, rcond=None)
    cc = c0 + sol[0] * e1 + sol[1] * e2
    R = math.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0))
    if R <= r * 0.5:
        return None
    pr = refine_torus(Prim(TORUS, point=cc, axis=ax, R=R, r0=r), X)
    return _set_sign(pr, cent, n, a)


# ---------------------------------------------------------------------------
# 領域成長
# ---------------------------------------------------------------------------

@dataclass
class Patch:
    kind_code: int
    tris: np.ndarray
    prim: Prim | None = None
    fillet: bool = False          # フィレット（トーラス・小半径で 90° 前後の円筒）: 変形では隣の面に追従させる

    @property
    def normal(self):
        return self.prim.normal if self.prim is not None and self.kind_code == PLANE else None


@dataclass
class Recognizer:
    P: np.ndarray
    T: np.ndarray
    tol: float                    # 頂点が面の上にあるとみなす距離 [mm]
    ang: float                    # 三角形の向きと面の向きのずれの許容 [度]（粗い曲面メッシュほど大きく）
    n: np.ndarray = field(init=False)
    a: np.ndarray = field(init=False)
    cent: np.ndarray = field(init=False)
    A: sp.csr_matrix = field(init=False)

    def __post_init__(self):
        self.n, self.a = tri_geom(self.P, self.T)
        self.cent = self.P[self.T].mean(axis=1)
        self.adj = tri_adjacency(self.T)
        nt, adj = len(self.T), self.adj
        self.A = sp.coo_matrix((np.ones(2 * len(adj)), (np.r_[adj[:, 0], adj[:, 1]], np.r_[adj[:, 1], adj[:, 0]])),
                               shape=(nt, nt)).tocsr()
        self.cos_ang = math.cos(math.radians(self.ang))
        self.size = float(np.ptp(self.P[np.unique(self.T)], axis=0).max()) or 1.0

    # ---- 判定・当てはめ ---------------------------------------------------------
    def _fits(self, pr: Prim, tris):
        """三角形が面の式に乗っているか（3 頂点の距離と、三角形の向き）。"""
        if not len(tris):
            return np.zeros(0, bool)
        d = pr.dist(self.P[self.T[tris].ravel()]).reshape(-1, 3).max(1)
        if pr.kind == PLANE:
            ok_n = self.n[tris] @ pr.normal >= math.cos(math.radians(1.0))
        else:
            ok_n = (pr.normals(self.cent[tris]) * self.n[tris]).sum(1) >= self.cos_ang
        return (d <= self.tol) & ok_n

    def _verts(self, tris):
        return self.P[np.unique(self.T[tris])]

    def _refit(self, pr: Prim, tris) -> Prim:
        X = self._verts(tris)
        if pr.kind == PLANE:
            return fit_plane(X, self.n[tris], self.a[tris]) or pr
        if pr.kind in (CYLINDER, CONE):
            new = refine_axisym(pr, X)
            if new.kind == CONE and abs(new.k) < math.tan(math.radians(0.3)):   # 勾配ほぼ 0 は円筒
                new = refine_axisym(Prim(CYLINDER, point=new.point, axis=new.axis, r0=new.r0, sign=new.sign), X)
        elif pr.kind == SPHERE:
            new = refine_sphere(pr, X)
        elif pr.kind == TORUS:
            new = refine_torus(pr, X)
        else:
            return pr
        return _set_sign(new, self.cent[tris], self.n[tris], self.a[tris])

    def _fit_curved(self, tris):
        X = self._verts(tris)
        c, n, a = self.cent[tris], self.n[tris], self.a[tris]
        best = None
        for pr in (fit_axisym(X, c, n, a), fit_sphere(X, c, n, a)):
            if pr is None:
                continue
            if pr.kind == SPHERE and pr.r0 > 0.2 * self.size:     # 形に対して大きすぎる球は採用しない（鋳物ではまれ）
                continue
            ok = self._fits(pr, tris).mean()
            if ok > 0.9 and (best is None or ok > best[1] + 1e-9):
                best = (pr, ok)
        return best[0] if best else None

    def _grow(self, pr: Prim, seed_set: np.ndarray, free: np.ndarray) -> tuple[np.ndarray, Prim]:
        inset = np.zeros(len(self.T), bool)
        inset[seed_set] = True
        size_at_fit = len(seed_set)
        frontier = seed_set
        for _ in range(10000):
            nb = np.unique(self.A[frontier].indices) if len(frontier) else np.zeros(0, np.int64)
            cand = nb[free[nb] & ~inset[nb]]
            good = cand[self._fits(pr, cand)]
            if len(good):
                inset[good] = True
                frontier = good
            cnt = int(inset.sum())
            if len(good) and cnt < 1.5 * size_at_fit:
                continue
            # 広がったら（または止まったら）頂点への最小二乗で当てはめ直す
            cur = np.flatnonzero(inset)
            new = self._refit(pr, cur)
            if self._fits(new, cur).mean() >= self._fits(pr, cur).mean() - 1e-9:
                pr = new
            size_at_fit = cnt
            if not len(good):
                # 当てはめ直した式で境目をもう一度調べ、増えなければ終了
                border = np.unique(self.A[cur].indices)
                border = border[free[border] & ~inset[border]]
                more = border[self._fits(pr, border)]
                if not len(more):
                    break
                inset[more] = True
                frontier = more
        return np.flatnonzero(inset), pr

    def _ring(self, seed, free, want=30, rounds=5):
        s = np.array([seed])
        for _ in range(rounds):
            nb = np.unique(np.r_[s, self.A[s].indices])
            nb = nb[free[nb]]
            if len(nb) == len(s):
                break
            s = nb
            if len(s) >= want:
                break
        return s

    def _arc(self, pr: Prim, tris) -> float:
        X = self._verts(tris)
        rel = X - pr.point
        e = rel - np.outer(rel @ pr.axis, pr.axis)
        e1, e2 = _perp(pr.axis)
        ang = np.sort(np.arctan2(e @ e2, e @ e1))
        gaps = np.diff(np.r_[ang, ang[0] + 2 * math.pi])
        return 2 * math.pi - gaps.max()

    def _complete(self, pr: Prim, tris) -> bool:
        """細かい破片を面として採用しない。円筒・円錐は軸まわりに 60° 以上、球は法線が 30° 以上広がっていること。"""
        if pr.kind in (CYLINDER, CONE):
            # 細い帯に大きな半径の円筒が「たまたま合う」のを防ぐ（大きな曲面は面積も大きいはず）
            small = self.a[tris].sum() < 0.01 * self.a.sum()
            if small and (pr.r0 > 0.25 * self.size or abs(pr.k) > math.tan(math.radians(60))):
                return False
            return self._arc(pr, tris) >= math.radians(60)
        if pr.kind == SPHERE:
            nn = self.n[tris]
            return float((nn @ _unit(nn.mean(0))).min()) <= math.cos(math.radians(30))
        if pr.kind == TORUS:
            return self._arc(pr, tris) >= math.radians(30)
        return True

    def _is_tube(self, pr: Prim, tris) -> bool:
        """小さな半径の円筒で、軸方向に短い → 輪のフィレット（トーラス）の一部かもしれない。"""
        if pr.kind not in (CYLINDER, CONE) or pr.r0 > 0.05 * self.size:
            return False
        h = (self._verts(tris) - pr.point) @ pr.axis
        return np.ptp(h) < 6 * pr.r0

    def _is_fillet(self, p: Patch) -> bool:
        if p.kind_code == TORUS:
            return True
        if p.kind_code in (CYLINDER, CONE) and p.prim is not None and p.prim.r0 < 0.03 * self.size:
            return self._arc(p.prim, p.tris) <= math.radians(120)   # 角を丸めた面（1/4 円前後）
        return False

    # ---- 本体 -----------------------------------------------------------------
    def run(self) -> tuple[np.ndarray, list[Patch]]:
        nt = len(self.T)
        free = np.ones(nt, bool)
        patches: list[Patch] = []
        min_area = self.a.sum() * 1e-6

        # 1. 平面: 隣と同じ向きの「平らな」三角形を種に、大きい順
        flat = np.ones(nt, bool)
        same = (self.n[self.adj[:, 0]] * self.n[self.adj[:, 1]]).sum(1)
        smooth = same >= math.cos(math.radians(max(self.ang, 5)))
        flat[self.adj[smooth & (same < math.cos(math.radians(0.5)))].ravel()] = False
        seeds = np.flatnonzero(flat)
        for s in seeds[np.argsort(-self.a[seeds])]:
            if not free[s]:
                continue
            pr = Prim(PLANE, normal=self.n[s].copy(), point=self.cent[s].copy())
            tris, pr = self._grow(pr, np.array([s]), free)
            if self.a[tris].sum() >= min_area:
                patches.append(Patch(PLANE, tris, pr))
                free[tris] = False

        # 2. 曲面: 残りの三角形を面積の大きい順に種にする
        order = np.flatnonzero(free)
        tried = np.zeros(nt, bool)
        for s in order[np.argsort(-self.a[order])]:
            if not free[s] or tried[s]:
                continue
            tried[s] = True
            # 出発点の範囲は小さい方から広げて試し、ほぼすべてが乗る式を使う
            # （広く取りすぎると、隣のフィレットの角などが混ざって式がずれる）
            pr, ring = None, np.array([s])
            for rounds in (1, 2, 3, 4):
                rg = self._ring(s, free, want=10 * rounds, rounds=rounds)
                if len(np.unique(self.T[rg])) < 6:
                    continue
                cand = self._fit_curved(rg)
                if cand is None:
                    if pr is not None:
                        break
                    continue
                if self._fits(cand, rg).mean() >= 0.98:
                    pr, ring = cand, rg
                elif pr is not None:
                    break
            if pr is None:
                tried[ring] = True          # 当てはまらなかった周りは、もう種にしない（高速化）
                continue
            ok_ring = ring[self._fits(pr, ring)]
            if s not in ok_ring:
                tried[ring] = True
                continue
            tris, pr = self._grow(pr, ok_ring, free)
            if self._is_tube(pr, tris):
                tor = torus_from_tube(pr, self._verts(tris), self.cent[tris], self.n[tris], self.a[tris])
                if tor is not None and self._fits(tor, tris).mean() > 0.8:
                    t2, tor = self._grow(tor, tris[self._fits(tor, tris)], free)
                    if self.a[t2].sum() > 1.2 * self.a[tris].sum():
                        tris, pr = t2, tor
            if self.a[tris].sum() >= min_area and len(tris) >= 3 and self._complete(pr, tris):
                patches.append(Patch(pr.kind, tris, pr))
                free[tris] = False
            tried[tris] = True

        # 3. 残り: つながりごとに「その他」
        rest = np.flatnonzero(free)
        if len(rest):
            _, lab = connected_components(self.A[rest][:, rest], directed=False)
            for L in np.unique(lab):
                patches.append(Patch(OTHER, rest[lab == L], None))

        patches = self._merge(patches)
        for p in patches:
            p.fillet = self._is_fillet(p)
        patches.sort(key=lambda p: -self.a[p.tris].sum())
        label = np.empty(nt, np.int64)
        for i, p in enumerate(patches):
            label[p.tris] = i
        return label, patches

    def _merge(self, patches: list[Patch]) -> list[Patch]:
        """隣り合う面をまとめる: 同じ種類で一緒に当てはめても合うもの、「その他」どうし。"""
        changed = True
        while changed:
            changed = False
            lab = np.empty(len(self.T), np.int64)
            for i, p in enumerate(patches):
                lab[p.tris] = i
            pairs = np.unique(np.sort(lab[self.adj], axis=1), axis=0)
            pairs = pairs[pairs[:, 0] != pairs[:, 1]]
            dead: set[int] = set()
            for i, j in pairs:
                if i in dead or j in dead:
                    continue
                pi, pj = patches[i], patches[j]
                tris = np.r_[pi.tris, pj.tris]
                if pi.kind_code != pj.kind_code:
                    # 小さい面が、隣の大きい曲面の式にそのまま乗るなら吸収する
                    # （曲面の一部がたまたま平らに並んで、小さな平面として先に取られた場合など）
                    ai, aj = self.a[pi.tris].sum(), self.a[pj.tris].sum()
                    big, small, bi = (pi, pj, i) if ai >= aj else (pj, pi, j)
                    if big.kind_code in (OTHER, PLANE) or self.a[small.tris].sum() > 0.1 * self.a[big.tris].sum():
                        continue
                    if self._fits(big.prim, small.tris).mean() < 0.995:
                        continue
                    patches[bi] = Patch(big.kind_code, tris, big.prim)
                    dead.add(j if bi == i else i)
                    changed = True
                    continue
                if pi.kind_code == OTHER:
                    patches[i] = Patch(OTHER, tris, None)
                else:
                    big = pi if self.a[pi.tris].sum() >= self.a[pj.tris].sum() else pj
                    new = self._refit(big.prim, tris)
                    if self._fits(new, tris).mean() < 0.995:
                        continue
                    patches[i] = Patch(new.kind, tris, new)
                dead.add(j)
                changed = True
            patches = [p for k, p in enumerate(patches) if k not in dead]
        return patches


def default_tol(P, T) -> float:
    span = float(np.ptp(P[np.unique(T)], axis=0).max()) or 1.0
    return span * 2e-4


def segment(P, T, angle_deg: float = 30.0, tol: float | None = None):
    """三角形ごとの面番号と、面の一覧（面積の大きい順）。angle_deg は曲面の三角形の向きのずれの許容。"""
    tol = float(tol) if tol else default_tol(P, T)
    return Recognizer(P, T, tol, angle_deg).run()


def patch_infos(P, T, patches: list[Patch]) -> list[FaceInfo]:
    n, a = tri_geom(P, T)
    cent = P[T].mean(axis=1)
    out = []
    for i, p in enumerate(patches):
        ar = a[p.tris]
        c = (cent[p.tris] * ar[:, None]).sum(0) / max(ar.sum(), 1e-30)
        k = p.tris[np.argmin(np.linalg.norm(cent[p.tris] - c, axis=1))]
        pt = cent[k]
        pr = p.prim
        if p.kind_code == PLANE:
            nr, pt = pr.normal, c
        elif pr is not None:
            nr = pr.normals(pt[None, :])[0]
        else:
            m = (n[p.tris] * ar[:, None]).sum(0)
            nr = m / (np.linalg.norm(m) or 1)
        info = FaceInfo(i, NAMES[p.kind_code], p.kind_code, round(float(ar.sum()), 6), np.round(c, 6).tolist(),
                        np.round(pt, 6).tolist(), np.round(nr, 6).tolist())
        if pr is not None and p.kind_code in (CYLINDER, CONE):
            X = P[np.unique(T[p.tris])]
            t = (X - pr.point) @ pr.axis
            rmax = pr.r0 + pr.k * (t.max() if pr.k >= 0 else t.min())     # 大径側（ボスの根元・穴の入口）
            info.radius = round(float(rmax), 6)
            info.axis_loc = np.round(pr.point, 6).tolist()
            info.axis_dir = np.round(pr.axis, 6).tolist()
            info.convex = bool(pr.sign > 0)
            info.semi_angle = round(math.degrees(math.atan(abs(pr.k))), 4)
        elif pr is not None and p.kind_code == SPHERE:
            info.radius = round(pr.r0, 6)
            info.convex = bool(pr.sign > 0)
        elif pr is not None and p.kind_code == TORUS:
            info.fillet_r = round(pr.r0, 6)
            info.convex = bool(pr.sign > 0)
        if p.fillet and pr is not None:
            info.fillet_r = round(pr.r0, 6)
        out.append(info)
    return out
