"""HTTP API（FastAPI の APIRouter）。

単体サーバ（server.py）でも、他のアプリ（ml-process-lab など）に埋め込んでも使えるよう、
ルータを作る関数だけを公開する:

    from shapelab.api import make_router
    app.include_router(make_router(Path("data/shape")), prefix="/api/shape")

data_dir の中身
    models/    … 元の CAD（STEP / IGES / BREP）
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

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import cad, demo, doe
from .generate import LOCK, generate
from .params import KINDS, base_value, check_params, face_offsets


class MorphIn(BaseModel):
    model: str
    params: list[dict] = Field(default_factory=list)
    values: dict[str, float] = Field(default_factory=dict)
    method: str = "auto"
    deflection: float | None = None


class CheckIn(BaseModel):
    model: str
    params: list[dict] = Field(default_factory=list)


class DoeIn(BaseModel):
    model: str
    params: list[dict]
    method: str = "lhs"
    n: int = 20
    seed: int = 0
    levels: int = 3


class RangeIn(BaseModel):
    model: str
    params: list[dict]
    method: str = "auto"


class ProjectIn(BaseModel):
    name: str
    project: dict


class GenerateIn(BaseModel):
    project: dict              # {"model", "params", "doe": {...}}
    out_name: str
    formats: list[str] = Field(default_factory=lambda: ["stl", "step"])
    deflection: float | None = None
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
    cache: dict[str, tuple[float, object, list]] = {}
    jobs: dict[str, dict] = {}

    def load(name: str):
        path = models_dir / _safe(name)
        if not path.exists():
            raise HTTPException(404, f"モデルがありません: {name}")
        mt = path.stat().st_mtime
        hit = cache.get(name)
        if hit and hit[0] == mt:
            return hit[1], hit[2]
        with LOCK:
            try:
                shape = cad.load_shape(path)
            except cad.CadError as e:
                raise HTTPException(400, str(e))
            infos = cad.all_face_info(shape)
        cache[name] = (mt, shape, infos)
        return shape, infos

    # ---- モデル --------------------------------------------------------------

    @router.get("/meta")
    def meta():
        return {"kinds": KINDS, "doe_methods": doe.METHODS, "suffixes": list(cad.CAD_SUFFIXES),
                "data_dir": str(data_dir.resolve())}

    @router.get("/models")
    def list_models():
        out = []
        for p in sorted(models_dir.iterdir()):
            if p.suffix.lower() in cad.CAD_SUFFIXES:
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
        return out

    @router.post("/models/upload")
    async def upload(file: UploadFile = File(...)):
        name = _safe(file.filename or "model.step")
        if Path(name).suffix.lower() not in cad.CAD_SUFFIXES:
            if Path(name).suffix.lower() == ".stl":
                raise HTTPException(400, "STL は寸法（面・穴径）の情報が無いため使えません。CAD から STEP で書き出してください。")
            raise HTTPException(400, f"対応形式は {', '.join(cad.CAD_SUFFIXES)} です")
        dst = models_dir / name
        with open(dst, "wb") as f:
            shutil.copyfileobj(file.file, f)
        cache.pop(name, None)
        try:
            load(name)
        except HTTPException:
            dst.unlink(missing_ok=True)
            raise
        return {"name": name}

    @router.post("/models/demo")
    def make_demo():
        with LOCK:
            path = demo.write_demo(models_dir)
        cache.pop(path.name, None)
        shape, _ = load(path.name)
        with LOCK:
            params = demo.demo_params(shape)
        project = {"version": 1, "model": path.name, "params": params,
                   "doe": {"method": "lhs", "n": 20, "seed": 0, "levels": 3}}
        (projects_dir / "demo.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), "utf-8")
        return {"name": path.name, "project": "demo", "data": project}

    @router.delete("/models/{name}")
    def delete_model(name: str):
        (models_dir / _safe(name)).unlink(missing_ok=True)
        cache.pop(name, None)
        return {"ok": True}

    @router.get("/models/{name}/info")
    def model_info(name: str):
        shape, infos = load(name)
        with LOCK:
            mp = cad.mass_props(shape)
            defl = cad.default_deflection(shape)
        return {"name": name, "faces": [f.to_dict() for f in infos], "mass": mp, "deflection": defl}

    @router.get("/models/{name}/mesh")
    def model_mesh(name: str, deflection: float | None = None):
        shape, infos = load(name)
        with LOCK:
            mesh = cad.tessellate(shape, deflection)
        return _pack({"faces": len(infos)}, mesh)

    # ---- パラメータ・変形 ------------------------------------------------------

    @router.post("/check")
    def check(body: CheckIn):
        _, infos = load(body.model)
        return check_params(body.params, infos)

    @router.post("/morph")
    def morph(body: MorphIn):
        shape, infos = load(body.model)
        t = time.time()
        with LOCK:
            try:
                offs = face_offsets(body.params, body.values, infos)
                res = cad.apply_offsets(shape, offs, method=body.method)
                mesh = cad.tessellate(res.shape, body.deflection, face_labels=res.origin)
                mp = cad.mass_props(res.shape)
            except cad.CadError as e:
                raise HTTPException(422, str(e))
        info = {"method": res.method, "offsets": {str(k): v for k, v in offs.items()},
                "mass": mp, "seconds": round(time.time() - t, 3)}
        return _pack(info, mesh)

    @router.post("/range-check")
    def range_check(body: RangeIn):
        """各パラメータを 1 つずつ最小・最大にしたとき＋全部最小・全部最大で形状が作れるか。"""
        shape, infos = load(body.model)
        active = [p for p in body.params if p.get("enabled") is not False]
        cases = []
        for p in active:
            for end in ("min", "max"):
                cases.append((f"{p.get('name') or p['key']} = {p[end]}（{'最小' if end == 'min' else '最大'}）",
                              {p["id"]: float(p[end])}))
        if len(active) > 1:
            cases.append(("全部 最小", {p["id"]: float(p["min"]) for p in active}))
            cases.append(("全部 最大", {p["id"]: float(p["max"]) for p in active}))
        out = []
        for label, vals in cases:
            t = time.time()
            with LOCK:
                try:
                    res = cad.apply_offsets(shape, face_offsets(active, vals, infos), method=body.method)
                    out.append({"label": label, "values": vals, "ok": True, "method": res.method,
                                "volume": cad.mass_props(res.shape)["volume"], "seconds": round(time.time() - t, 3)})
                except cad.CadError as e:
                    out.append({"label": label, "values": vals, "ok": False, "error": str(e)})
        return out

    @router.post("/doe")
    def doe_preview(body: DoeIn):
        _, infos = load(body.model)
        bases = {p["id"]: base_value(p, infos)["base"] for p in body.params}
        try:
            return doe.sample(body.params, body.n, body.method, body.seed, body.levels, bases)
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
        shape, infos = load(pj["model"])
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
            samples = doe.sample(params, int(d.get("n", 20)), d.get("method", "lhs"), int(d.get("seed", 0)),
                                 int(d.get("levels", 3)), bases)
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
                generate(shape, params, samples, out, formats=tuple(body.formats), deflection=body.deflection,
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
