"""実験計画（どの寸法の組み合わせで形状を作るか）。

method
  lhs    : ラテン超方格。各パラメータの範囲を n 等分し、どの区間も 1 回ずつ使う（少ない数で全体をまんべんなく）
  random : 一様乱数
  grid   : 格子（各パラメータ levels 水準の全組み合わせ。数が爆発するので少数パラメータ向け）
  oat    : 1 因子ずつ（他は基準値のまま、1 つだけ min〜max を levels 水準で振る。感度を見る用）
"""

from __future__ import annotations

import itertools

import numpy as np

METHODS = {"lhs": "ラテン超方格（LHS）", "random": "ランダム", "grid": "格子（全組み合わせ）", "oat": "1因子ずつ（感度確認）"}


def _lhs(n: int, k: int, rng: np.random.Generator, tries: int = 30) -> np.ndarray:
    """最小距離が最大になるものを tries 回から選ぶ（maximin LHS の簡易版）。"""
    best, best_d = None, -1.0
    for _ in range(max(1, tries)):
        u = (np.argsort(rng.random((k, n)), axis=1).T + rng.random((n, k))) / n
        if n > 1:
            d = np.sqrt(((u[:, None, :] - u[None, :, :]) ** 2).sum(-1))
            d = d[np.triu_indices(n, 1)].min()
        else:
            d = 0.0
        if d > best_d:
            best, best_d = u, d
    return best


def sample(params: list[dict], n: int = 20, method: str = "lhs", seed: int = 0, levels: int = 3,
           bases: dict[str, float] | None = None) -> list[dict[str, float]]:
    """params: id・min・max・step（任意）を持つ dict。戻り値: [{id: 値}, ...]"""
    ps = [p for p in params if p.get("enabled") is not False]
    if not ps:
        return []
    lo = np.array([float(p["min"]) for p in ps])
    hi = np.array([float(p["max"]) for p in ps])
    k = len(ps)
    rng = np.random.default_rng(seed)
    if method == "lhs":
        u = _lhs(int(n), k, rng)
    elif method == "random":
        u = rng.random((int(n), k))
    elif method == "grid":
        lv = np.linspace(0, 1, max(2, int(levels)))
        total = len(lv) ** k
        if total > 5000:
            raise ValueError(f"格子の組み合わせが {total} 通りになります。水準数かパラメータ数を減らしてください")
        u = np.array(list(itertools.product(lv, repeat=k)))
    elif method == "oat":
        bases = bases or {}
        b = np.array([(float(bases.get(p["id"], (lo[i] + hi[i]) / 2)) - lo[i]) / ((hi[i] - lo[i]) or 1)
                      for i, p in enumerate(ps)]).clip(0, 1)
        rows = [b.copy()]
        for i in range(k):
            for t in np.linspace(0, 1, max(2, int(levels))):
                r = b.copy()
                r[i] = t
                if not any(np.allclose(r, x) for x in rows):
                    rows.append(r)
        u = np.array(rows)
    else:
        raise ValueError(f"不明な方法: {method}")
    vals = lo + u * (hi - lo)
    out = []
    for row in vals:
        d = {}
        for p, v in zip(ps, row):
            step = float(p.get("step") or 0)
            if step > 0:
                v = float(np.clip(round((v - float(p["min"])) / step) * step + float(p["min"]), p["min"], p["max"]))
            d[p["id"]] = round(float(v), 6)
        out.append(d)
    return out
