"""実験計画に沿って形状を一括生成し、サロゲート学習用のフォルダに書き出す。

出力（ml-process-lab / surrogate-manager の data/sample_XXX 形式に合わせる）
  <out>/
    doe_table.csv          … 全サンプルのパラメータ・体積・表面積・成否の一覧
    project.json           … 生成に使った定義（再現用）
    sample_001/
      geometry.stl         … 形状（学習・ボクセル化用）
      geometry.step        … 形状（CAE にそのまま渡す用）
      conditions.csv       … "name,value" 形式の寸法パラメータ（学習の入力条件）
      shape.json           … 寸法・面の移動量・方式・体積など
    sample_002/ ...
"""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Callable

from . import cad
from .params import base_value, face_offsets

LOCK = threading.RLock()  # OpenCascade の処理は 1 つずつ


def generate(shape, params: list[dict], samples: list[dict[str, float]], out_dir: Path, *,
             formats=("stl", "step"), deflection: float | None = None, method: str = "auto",
             project: dict | None = None, progress: Callable[[int, int, dict], None] | None = None,
             cancel: Callable[[], bool] | None = None) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    with LOCK:
        infos = cad.all_face_info(shape)
        base = cad.mass_props(shape)
        if deflection is None:
            deflection = cad.default_deflection(shape)
    active = [p for p in params if p.get("enabled") is not False]
    bases = {p["id"]: base_value(p, infos)["base"] for p in active}
    if project is not None:
        (out_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), "utf-8")
    rows: list[dict] = []
    width = max(3, len(str(len(samples))))
    for i, vals in enumerate(samples, 1):
        if cancel and cancel():
            break
        name = f"sample_{i:0{width}d}"
        row = {"sample": name, "status": "ok", "method": "", "error": ""}
        row.update({p["key"]: vals.get(p["id"], bases[p["id"]]) for p in active})
        t = time.time()
        try:
            with LOCK:
                offs = face_offsets(active, vals, infos)
                res = cad.apply_offsets(shape, offs, method=method)
                mp = cad.mass_props(res.shape)
                d = out_dir / name
                d.mkdir(exist_ok=True)
                if "stl" in formats:
                    cad.write_stl(cad.tessellate(res.shape, deflection), d / "geometry.stl")
                if "step" in formats:
                    cad.write_step(res.shape, d / "geometry.step")
            with open(d / "conditions.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["name", "value"])
                for p in active:
                    w.writerow([p["key"], row[p["key"]]])
            info = {
                "sample": name, "method": res.method,
                "params": [{"key": p["key"], "name": p.get("name", ""), "kind": p["kind"],
                            "value": row[p["key"]], "base": bases[p["id"]]} for p in active],
                "face_offsets": {str(k): v for k, v in offs.items()},
                "volume": mp["volume"], "area": mp["area"], "size": mp["size"],
                "volume_ratio": mp["volume"] / base["volume"],
            }
            (d / "shape.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
            row.update(method=res.method, volume=round(mp["volume"], 3), area=round(mp["area"], 3),
                       size_x=round(mp["size"][0], 4), size_y=round(mp["size"][1], 4), size_z=round(mp["size"][2], 4))
        except Exception as e:  # 1 件の失敗で全体を止めない
            row.update(status="failed", error=str(e))
        row["seconds"] = round(time.time() - t, 3)
        rows.append(row)
        if progress:
            progress(i, len(samples), row)
    _write_table(out_dir / "doe_table.csv", rows, [p["key"] for p in active])
    return rows


def _write_table(path: Path, rows: list[dict], keys: list[str]) -> None:
    cols = ["sample", "status", "method", *keys, "volume", "area", "size_x", "size_y", "size_z", "seconds", "error"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # Excel で文字化けしないよう BOM 付き
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
