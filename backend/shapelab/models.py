"""CAD（STEP など）とメッシュ（STL / .inp）を同じ操作で扱うための入れ物。

どちらも「面の一覧（FaceInfo）」「表示用メッシュ」「面の移動量 → 変形」「書き出し」を持つ。
パラメータ（params.py）は面の一覧だけを見るので、形式によらず同じ寸法定義が使える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import cad, meshio_, meshmorph, recognize
from .cad import CadError

MESH_SUFFIXES = (".stl", ".inp")
ALL_SUFFIXES = cad.CAD_SUFFIXES + MESH_SUFFIXES


@dataclass
class MorphOut:
    mesh: cad.Mesh                 # 表示用（面番号 = 元の面番号）
    mass: dict
    method: str                    # none / offset / boolean / morph
    quality: dict = field(default_factory=dict)
    handle: object = None          # CAD: TopoDS_Shape, メッシュ: 変形後の節点座標


class CadModel:
    kind = "cad"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.shape = cad.load_shape(self.path)
        self.infos = cad.all_face_info(self.shape)
        self._mass = cad.mass_props(self.shape)
        self.deflection = cad.default_deflection(self.shape)
        self.formats = ["stl", "step"]
        self.methods = ["auto", "offset", "boolean"]

    def describe(self) -> dict:
        return {"type": "cad", "label": f"CAD（{self.path.suffix[1:].upper()}）", "faces": len(self.infos),
                "warnings": []}

    def mass(self) -> dict:
        return self._mass

    def base_mesh(self) -> cad.Mesh:
        return cad.tessellate(self.shape, self.deflection)

    def morph(self, offsets: dict[int, float], method: str = "auto", full: bool = True) -> MorphOut:
        res = cad.apply_offsets(self.shape, offsets, method=method if method in self.methods else "auto")
        mesh = cad.tessellate(res.shape, self.deflection, face_labels=res.origin)
        return MorphOut(mesh, cad.mass_props(res.shape), res.method, {}, res.shape)

    def export(self, out: MorphOut, d: Path, formats) -> list[str]:
        files = []
        if "stl" in formats:
            cad.write_stl(cad.tessellate(out.handle, self.deflection), d / "geometry.stl")
            files.append("geometry.stl")
        if "step" in formats:
            cad.write_step(out.handle, d / "geometry.step")
            files.append("geometry.step")
        return files


class MeshModel:
    kind = "mesh"

    def __init__(self, path: Path, seg_angle: float = 30.0, seg_tol: float | None = None):
        self.path = Path(path)
        suf = self.path.suffix.lower()
        try:
            self.data = meshio_.read_stl(self.path) if suf == ".stl" else meshio_.read_inp(self.path)
        except meshio_.MeshError as e:
            raise CadError(str(e)) from e
        self.seg_angle = float(seg_angle)
        P, T = self.data.nodes, self.data.tris
        self.seg_tol = float(seg_tol) if seg_tol else recognize.default_tol(P, T)
        self.label, self.patches = recognize.segment(P, T, self.seg_angle, self.seg_tol)
        self.infos = recognize.patch_infos(P, T, self.patches)
        self.morpher = meshmorph.Morpher(self.data, self.label, self.patches)
        self._mass = meshmorph.mesh_mass(P, T)
        self.formats = ["stl", "inp"] if self.data.source == "inp" else ["stl"]
        self.methods = ["morph"]

    def describe(self) -> dict:
        d = self.data
        nel = sum(len(b.conn) for b in d.blocks)
        types = sorted({b.name or b.etype for b in d.blocks})
        label = "メッシュ（STL）" if d.source == "stl" else f"メッシュ（INP: 要素 {nel:,} 個・{'/'.join(types)}）"
        kinds = [p.kind_code for p in self.patches]
        R = recognize
        return {"type": "mesh", "source": d.source, "label": label, "nodes": int(len(d.nodes)), "tris": int(len(d.tris)),
                "elements": nel, "faces": len(self.patches), "seg_angle": self.seg_angle, "seg_tol": self.seg_tol,
                "planes": kinds.count(R.PLANE), "cylinders": kinds.count(R.CYLINDER), "cones": kinds.count(R.CONE),
                "spheres": kinds.count(R.SPHERE), "tori": kinds.count(R.TORUS), "others": kinds.count(R.OTHER),
                "warnings": d.warnings}

    def mass(self) -> dict:
        return self._mass

    def base_mesh(self) -> cad.Mesh:
        return cad.Mesh(self.data.nodes.astype(np.float32), self.data.tris.astype(np.uint32), self.label.astype(np.int32))

    def morph(self, offsets: dict[int, float], method: str = "morph", full: bool = True) -> MorphOut:
        offsets = {int(k): float(v) for k, v in offsets.items() if abs(float(v)) > 1e-12}
        if not offsets:
            return MorphOut(self.base_mesh(), self._mass, "none", {}, self.data.nodes)
        U = self.morpher.displacement(offsets, full=full)
        Pn = self.data.nodes + U
        q = self.morpher.quality(Pn)
        try:
            meshmorph.check_quality(q)
        except meshio_.MeshError as e:
            raise CadError(str(e)) from e
        mesh = cad.Mesh(Pn.astype(np.float32), self.data.tris.astype(np.uint32), self.label.astype(np.int32))
        return MorphOut(mesh, meshmorph.mesh_mass(Pn, self.data.tris), "morph", q, Pn)

    def export(self, out: MorphOut, d: Path, formats) -> list[str]:
        files = []
        if "stl" in formats:
            meshio_.write_stl(out.handle, self.data.tris, d / "geometry.stl")
            files.append("geometry.stl")
        if "inp" in formats and self.data.inp is not None:
            meshio_.write_inp(self.data.inp, out.handle, d / f"{self.path.stem}.inp")
            files.append(f"{self.path.stem}.inp")
        return files


def load_model(path: str | Path, seg_angle: float = 30.0, seg_tol: float | None = None):
    path = Path(path)
    suf = path.suffix.lower()
    if suf in MESH_SUFFIXES:
        return MeshModel(path, seg_angle, seg_tol)
    return CadModel(path)
