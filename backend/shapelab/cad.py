"""CAD 形状（B-rep）の読み込み・面情報・面移動（寸法変更）・メッシュ化・書き出し。

OpenCascade（cadquery-ocp）を使う。寸法変更は「選んだ面を法線方向に動かす」
ダイレクトモデリング方式（BRepOffset_MakeOffset の面ごとオフセット）で行う。
  - 平面を動かす → 厚み・高さ・長さが変わる
  - 円筒面を動かす → 穴径・ボス径が変わる
隣の面は自動で延長・短縮されるので、形状の履歴（CAD のツリー）が無い STEP でも寸法を変えられる。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import GeomAbs_Intersection
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.IGESControl import IGESControl_Reader
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.Standard import Standard_Failure
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

try:  # OCP 8 系（OCCT 8）はコレクションが OCP.collections に移った
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape
    from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
except ImportError:  # OCP 7 系
    from OCP.TopTools import TopTools_IndexedMapOfShape, TopTools_ListOfShape

_to_face = getattr(TopoDS, "Face", None) or TopoDS.Face_s

def _quiet_occt() -> None:
    """STEP 読み書き時の大量のログ（Statistics on Transfer…）を止める。"""
    try:
        from OCP.Message import Message, Message_Gravity
        for pr in Message.DefaultMessenger_s().Printers():
            pr.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception:
        pass


_quiet_occt()

CAD_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep", ".brp")

SURFACE_NAMES = {
    0: "平面", 1: "円筒面", 2: "円錐面", 3: "球面", 4: "トーラス面",
    5: "ベジエ面", 6: "Bスプライン面", 7: "回転面", 8: "押出面", 9: "オフセット面", 10: "その他",
}


class CadError(Exception):
    """利用者に見せる日本語メッセージ付きのエラー。"""


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------

def load_shape(path: str | Path) -> TopoDS_Shape:
    path = Path(path)
    suf = path.suffix.lower()
    if suf in (".step", ".stp"):
        r = STEPControl_Reader()
        if r.ReadFile(str(path)) != IFSelect_RetDone:
            raise CadError(f"STEP を読めませんでした: {path.name}")
        r.TransferRoots()
        shape = r.OneShape()
    elif suf in (".iges", ".igs"):
        r = IGESControl_Reader()
        if r.ReadFile(str(path)) != IFSelect_RetDone:
            raise CadError(f"IGES を読めませんでした: {path.name}")
        r.TransferRoots()
        shape = r.OneShape()
    elif suf in (".brep", ".brp"):
        shape = TopoDS_Shape()
        if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
            raise CadError(f"BREP を読めませんでした: {path.name}")
    elif suf == ".stl":
        raise CadError("STL は三角形の集まりで寸法（面・穴径）の情報が無いため、寸法変更できません。"
                       "CAD から STEP で書き出したファイルを使ってください。")
    else:
        raise CadError(f"対応していない形式です: {suf}（STEP / IGES / BREP に対応）")
    if shape.IsNull():
        raise CadError(f"形状が空です: {path.name}")
    # ソリッドが 1 個だけならそれを取り出す（面の番号を安定させるため）
    solids = list(_iter(shape, TopAbs_SOLID))
    if len(solids) == 1:
        shape = solids[0]
    elif not solids:
        raise CadError("ソリッド（中身の詰まった立体）がありません。サーフェスだけの形状は扱えません。")
    return shape


def write_step(shape: TopoDS_Shape, path: str | Path) -> None:
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise CadError(f"STEP を書き出せませんでした: {path}")


def write_stl(mesh: "Mesh", path: str | Path) -> None:
    """バイナリ STL（法線は三角形から計算）。"""
    v = mesh.positions[mesh.indices]                      # (M, 3, 3)
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(v), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, v
    with open(path, "wb") as f:
        f.write(b"shape-param-lab".ljust(80, b" "))
        f.write(struct.pack("<I", len(v)))
        f.write(rec.tobytes())


# ---------------------------------------------------------------------------
# 面の情報
# ---------------------------------------------------------------------------

def _iter(shape, kind):
    e = TopExp_Explorer(shape, kind)
    while e.More():
        yield e.Current()
        e.Next()


def face_map(shape: TopoDS_Shape) -> TopTools_IndexedMapOfShape:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return m


def faces(shape: TopoDS_Shape) -> list:
    m = face_map(shape)
    return [_to_face(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def _vec(v) -> list[float]:
    return [round(v.X(), 6), round(v.Y(), 6), round(v.Z(), 6)]


def surface_point_normal(face) -> tuple[np.ndarray, np.ndarray]:
    """面の UV 中央の点と、ソリッドの外向き法線。"""
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    u, v = (u0 + u1) / 2, (v0 + v1) / 2
    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(u, v, p, n)  # 面の向き（REVERSED）込みの法線
    nn = np.array([n.X(), n.Y(), n.Z()])
    ln = np.linalg.norm(nn)
    return np.array([p.X(), p.Y(), p.Z()]), (nn / ln if ln > 0 else nn)


@dataclass
class FaceInfo:
    id: int
    kind: str
    kind_code: int
    area: float
    center: list[float]
    point: list[float]
    normal: list[float]
    radius: float | None = None
    axis_loc: list[float] | None = None
    axis_dir: list[float] | None = None
    convex: bool | None = None   # 円筒: True=ボス（外側）, False=穴（内側）

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        if self.kind_code == 0:
            d["label"] = f"#{self.id} 平面"
        elif self.radius is not None:
            what = "" if self.convex is None else ("（外側＝ボス）" if self.convex else "（内側＝穴）")
            d["label"] = f"#{self.id} {self.kind} φ{2 * self.radius:.3g}{what}"
        else:
            d["label"] = f"#{self.id} {self.kind}"
        return d


def face_info(face, idx: int) -> FaceInfo:
    ad = BRepAdaptor_Surface(face)
    code = int(ad.GetType().value)
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    pt, n = surface_point_normal(face)
    info = FaceInfo(idx, SURFACE_NAMES.get(code, "その他"), code, round(props.Mass(), 6),
                    _vec(props.CentreOfMass()), [round(float(x), 6) for x in pt], [round(float(x), 6) for x in n])
    if code == 1:  # 円筒
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        rel = pt - loc
        radial = rel - rel.dot(d) * d
        info.radius = round(cyl.Radius(), 6)
        info.axis_loc, info.axis_dir = loc.round(6).tolist(), d.round(6).tolist()
        info.convex = bool(n.dot(radial) > 0)
    elif code == 3:
        info.radius = round(ad.Sphere().Radius(), 6)
        info.convex = bool(n.dot(pt - np.array(_vec(ad.Sphere().Location()))) > 0)
    elif code == 2:
        info.radius = round(ad.Cone().RefRadius(), 6)
    return info


def all_face_info(shape) -> list[FaceInfo]:
    return [face_info(f, i) for i, f in enumerate(faces(shape))]


def mass_props(shape) -> dict:
    vp, sp = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    BRepGProp.SurfaceProperties_s(shape, sp)
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    lo, hi = b.CornerMin(), b.CornerMax()
    lo, hi = [lo.X(), lo.Y(), lo.Z()], [hi.X(), hi.Y(), hi.Z()]
    return {"volume": vp.Mass(), "area": sp.Mass(), "bbox_min": lo, "bbox_max": hi,
            "size": [h - l for h, l in zip(hi, lo)]}


# ---------------------------------------------------------------------------
# 面移動（寸法変更）
# ---------------------------------------------------------------------------

@dataclass
class MorphResult:
    shape: TopoDS_Shape
    origin: list[int]        # 変形後の面 i → 元の面番号（対応が無い面は -1）
    method: str = "none"     # none / offset（精密）/ boolean（近似）


def apply_offsets(shape: TopoDS_Shape, offsets: dict[int, float], tol: float = 1e-6,
                  method: str = "auto") -> MorphResult:
    """face 番号 → 法線方向の移動量 [mm]（+ で外側へ＝肉が増える）。

    method
      auto    : まず精密（面オフセット）を試し、失敗したら近似（ブーリアン）で作る
      offset  : 精密のみ。隣の面が自然に伸び縮みする。リブがボスに付く形などで失敗しやすい
      boolean : 近似のみ。平面は押し出しを足す/削る、円筒は筒を足す/削る。隣の面は動かす面の法線方向に伸びる
    """
    src = faces(shape)
    offsets = {int(k): float(v) for k, v in offsets.items() if abs(float(v)) > 1e-9}
    if not offsets:
        return MorphResult(shape, list(range(len(src))), "none")
    bad = [k for k in offsets if not 0 <= k < len(src)]
    if bad:
        raise CadError(f"面番号が範囲外です: {bad}")
    infos = [face_info(f, i) for i, f in enumerate(src)]
    first_err = None
    if method in ("auto", "offset"):
        try:
            out = _offset_precise(shape, src, offsets, tol)
            return MorphResult(out, match_origin(infos, offsets, out), "offset")
        except CadError as e:
            if method == "offset":
                raise
            first_err = e
    try:
        out = _push_pull(shape, infos, offsets)
    except CadError as e:
        raise CadError(f"{e}" + (f"（精密方式も失敗: {first_err}）" if first_err else "")) from e
    return MorphResult(out, match_origin(infos, offsets, out), "boolean")


def _finish(out: TopoDS_Shape, what: str) -> TopoDS_Shape:
    if out is None or out.IsNull():
        raise CadError(f"形状を作れませんでした（{what}）")
    solids = list(_iter(out, TopAbs_SOLID))
    if not solids:
        raise CadError(f"形状を作れませんでした（{what}: ソリッドが残りません）")
    if len(solids) > 1:
        raise CadError(f"形状が {len(solids)} 個に分かれました。動かす量が大きすぎる可能性があります")
    out = solids[0]
    if not BRepCheck_Analyzer(out).IsValid():
        raise CadError("変形後の形状が不正（自己交差など）になりました。動かす量を小さくしてください。")
    return out


def _offset_precise(shape, src, offsets, tol) -> TopoDS_Shape:
    mo = BRepOffset_MakeOffset()
    mo.Initialize(shape, 0.0, tol, BRepOffset_Skin, False, False, GeomAbs_Intersection, False)
    for k, v in offsets.items():
        mo.SetOffsetOnFace(src[k], v)
    try:
        mo.MakeOffsetShape()
    except Standard_Failure as e:
        raise CadError(f"面オフセットで例外: {e}") from e
    if not mo.IsDone():
        raise CadError(f"面オフセットに失敗: {str(mo.Error()).split('_')[-1]}")
    return _finish(mo.Shape(), "面オフセット")


# --- 近似方式（ブーリアンによる押し出し/削り） ------------------------------------

def _unify(shape):
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    u.Build()
    return u.Shape()


def _bbox(shape, gap: float = 0.0):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b)
    if gap:
        b.Enlarge(gap)
    return b


def _same_surface(f: FaceInfo, g: FaceInfo, shift: float, tol: float) -> bool:
    """g が「f を shift だけ法線方向に動かした面」と同じ曲面上にあるか。"""
    if f.kind_code != g.kind_code:
        return False
    if f.kind_code == 0:
        n = np.array(f.normal)
        if float(n.dot(np.array(g.normal))) < 0.9999:
            return False
        return abs(float((np.array(g.point) - np.array(f.point)).dot(n)) - shift) < tol
    if f.kind_code == 1:
        d1, d2 = np.array(f.axis_dir), np.array(g.axis_dir)
        if abs(float(d1.dot(d2))) < 0.9999 or f.convex != g.convex:
            return False
        rel = np.array(g.axis_loc) - np.array(f.axis_loc)
        if np.linalg.norm(rel - rel.dot(d1) * d1) > tol:
            return False
        r_new = f.radius + shift * (1 if f.convex else -1)
        return abs(g.radius - r_new) < tol
    return False


def _find_current(shape, f: FaceInfo, f_shape, gap: float, tol: float) -> list:
    """変形途中の形状から、元の面 f と同じ曲面上で重なる面を探す。"""
    box = _bbox(f_shape, gap)
    out = []
    for i, g in enumerate(faces(shape)):
        gi = face_info(g, i)
        if _same_surface(f, gi, 0.0, tol) and not box.IsOut(_bbox(g)):
            out.append(g)
    return out


def _tube(face, info: FaceInfo, r_a: float, r_b: float):
    """円筒面 face の角度範囲・軸方向範囲で、半径 r_a〜r_b の筒（ソリッド）。"""
    ad = BRepAdaptor_Surface(face)
    ax3 = ad.Cylinder().Position()
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    D, X, Y = gp_Vec(ax3.Direction()), gp_Vec(ax3.XDirection()), gp_Vec(ax3.YDirection())
    # 曲面上の点 = loc + r(cos u·X + sin u·Y) + v·D。円筒プリミティブは N=X×Y まわりに角度を測るので、
    # Ax3 が左手系（N = -D）のときは v1 側を底にして -D 方向へ伸ばす
    N = X.Crossed(Y)
    base_v = v0 if N.Dot(D) > 0 else v1
    loc = ax3.Location().Translated(D * base_v)
    dd, xd = gp_Dir(N), gp_Dir(X * math.cos(u0) + Y * math.sin(u0))
    h, ang = v1 - v0, min(u1 - u0, 2 * math.pi)
    full = ang > 2 * math.pi - 1e-6
    lo, hi = min(r_a, r_b), max(r_a, r_b)
    mk = (lambda r: BRepPrimAPI_MakeCylinder(gp_Ax2(loc, dd, xd), r, h).Shape()) if full else \
         (lambda r: BRepPrimAPI_MakeCylinder(gp_Ax2(loc, dd, xd), r, h, ang).Shape())
    outer = mk(hi)
    if lo < 1e-6:
        return outer
    return BRepAlgoAPI_Cut(outer, mk(lo)).Shape()


def _push_pull(shape, infos: list[FaceInfo], offsets: dict[int, float]) -> TopoDS_Shape:
    src = faces(shape)
    size = max(mass_props(shape)["size"])
    tol = max(size * 1e-6, 1e-5)
    gap = max(abs(v) for v in offsets.values()) + tol * 10
    cur = shape
    for k, d in offsets.items():
        f = infos[k]
        if f.kind_code not in (0, 1):
            raise CadError(f"近似方式は平面と円筒面だけ動かせます（#{k} は{f.kind}）")
        targets = _find_current(cur, f, src[k], gap, tol * 100)
        if not targets:
            raise CadError(f"#{k} の面が途中で消えました（他の寸法と干渉している可能性）")
        tools = []
        for g in targets:
            if f.kind_code == 0:
                tools.append(BRepPrimAPI_MakePrism(g, gp_Vec(*f.normal) * d).Shape())
            else:
                gi = face_info(g, 0)
                r1 = gi.radius + d * (1 if gi.convex else -1)
                if r1 <= tol:
                    raise CadError(f"#{k} の半径が 0 以下になります")
                tools.append(_tube(g, gi, gi.radius, r1))
        tool = tools[0]
        for t in tools[1:]:
            tool = BRepAlgoAPI_Fuse(tool, t).Shape()
        op = BRepAlgoAPI_Fuse(cur, tool) if d > 0 else BRepAlgoAPI_Cut(cur, tool)
        if not op.IsDone():
            raise CadError(f"#{k} の{'押し出し' if d > 0 else '削り'}に失敗しました")
        cur = _finish(_unify(op.Shape()), f"#{k} の移動")
        if f.kind_code == 1 and f.convex and d < 0:
            cur = _reattach(cur, f, d, tol)
    return cur


def _reattach(cur, f: FaceInfo, d: float, tol: float):
    """ボスを細くしたとき、ボスに付いていたリブなどを新しい円筒まで延ばす。

    削った後、付いていた形状の端面は元の円筒（半径 r0、軸側を向く面）上に残るので、
    その面を軸方向へ押し出し、削った筒の範囲だけ切り取って足す。
    """
    r0, r1 = f.radius, f.radius + d
    axis = np.array(f.axis_dir)
    loc = np.array(f.axis_loc)
    ends = []
    for i, g in enumerate(faces(cur)):
        gi = face_info(g, i)
        if gi.kind_code != 1 or gi.convex or abs(gi.radius - r0) > tol * 100:
            continue
        rel = np.array(gi.axis_loc) - loc
        if abs(float(np.dot(gi.axis_dir, axis))) < 0.9999 or np.linalg.norm(rel - rel.dot(axis) * axis) > tol * 100:
            continue
        ends.append((g, gi))
    if not ends:
        return cur
    # 削った筒（r1〜r0、軸方向は十分長く）の範囲だけ足す（中の穴を埋めないように）
    span = max(mass_props(cur)["size"]) * 2
    base = gp_Pnt(*(loc - axis * span))
    ax2 = gp_Ax2(base, gp_Dir(*axis))
    clip = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(ax2, r0 + tol, span * 2).Shape(),
                           BRepPrimAPI_MakeCylinder(ax2, max(r1 - tol * 10, tol), span * 2).Shape()).Shape()
    out = cur
    for g, gi in ends:
        n = np.array(gi.normal)  # 軸側を向く
        prism = BRepPrimAPI_MakePrism(g, gp_Vec(*(n * (2 * r0 - r1)))).Shape()
        add = BRepAlgoAPI_Common(prism, clip).Shape()
        op = BRepAlgoAPI_Fuse(out, add)
        if op.IsDone():
            out = op.Shape()
    return _finish(_unify(out), "ボスに付いた形状の延長")


def match_origin(infos: list[FaceInfo], offsets: dict[int, float], out) -> list[int]:
    """変形後の各面が元のどの面に当たるか（同じ曲面＋移動量で照合）。色分け・選択の引き継ぎ用。"""
    size = max(mass_props(out)["size"])
    tol = max(size * 1e-5, 1e-4)
    origin = []
    for i, g in enumerate(faces(out)):
        gi = face_info(g, i)
        cands = [f for f in infos if _same_surface(f, gi, offsets.get(f.id, 0.0), tol)]
        if not cands:
            origin.append(-1)
            continue
        c = np.array(gi.center)
        origin.append(min(cands, key=lambda f: float(np.linalg.norm(np.array(f.center) - c))).id)
    return origin


# ---------------------------------------------------------------------------
# メッシュ化
# ---------------------------------------------------------------------------

@dataclass
class Mesh:
    positions: np.ndarray   # (N, 3) float32
    indices: np.ndarray     # (M, 3) uint32
    face_ids: np.ndarray    # (M,) int32  三角形 → 面番号

    def to_bytes(self) -> bytes:
        """[uint32 N][uint32 M][float32 N*3][uint32 M*3][int32 M]（リトルエンディアン）"""
        return (struct.pack("<II", len(self.positions), len(self.indices))
                + self.positions.astype("<f4").tobytes()
                + self.indices.astype("<u4").tobytes()
                + self.face_ids.astype("<i4").tobytes())


def default_deflection(shape) -> float:
    s = mass_props(shape)["size"]
    return max(max(s) * 0.002, 0.01)


def tessellate(shape, deflection: float | None = None, angle: float = 0.35,
               face_labels: list[int] | None = None) -> Mesh:
    if deflection is None:
        deflection = default_deflection(shape)
    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    pos, idx, fid = [], [], []
    base = 0
    for i, face in enumerate(faces(shape)):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        nn = tri.NbNodes()
        pts = np.empty((nn, 3))
        for k in range(nn):
            p = tri.Node(k + 1).Transformed(trsf)
            pts[k] = (p.X(), p.Y(), p.Z())
        nt = tri.NbTriangles()
        tr = np.empty((nt, 3), dtype=np.int64)
        for k in range(nt):
            tr[k] = tri.Triangle(k + 1).Get()
        tr -= 1
        if face.Orientation() == TopAbs_REVERSED:
            tr = tr[:, ::-1]
        pos.append(pts)
        idx.append(tr + base)
        fid.append(np.full(nt, face_labels[i] if face_labels else i, dtype=np.int32))
        base += nn
    if not pos:
        raise CadError("メッシュを作れませんでした")
    return Mesh(np.concatenate(pos).astype(np.float32), np.concatenate(idx).astype(np.uint32),
                np.concatenate(fid))


def angle_deg(a, b) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b))))))
