"""実験計画（どの寸法の組み合わせで形状を作るか）。

method
  lhs    : ラテン超方格。各パラメータの範囲を n 等分し、どの区間も 1 回ずつ使う（少ない数で全体をまんべんなく）
  random : 一様乱数
  grid   : 格子（各パラメータ levels 水準の全組み合わせ。数が爆発するので少数パラメータ向け）
  oat    : 1 因子ずつ（他は基準値のまま、1 つだけ min〜max を levels 水準で振る。感度を見る用）

制約（constraints）
  "rib_h <= bossB_h - 3" のように、列名（key）を使った不等式を 1 行 1 つ。すべて満たす組み合わせだけを使う。
  LHS・ランダムでは、候補を多めに作って制約を満たすものから、互いに離れた n 点を選ぶ（まんべんなさを保つ）。
"""

from __future__ import annotations

import ast
import itertools
import operator

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


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
        ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


def compile_constraints(exprs: list[str], keys: list[str]):
    """制約式 → 関数 f(列名→値の配列 dict) → 満たすかどうかの bool 配列。四則演算・比較・and/or だけ使える。"""
    trees = []
    for e in exprs or []:
        e = e.strip()
        if not e or e.startswith("#"):
            continue
        try:
            tree = ast.parse(e.replace("≦", "<=").replace("≧", ">="), mode="eval").body
        except SyntaxError:
            raise ValueError(f"制約式を読めません: {e}")
        _check(tree, keys, e)
        trees.append(tree)

    def ev(t, env):
        if isinstance(t, ast.Constant):
            return t.value
        if isinstance(t, ast.Name):
            return env[t.id]
        if isinstance(t, ast.BinOp):
            return _OPS[type(t.op)](ev(t.left, env), ev(t.right, env))
        if isinstance(t, ast.UnaryOp):
            return _OPS[type(t.op)](ev(t.operand, env))
        if isinstance(t, ast.BoolOp):
            vals = [ev(v, env) for v in t.values]
            out = vals[0]
            for v in vals[1:]:
                out = (out & v) if isinstance(t.op, ast.And) else (out | v)
            return out
        if isinstance(t, ast.Compare):
            left, out = ev(t.left, env), True
            for op, c in zip(t.ops, t.comparators):
                right = ev(c, env)
                out = out & _OPS[type(op)](left, right)
                left = right
            return out
        raise ValueError("使えない式です")

    def f(env):
        ok = np.ones(len(next(iter(env.values()))), bool) if env else np.ones(0, bool)
        for t in trees:
            ok &= np.asarray(ev(t, env), dtype=bool)
        return ok

    return f, len(trees)


def _check(t, keys, src):
    allowed = (ast.Constant, ast.Name, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Load, ast.And, ast.Or,
               *_OPS.keys())
    for node in ast.walk(t):
        if not isinstance(node, allowed):
            raise ValueError(f"制約式に使えない書き方があります: {src}")
        if isinstance(node, ast.Name) and node.id not in keys:
            raise ValueError(f"制約式の {node.id} という列名がありません（使える列名: {', '.join(keys)}）")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError(f"制約式には数値だけ書けます: {src}")
    if not isinstance(t, (ast.Compare, ast.BoolOp)):
        raise ValueError(f"制約式は <= や >= を含む不等式にしてください: {src}")


def _farthest(u: np.ndarray, n: int, rng) -> np.ndarray:
    """候補 u（0〜1 に正規化）から、互いに離れた n 点を選ぶ。"""
    if len(u) <= n:
        return np.arange(len(u))
    chosen = [int(rng.integers(len(u)))]
    d = np.linalg.norm(u - u[chosen[0]], axis=1)
    for _ in range(n - 1):
        k = int(np.argmax(d))
        chosen.append(k)
        d = np.minimum(d, np.linalg.norm(u - u[k], axis=1))
    return np.array(chosen)


def sample(params: list[dict], n: int = 20, method: str = "lhs", seed: int = 0, levels: int = 3,
           bases: dict[str, float] | None = None, constraints: list[str] | None = None) -> list[dict[str, float]]:
    """params: id・min・max・step（任意）を持つ dict。戻り値: [{id: 値}, ...]"""
    ps = [p for p in params if p.get("enabled") is not False]
    if not ps:
        return []
    feasible, ncons = compile_constraints(constraints or [], [p["key"] for p in ps if "key" in p])
    if ncons:
        return _sample_constrained(ps, n, method, seed, levels, bases, feasible)
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
    return _to_values(ps, lo + u * (hi - lo))


def _to_values(ps, vals) -> list[dict[str, float]]:
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


def _sample_constrained(ps, n, method, seed, levels, bases, feasible) -> list[dict[str, float]]:
    rng = np.random.default_rng(seed)
    lo = np.array([float(p["min"]) for p in ps])
    hi = np.array([float(p["max"]) for p in ps])
    keys = [p["key"] for p in ps]
    if method in ("lhs", "random"):
        m = max(30 * int(n), 300)
        u = _lhs(m, len(ps), rng, tries=1) if method == "lhs" else rng.random((m, len(ps)))
    else:
        rows = sample(ps, n, method, seed, levels, bases)          # 制約なしで作ってから絞る
        u = np.array([[(r[p["id"]] - lo[i]) / ((hi[i] - lo[i]) or 1) for i, p in enumerate(ps)] for r in rows])
    vals = np.array([[d[p["id"]] for p in ps] for d in _to_values(ps, lo + u * (hi - lo))])   # 刻みに丸めてから判定
    ok = feasible({k: vals[:, i] for i, k in enumerate(keys)})
    if not ok.any():
        raise ValueError("制約をすべて満たす組み合わせがありません。範囲か制約式を見直してください")
    vals, u = vals[ok], u[ok]
    if method in ("lhs", "random"):
        _, first = np.unique(vals.round(9), axis=0, return_index=True)   # 刻みで重なった候補は 1 つに
        vals, u = vals[np.sort(first)], u[np.sort(first)]
        vals = vals[_farthest(u, int(n), rng)]
    return [{p["id"]: round(float(v), 6) for p, v in zip(ps, row)} for row in vals]
