"""CLI（画面なしで一括生成）。

  uv run python -m shapelab demo                      # デモ形状と demo.json を data/ に作る
  uv run python -m shapelab info  ../data/models/x.step    # 面の一覧（STL / INP は自動認識した面）
  uv run python -m shapelab generate ../data/projects/demo.json -o ../data/outputs/run1 [-n 30 --method lhs --seed 1]
"""

import argparse
import json
import sys
from pathlib import Path

from . import demo, doe
from .generate import generate
from .models import load_model
from .params import base_value, check_params

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shapelab", description="CAD 寸法のパラメータ化・一括生成")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="デモ形状（STEP）とパラメータ定義を作る")
    a = sub.add_parser("info", help="CAD の面一覧")
    a.add_argument("model")
    a.add_argument("--seg-angle", type=float, default=30.0, help="STL/INP の面の分割角度 [度]")
    g = sub.add_parser("generate", help="プロジェクト JSON に沿って一括生成")
    g.add_argument("project")
    g.add_argument("-o", "--out", required=True)
    g.add_argument("-n", type=int)
    g.add_argument("--method", choices=list(doe.METHODS))
    g.add_argument("--seed", type=int)
    g.add_argument("--levels", type=int)
    g.add_argument("--formats", default="stl,step,inp")
    g.add_argument("--morph", default="auto", choices=["auto", "offset", "boolean"])
    g.add_argument("--models-dir", default=str(ROOT / "data" / "models"))
    args = ap.parse_args(argv)

    if args.cmd == "demo":
        path = demo.write_demo(ROOT / "data" / "models")
        pj = {"version": 1, "model": path.name, "params": demo.demo_params(load_model(path).infos),
              "doe": {"method": "lhs", "n": 20, "seed": 0, "levels": 3}}
        pp = ROOT / "data" / "projects" / "demo.json"
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(json.dumps(pj, ensure_ascii=False, indent=2), "utf-8")
        print(f"作成: {path}\n作成: {pp}")
        return 0

    if args.cmd == "info":
        m = load_model(args.model, args.seg_angle)
        print(m.describe())
        for f in m.infos:
            print(f.to_dict()["label"], "中心", f.center, "法線", f.normal)
        print(m.mass())
        return 0

    pj = json.loads(Path(args.project).read_text("utf-8"))
    mp = Path(pj["model"])
    model = mp if mp.is_absolute() else Path(args.models_dir) / mp
    mdl = load_model(model, float(pj.get("seg_angle", 30.0)))
    infos = mdl.infos
    params = [p for p in pj["params"] if p.get("enabled") is not False]
    errs = [c for c in check_params(params, infos) if c["error"]]
    if errs:
        for e in errs:
            print("エラー:", e["id"], e["error"], file=sys.stderr)
        return 1
    d = dict(pj.get("doe", {}))
    for k in ("n", "method", "seed", "levels"):
        if getattr(args, k) is not None:
            d[k] = getattr(args, k)
    bases = {p["id"]: base_value(p, infos)["base"] for p in params}
    samples = doe.sample(params, int(d.get("n", 20)), d.get("method", "lhs"), int(d.get("seed", 0)),
                         int(d.get("levels", 3)), bases, d.get("constraints") or [])

    def prog(i, n, row):
        msg = "OK" if row["status"] == "ok" else f"失敗: {row['error']}"
        print(f"[{i}/{n}] {row['sample']} {row.get('method', '')} {msg}", flush=True)

    rows = generate(mdl, params, samples, Path(args.out), formats=tuple(args.formats.split(",")),
                    method=args.morph, project={**pj, "doe": d, "samples": samples}, progress=prog)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"完了: {ok}/{len(rows)} 件 → {Path(args.out).resolve()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
