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

from .cad import all_face_info, tessellate, write_stl, write_step

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


def make_fillet_demo_shape():
    """デモ形状に、抜き勾配付きボス（円錐）と、ボス・リブの付け根の R1.5 フィレットを足した形（実物に近い確認用）。"""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    s = make_demo_shape()
    cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(80, 80, 12), gp_Dir(0, 0, 1)), 9, 7, 16).Shape()
    u = ShapeUpgrade_UnifySameDomain(BRepAlgoAPI_Fuse(s, cone).Shape(), True, True, True)
    u.Build()
    s = u.Shape()
    mf = BRepFilletAPI_MakeFillet(s)
    e = TopExp_Explorer(s, TopAbs_EDGE)
    to_edge = getattr(TopoDS, "Edge", None) or TopoDS.Edge_s
    while e.More():
        g = GProp_GProps()
        BRepGProp.LinearProperties_s(e.Current(), g)
        c = g.CentreOfMass()
        # 板の上面（z=12）にある辺のうち、外周と角窓以外 ＝ ボス・リブの付け根
        if 11.9 < c.Z() < 12.1 and not (65 <= c.X() <= 95 and 12 <= c.Y() <= 26) \
                and 0.5 < c.X() < 159.5 and 0.5 < c.Y() < 99.5:
            mf.Add(1.5, to_edge(e.Current()))
        e.Next()
    mf.Build()
    return mf.Shape()


def write_demo(models_dir: Path) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / DEMO_NAME
    write_step(make_demo_shape(), path)
    return path


def write_fillet_demo_stl(models_dir: Path) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / "demo_fillet.stl"
    write_stl(tessellate(make_fillet_demo_shape(), 0.02, angle=0.15), path)
    return path


def write_demo_stl(models_dir: Path) -> Path:
    """同じデモ形状を STL（三角形メッシュ）で。メッシュ変形の確認用。"""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / "demo_casting.stl"
    write_stl(tessellate(make_demo_shape(), 0.05, angle=0.2), path)
    return path


def _find(infos, pred):
    hit = [f.id for f in infos if pred(f)]
    if not hit:
        raise RuntimeError("デモ形状の面が見つかりません")
    return hit[0]


def demo_params(shape_or_infos) -> list[dict]:
    """デモ形状に合わせたパラメータ定義（面番号は形状から探す）。CAD の形状か、面の一覧（STL 等）を渡す。"""
    infos = shape_or_infos if isinstance(shape_or_infos, list) else all_face_info(shape_or_infos)
    c = lambda f: np.array(f.center)
    nz = lambda f: f.kind_code == 0 and abs(f.normal[2]) > 0.99
    top = _find(infos, lambda f: nz(f) and f.normal[2] > 0 and abs(c(f)[2] - 12) < 1e-3)
    bottom = _find(infos, lambda f: nz(f) and f.normal[2] < 0 and abs(c(f)[2]) < 1e-3)
    boss_a_top = _find(infos, lambda f: nz(f) and abs(c(f)[2] - 37) < 1e-3)
    boss_b_top = _find(infos, lambda f: nz(f) and abs(c(f)[2] - 30) < 1e-3)
    boss_a_side = _find(infos, lambda f: f.kind_code == 1 and f.convex and f.fillet_r is None and abs(f.radius - 14) < 0.1)
    hole = _find(infos, lambda f: f.kind_code == 1 and not f.convex and f.fillet_r is None and abs(f.radius - 6) < 0.1)
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
