"""パラメータ（寸法）→ 面ごとの移動量 への変換。

パラメータの種類（kind）
  distance : 2 つの平面の距離（厚み・高さ・幅）。動かす面と基準面を選ぶ。値＝距離 [mm]
  diameter : 円筒面の直径（穴径・ボス径）。値＝直径 [mm]
  offset   : 選んだ面を法線方向に動かす量そのもの。値＝移動量 [mm]（0 で元の形）

動かす面には係数 coef を付けられる（例: リブ幅を両側 0.5 ずつ広げる）。
同じ面が複数のパラメータに入っている場合は移動量を足し合わせる。

パラメータ定義（dict）の例:
  {"id": "p1", "key": "plate_t", "name": "板厚", "kind": "distance",
   "faces": [{"face": 3, "coef": 1.0}], "ref_face": 5, "min": 8, "max": 16, "step": 0.5}
"""

from __future__ import annotations

import math
import re

import numpy as np

from .cad import CadError, FaceInfo, angle_deg

KINDS = {"distance": "寸法（2面の距離）", "diameter": "直径", "offset": "面の移動量"}
ROUND = (1, 2, 3)   # 直径を持つ面: 円筒・円錐・球


def _moving(p: dict) -> list[tuple[int, float]]:
    out = []
    for f in p.get("faces") or []:
        if isinstance(f, dict):
            out.append((int(f["face"]), float(f.get("coef", 1.0))))
        else:
            out.append((int(f), 1.0))
    return out


def base_value(p: dict, infos: list[FaceInfo]) -> dict:
    """元の形状でのパラメータ値（基準値）と、計算に使う符号。問題があれば error を返す。"""
    kind = p.get("kind", "offset")
    mv = _moving(p)
    if not mv:
        return {"base": None, "sign": 1, "error": "動かす面が選ばれていません"}
    if any(not 0 <= k < len(infos) for k, _ in mv):
        return {"base": None, "sign": 1, "error": "面番号が形状と合いません（モデルが変わった可能性）"}
    if kind == "offset":
        return {"base": 0.0, "sign": 1, "error": None}
    if kind == "diameter":
        f = infos[mv[0][0]]
        if f.kind_code not in ROUND or f.radius is None:
            return {"base": None, "sign": 1, "error": f"直径は円筒・円錐・球の面で指定してください（#{f.id} は{f.kind}）"}
        bad = [k for k, _ in mv if infos[k].kind_code not in ROUND or infos[k].radius is None]
        if bad:
            return {"base": None, "sign": 1, "error": f"円筒・円錐・球以外の面が含まれています: {bad}"}
        return {"base": 2 * f.radius, "sign": 1, "error": None}
    if kind == "distance":
        ref = p.get("ref_face")
        if ref is None or not 0 <= int(ref) < len(infos):
            return {"base": None, "sign": 1, "error": "基準面が選ばれていません"}
        r = infos[int(ref)]
        cands = [k for k, _ in mv if k != int(ref)]
        if not cands:
            return {"base": None, "sign": 1, "error": "動かす面と基準面が同じです"}
        m = infos[cands[0]]
        if m.kind_code != 0 or r.kind_code != 0:
            return {"base": None, "sign": 1, "error": "寸法（距離）は平面どうしで指定してください"}
        nm, nr = np.array(m.normal), np.array(r.normal)
        ang = angle_deg(nm, nr)
        if min(ang, 180 - ang) > 1.0:
            return {"base": None, "sign": 1, "error": f"2 つの平面が平行ではありません（{min(ang, 180 - ang):.1f}°）"}
        d = np.array(m.point) - np.array(r.point)
        dist = abs(float(d.dot(nr)))
        sign = 1 if float(nm.dot(d)) >= 0 else -1
        return {"base": dist, "sign": sign, "error": None}
    return {"base": None, "sign": 1, "error": f"不明な種類: {kind}"}


def face_offsets(params: list[dict], values: dict[str, float], infos: list[FaceInfo]) -> dict[int, float]:
    """パラメータ値 → {面番号: 移動量}。values は id → 値（無ければ基準値＝変形なし）。"""
    out: dict[int, float] = {}
    for p in params:
        if p.get("enabled") is False:
            continue
        b = base_value(p, infos)
        if b["error"]:
            raise CadError(f"「{p.get('name') or p.get('id')}」: {b['error']}")
        v = values.get(p["id"])
        if v is None:
            continue
        delta = float(v) - b["base"]
        for k, coef in _moving(p):
            if p["kind"] == "diameter":
                f = infos[k]
                # ボス（外側）は半径が増える＝外へ、穴（内側）は半径が増える＝肉が減る方向。
                # 円錐は面に垂直に動かすので、半径の変化 × cos(半頂角)（勾配はそのまま）
                cosa = math.cos(math.radians(f.semi_angle or 0.0)) if f.kind_code == 2 else 1.0
                off = (delta / 2) * cosa * (1 if f.convex else -1)
            else:
                off = delta * b["sign"]
            out[k] = out.get(k, 0.0) + off * coef
    return out


def check_params(params: list[dict], infos: list[FaceInfo]) -> list[dict]:
    """画面表示用: 各パラメータの基準値・エラー・キーの重複。"""
    out, seen = [], set()
    for p in params:
        b = base_value(p, infos)
        err = b["error"]
        key = p.get("key") or ""
        if not err and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            err = "列名（key）は英数字と _ で指定してください（CSV の列名になります）"
        if not err and key in seen:
            err = f"列名 {key} が重複しています"
        seen.add(key)
        if not err and p.get("min") is not None and p.get("max") is not None and float(p["min"]) > float(p["max"]):
            err = "最小値が最大値より大きいです"
        out.append({"id": p["id"], "base": b["base"], "sign": b["sign"], "error": err})
    return out
