"""デモ用の鋳物っぽい形状（STEP）とパラメータ定義。

板（160×100×厚さ12）＋ ボス2個（片方に貫通穴）＋ リブ ＋ 角窓。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

from .cad import all_face_info, write_step

DEMO_NAME = "demo_casting.step"


def _box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def _cyl(x, y, z, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, h).Shape()


def make_demo_shape():
    s = _box(0, 0, 0, 160, 100, 12)
    s = BRepAlgoAPI_Fuse(s, _cyl(40, 50, 12, 14, 25)).Shape()       # ボスA（高さ25）
    s = BRepAlgoAPI_Fuse(s, _cyl(120, 50, 12, 10, 18)).Shape()      # ボスB（高さ18）
    s = BRepAlgoAPI_Fuse(s, _box(52, 47, 12, 62, 6, 14)).Shape()     # リブ（幅6・高さ14）
    s = BRepAlgoAPI_Cut(s, _cyl(40, 50, -1, 6, 40)).Shape()         # ボスAの貫通穴 φ12
    s = BRepAlgoAPI_Cut(s, _box(65, 12, -1, 30, 14, 20)).Shape()    # 角窓 30×14
    u = ShapeUpgrade_UnifySameDomain(s, True, True, True)
    u.Build()
    return u.Shape()


def write_demo(models_dir: Path) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / DEMO_NAME
    write_step(make_demo_shape(), path)
    return path


def _find(infos, pred):
    hit = [f.id for f in infos if pred(f)]
    if not hit:
        raise RuntimeError("デモ形状の面が見つかりません")
    return hit[0]


def demo_params(shape) -> list[dict]:
    """デモ形状に合わせたパラメータ定義（面番号は形状から探す）。"""
    infos = all_face_info(shape)
    c = lambda f: np.array(f.center)
    nz = lambda f: f.kind_code == 0 and abs(f.normal[2]) > 0.99
    top = _find(infos, lambda f: nz(f) and f.normal[2] > 0 and abs(c(f)[2] - 12) < 1e-3)
    bottom = _find(infos, lambda f: nz(f) and f.normal[2] < 0 and abs(c(f)[2]) < 1e-3)
    boss_a_top = _find(infos, lambda f: nz(f) and abs(c(f)[2] - 37) < 1e-3)
    boss_b_top = _find(infos, lambda f: nz(f) and abs(c(f)[2] - 30) < 1e-3)
    boss_a_side = _find(infos, lambda f: f.kind_code == 1 and f.convex and abs(f.radius - 14) < 1e-3)
    hole = _find(infos, lambda f: f.kind_code == 1 and not f.convex and abs(f.radius - 6) < 1e-3)
    rib_top = _find(infos, lambda f: nz(f) and abs(c(f)[2] - 26) < 1e-3)
    rib_y0 = _find(infos, lambda f: f.kind_code == 0 and f.normal[1] < -0.99 and abs(c(f)[1] - 47) < 1e-3)
    rib_y1 = _find(infos, lambda f: f.kind_code == 0 and f.normal[1] > 0.99 and abs(c(f)[1] - 53) < 1e-3)
    return [
        {"id": "p1", "key": "plate_t", "name": "板厚", "kind": "distance",
         "faces": [{"face": top, "coef": 1.0}], "ref_face": bottom, "min": 8, "max": 16, "step": 0.5},
        {"id": "p2", "key": "bossA_h", "name": "ボスA 上面の高さ（底面から）", "kind": "distance",
         "faces": [{"face": boss_a_top, "coef": 1.0}], "ref_face": bottom, "min": 30, "max": 45, "step": 0.5},
        {"id": "p3", "key": "bossA_d", "name": "ボスA 外径", "kind": "diameter",
         "faces": [{"face": boss_a_side, "coef": 1.0}], "min": 24, "max": 34, "step": 0.5},
        {"id": "p4", "key": "hole_d", "name": "貫通穴の径", "kind": "diameter",
         "faces": [{"face": hole, "coef": 1.0}], "min": 8, "max": 16, "step": 0.5},
        {"id": "p5", "key": "bossB_h", "name": "ボスB 上面の高さ（底面から）", "kind": "distance",
         "faces": [{"face": boss_b_top, "coef": 1.0}], "ref_face": bottom, "min": 22, "max": 36, "step": 0.5},
        {"id": "p6", "key": "rib_w", "name": "リブ幅（両側に均等）", "kind": "distance",
         "faces": [{"face": rib_y1, "coef": 0.5}, {"face": rib_y0, "coef": 0.5}], "ref_face": rib_y0,
         "min": 4, "max": 10, "step": 0.5},
        {"id": "p7", "key": "rib_h", "name": "リブ上面の高さ（底面から）", "kind": "distance",
         "faces": [{"face": rib_top, "coef": 1.0}], "ref_face": bottom, "min": 20, "max": 30, "step": 0.5},
    ]
