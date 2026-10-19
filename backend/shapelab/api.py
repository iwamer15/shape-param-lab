"""HTTP API（FastAPI の APIRouter）。

単体サーバ（server.py）でも、他のアプリ（ml-process-lab など）に埋め込んでも使えるよう、
ルータを作る関数だけを公開する:

    from shapelab.api import make_router
    app.include_router(make_router(Path("data/shape")), prefix="/api/shape")

data_dir の中身
    models/    … 元の形状（STEP / IGES / BREP / STL / INP）
    projects/  … パラメータ定義（*.json）
    outputs/   … 一括生成した形状（outputs/<名前>/sample_XXX/...）
"""

from __future__ import annotations

import io
import json
import re
import shutil
import struct
import threading
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import cad, demo, doe
from .generate import LOCK, generate
from .models import ALL_SUFFIXES, load_model
from .params import KINDS, base_value, check_params, face_offsets


class MorphIn(BaseModel):
    model: str
    seg_angle: float = 30.0
    params: list[dict] = Field(default_factory=list)
    values: dict[str, float] = Field(default_factory=dict)
    method: str = "auto"
    deflection: float | None = None


class CheckIn(BaseModel):
    model: str
    seg_angle: float = 30.0
    params: list[dict] = Field(default_factory=list)


class DoeIn(BaseModel):
    model: str
    seg_angle: float = 30.0
    params: list[dict]
    method: str = "lhs"
    n: int = 20
    seed: int = 0
    levels: int = 3
    constraints: list[str] = Field(default_factory=list)


class RangeIn(BaseModel):
    model: str
    seg_angle: float = 30.0
    params: list[dict]
    method: str = "auto"
    constraints: list[str] = Field(default_factory=list)


class ProjectIn(BaseModel):
    name: str
    project: dict


class GenerateIn(BaseModel):
    project: dict              # {"model", "params", "doe": {...}, "seg_angle"}
    out_name: str
    formats: list[str] = Field(default_factory=lambda: ["stl", "step", "inp"])
    method: str = "auto"
    samples: list[dict[str, float]] | None = None   # 指定が無ければ doe から作る
    overwrite: bool = False


def _safe(name: str) -> str:
    name = Path(name).name.strip()
    if not name or name.startswith(".") or not re.fullmatch(r"[^\\/:*?\"<>|]+", name):
        raise HTTPException(400, f"使えない名前です: {name!r}")
    return name


def _pack(info: dict, mesh: cad.Mesh) -> Response:
    """[uint32 JSON長][JSON(UTF-8)][メッシュ]"""
    js = json.dumps(info, ensure_ascii=False).encode("utf-8")
    return Response(struct.pack("<I", len(js)) + js + mesh.to_bytes(), media_type="application/octet-stream")


def make_router(data_dir: Path) -> APIRouter:
    data_dir = Path(data_dir)
    models_dir, projects_dir, outputs_dir = data_dir / "models", data_dir / "projects", data_dir / "outputs"
    for d in (models_dir, projects_dir, outputs_dir):
        d.mkdir(parents=True, exist_ok=True)

    router = APIRouter(tags=["shape-param-lab"])
    cache: dict[tuple, tuple[float, object]] = {}
    jobs: dict[str, dict] = {}

    def load(name: str, seg_angle: float = 30.0):
        """形状を読み込んでキャッシュ（メッシュは面の分割角度ごと）。"""
        path = models_dir / _safe(name)
        if not path.exists():
            raise HTTPException(404, f"モデルがありません: {name}")
        mt = path.stat().st_mtime
        key = (name, round(float(seg_angle), 3) if path.suffix.lower() in (".stl", ".inp") else None)
        hit = cache.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        with LOCK:
            try:
                model = load_model(path, seg_angle)
            except cad.CadError as e:
                raise HTTPException(400, str(e))
        cache[key] = (mt, model)
        same = [k for k in cache if k[0] == name]
        for k in same[:-3]:   # 分割角度違いは新しい 3 つまで残す
            cache.pop(k, None)
        return model

    def _clear(name):
        for k in [k for k in cache if k[0] == name]:
            cache.pop(k, None)

    # ---- モデル --------------------------------------------------------------

    @router.get("/meta")
    def meta():
        return {"kinds": KINDS, "doe_methods": doe.METHODS, "suffixes": list(ALL_SUFFIXES),
                "data_dir": str(data_dir.resolve())}

    @router.get("/models")
    def list_models():
        out = []
        for p in sorted(models_dir.iterdir()):
            if p.suffix.lower() in ALL_SUFFIXES:
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
        return out

    @router.post("/models/upload")
    async def upload(file: UploadFile = File(...)):
        name = _safe(file.filename or "model.step")
        if Path(name).suffix.lower() not in ALL_SUFFIXES:
            raise HTTPException(400, f"対応形式は {', '.join(ALL_SUFFIXES)} です")
        dst = models_dir / name
        with open(dst, "wb") as f:
            shutil.copyfileobj(file.file, f)
        _clear(name)
        try:
            load(name)
        except HTTPException:
            dst.unlink(missing_ok=True)
            raise
        return {"name": name}

    @router.post("/models/demo")
    def make_demo(kind: str = "step"):
        with LOCK:
            path = {"stl": demo.write_demo_stl, "fillet": demo.write_fillet_demo_stl}.get(kind, demo.write_demo)(models_dir)
        _clear(path.name)
        model = load(path.name)
        params = demo.demo_params(model.infos)
        if kind == "fillet":
            cone = next((f for f in model.infos if f.kind_code == 2 and f.fillet_r is None and f.radius), None)
            if cone is not None:
                params.append({"id": "p8", "key": "draft_d", "name": "抜き勾配ボスの根元径", "kind": "diameter",
                               "faces": [{"face": cone.id, "coef": 1.0}], "min": 15, "max": 21, "step": 0.5})
        project = {"version": 1, "model": path.name, "params": params,
                   "doe": {"method": "lhs", "n": 20, "seed": 0, "levels": 3}}
        pname = {"stl": "demo_stl", "fillet": "demo_fillet"}.get(kind, "demo")
        if kind in ("stl", "fillet"):
            project["seg_angle"] = 30.0
            project["doe"]["constraints"] = ["rib_h <= bossB_h - 3", "rib_h - plate_t >= 8"]
        (projects_dir / f"{pname}.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), "utf-8")
        return {"name": path.name, "project": pname, "data": project}

    @router.delete("/models/{name}")
    def delete_model(name: str):
        (models_dir / _safe(name)).unlink(missing_ok=True)
        _clear(name)
        return {"ok": True}

    @router.get("/models/{name}/info")
    def model_info(name: str, seg_angle: float = 30.0):
        m = load(name, seg_angle)
        return {"name": name, "faces": [f.to_dict() for f in m.infos], "mass": m.mass(), "model": m.describe(),
                "formats": m.formats, "methods": m.methods}

    @router.get("/models/{name}/mesh")
    def model_mesh(name: str, seg_angle: float = 30.0):
        m = load(name, seg_angle)
        with LOCK:
            mesh = m.base_mesh()
        return _pack({"faces": len(m.infos)}, mesh)

    # ---- パラメータ・変形 ------------------------------------------------------

    @router.post("/check")
    def check(body: CheckIn):
        return check_params(body.params, load(body.model, body.seg_angle).infos)

    @router.post("/morph")
    def morph(body: MorphIn):
        m = load(body.model, body.seg_angle)
        t = time.time()
        # 大きな解析メッシュは、操作中は表面だけ動かす（内部節点・要素の反転確認は生成時に行う）
        full = not (m.kind == "mesh" and len(m.data.nodes) > 300_000)
        with LOCK:
            try:
                offs = face_offsets(body.params, body.values, m.infos)
                res = m.morph(offs, method=body.method, full=full)
            except cad.CadError as e:
                raise HTTPException(422, str(e))
        info = {"method": res.method, "offsets": {str(k): v for k, v in offs.items()}, "quality": res.quality,
                "mass": res.mass, "seconds": round(time.time() - t, 3), "full": full}
        return _pack(info, res.mesh)

    @router.post("/range-check")
    def range_check(body: RangeIn):
        """各パラメータを 1 つずつ最小・最大にしたとき＋全部最小・全部最大で形状が作れるか。"""
        m = load(body.model, body.seg_angle)
        active = [p for p in body.params if p.get("enabled") is not False]
        cases = []
        for p in active:
            for end in ("min", "max"):
                cases.append((f"{p.get('name') or p['key']} = {p[end]}（{'最小' if end == 'min' else '最大'}）",
                              {p["id"]: float(p[end])}))
        if len(active) > 1:
            cases.append(("全部 最小", {p["id"]: float(p["min"]) for p in active}))
            cases.append(("全部 最大", {p["id"]: float(p["max"]) for p in active}))
        try:
            feasible, ncons = doe.compile_constraints(body.constraints, [p["key"] for p in active])
        except ValueError as e:
            raise HTTPException(400, str(e))
        out = []
        for label, vals in cases:
            if ncons:
                env = {p["key"]: np.array([vals.get(p["id"], base_value(p, m.infos)["base"])]) for p in active}
                if not feasible(env)[0]:
                    out.append({"label": label, "values": vals, "ok": True, "skipped": True, "method": "制約外"})
                    continue
            t = time.time()
            with LOCK:
                try:
                    res = m.morph(face_offsets(active, vals, m.infos), method=body.method, full=True)
                    out.append({"label": label, "values": vals, "ok": True, "method": res.method, "quality": res.quality,
                                "volume": res.mass["volume"], "seconds": round(time.time() - t, 3)})
                except cad.CadError as e:
                    out.append({"label": label, "values": vals, "ok": False, "error": str(e)})
        return out

    @router.post("/doe")
    def doe_preview(body: DoeIn):
        infos = load(body.model, body.seg_angle).infos
        bases = {p["id"]: base_value(p, infos)["base"] for p in body.params}
        try:
            return doe.sample(body.params, body.n, body.method, body.seed, body.levels, bases, body.constraints)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---- プロジェクト（パラメータ定義の保存） -----------------------------------

    @router.get("/projects")
    def list_projects():
        return [{"name": p.stem, "mtime": p.stat().st_mtime} for p in sorted(projects_dir.glob("*.json"))]

    @router.get("/projects/{name}")
    def get_project(name: str):
        p = projects_dir / f"{_safe(name)}.json"
        if not p.exists():
            raise HTTPException(404, "プロジェクトがありません")
        return json.loads(p.read_text("utf-8"))

    @router.post("/projects")
    def save_project(body: ProjectIn):
        p = projects_dir / f"{_safe(body.name)}.json"
        p.write_text(json.dumps(body.project, ensure_ascii=False, indent=2), "utf-8")
        return {"name": p.stem}

    @router.delete("/projects/{name}")
    def delete_project(name: str):
        (projects_dir / f"{_safe(name)}.json").unlink(missing_ok=True)
        return {"ok": True}

    # ---- 一括生成 --------------------------------------------------------------

    @router.post("/generate")
    def start_generate(body: GenerateIn):
        pj = body.project
        model = load(pj["model"], float(pj.get("seg_angle", 30.0)))
        infos = model.infos
        params = [p for p in pj.get("params", []) if p.get("enabled") is not False]
        errs = [c for c in check_params(params, infos) if c["error"]]
        if errs:
            raise HTTPException(400, "パラメータに問題があります: " + " / ".join(e["error"] for e in errs))
        out = outputs_dir / _safe(body.out_name)
        if out.exists():
            if not body.overwrite:
                raise HTTPException(409, f"出力先 {out.name} は既にあります（上書きする場合は overwrite）")
            shutil.rmtree(out)
        if body.samples is not None:
            samples = body.samples
        else:
            d = pj.get("doe", {})
            bases = {p["id"]: base_value(p, infos)["base"] for p in params}
            try:
                samples = doe.sample(params, int(d.get("n", 20)), d.get("method", "lhs"), int(d.get("seed", 0)),
                                     int(d.get("levels", 3)), bases, d.get("constraints") or [])
            except ValueError as e:
                raise HTTPException(400, str(e))
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "out": out.name, "total": len(samples), "done": 0, "ok": 0, "failed": 0,
               "status": "running", "rows": [], "started": time.time(), "cancel": False, "error": None}
        jobs[jid] = job

        def prog(i, n, row):
            job["done"] = i
            job["ok" if row["status"] == "ok" else "failed"] += 1
            job["rows"].append(row)

        def run():
            try:
                generate(model, params, samples, out, formats=tuple(body.formats),
                         method=body.method, project={**pj, "samples": samples}, progress=prog,
                         cancel=lambda: job["cancel"])
                job["status"] = "cancelled" if job["cancel"] else "done"
            except Exception as e:
                job["status"], job["error"] = "error", str(e)
            job["finished"] = time.time()

        threading.Thread(target=run, daemon=True).start()
        return {"job": jid, "total": len(samples), "out": out.name, "path": str(out.resolve())}

    @router.get("/jobs/{jid}")
    def job_status(jid: str, since: int = 0):
        job = jobs.get(jid)
        if not job:
            raise HTTPException(404, "ジョブがありません")
        return {k: v for k, v in job.items() if k != "rows"} | {"rows": job["rows"][since:]}

    @router.post("/jobs/{jid}/cancel")
    def job_cancel(jid: str):
        if jid in jobs:
            jobs[jid]["cancel"] = True
        return {"ok": True}

    @router.get("/outputs")
    def list_outputs():
        out = []
        for d in sorted(outputs_dir.iterdir()):
            if d.is_dir():
                out.append({"name": d.name, "samples": len(list(d.glob("sample_*"))), "path": str(d.resolve()),
                            "mtime": d.stat().st_mtime})
        return out

    @router.get("/outputs/{name}/zip")
    def output_zip(name: str):
        d = outputs_dir / _safe(name)
        if not d.is_dir():
            raise HTTPException(404, "出力がありません")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in d.rglob("*"):
                if p.is_file():
                    z.write(p, Path(d.name) / p.relative_to(d))
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{d.name}.zip"'.encode(
                                     "ascii", "ignore").decode()})

    @router.get("/outputs/{name}/{sample}/mesh")
    def output_mesh(name: str, sample: str):
        """生成済みサンプルの STEP を読み直して表示（STL しか無ければ表示不可）。"""
        p = outputs_dir / _safe(name) / _safe(sample) / "geometry.step"
        if not p.exists():
            raise HTTPException(404, "STEP がありません")
        with LOCK:
            shape = cad.load_shape(p)
            mesh = cad.tessellate(shape)
        return _pack({}, mesh)

    return router
