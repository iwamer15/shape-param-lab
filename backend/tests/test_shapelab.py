"""寸法変更の正しさ（体積が手計算と合うか）と、一括生成の出力形式のテスト。

  uv run pytest -q
"""

import csv
import math

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

from shapelab import cad, demo, doe
from shapelab.generate import generate
from shapelab.models import load_model
from shapelab.params import check_params, face_offsets

B, C = demo._box, demo._cyl


@pytest.fixture(scope="module")
def simple():
    """リブ無し（精密方式も通る）: 板 160×100×12 + ボスφ28 高さ25 + 穴φ12 + 角窓"""
    s = B(0, 0, 0, 160, 100, 12)
    s = BRepAlgoAPI_Fuse(s, C(40, 50, 12, 14, 25)).Shape()
    s = BRepAlgoAPI_Cut(s, C(40, 50, -1, 6, 40)).Shape()
    s = BRepAlgoAPI_Cut(s, B(65, 12, -1, 30, 14, 20)).Shape()
    s = cad._finish(cad._unify(s), "test")
    infos = cad.all_face_info(s)
    f = lambda pred: next(x.id for x in infos if pred(x))
    top = f(lambda x: x.kind_code == 0 and x.normal[2] > 0.99 and abs(x.center[2] - 12) < 1e-3)
    bot = f(lambda x: x.kind_code == 0 and x.normal[2] < -0.99)
    boss_top = f(lambda x: x.kind_code == 0 and abs(x.center[2] - 37) < 1e-3)
    boss = f(lambda x: x.kind_code == 1 and x.convex)
    hole = f(lambda x: x.kind_code == 1 and not x.convex)
    params = [
        {"id": "t", "key": "t", "kind": "distance", "faces": [{"face": top, "coef": 1}], "ref_face": bot},
        {"id": "h", "key": "h", "kind": "distance", "faces": [{"face": boss_top, "coef": 1}], "ref_face": bot},
        {"id": "d", "key": "d", "kind": "diameter", "faces": [{"face": boss, "coef": 1}]},
        {"id": "o", "key": "o", "kind": "diameter", "faces": [{"face": hole, "coef": 1}]},
    ]
    return s, infos, params


def vol(s):
    return cad.mass_props(s)["volume"]


def test_base_values(simple):
    _, infos, params = simple
    got = {c["id"]: c["base"] for c in check_params(params, infos)}
    assert got == pytest.approx({"t": 12, "h": 37, "d": 28, "o": 12})


TOP_AREA = 160 * 100 - 30 * 14 - math.pi * 14 ** 2  # 板上面（ボスの下と窓を除く）


@pytest.mark.parametrize("method", ["offset", "boolean"])
@pytest.mark.parametrize("vals,dv", [
    ({"t": 14}, 2 * TOP_AREA),
    ({"t": 10}, -2 * TOP_AREA),
    ({"h": 42}, math.pi * (14 ** 2 - 6 ** 2) * 5),
    ({"d": 32}, math.pi * (16 ** 2 - 14 ** 2) * 25),
    ({"o": 16}, -math.pi * (8 ** 2 - 6 ** 2) * 37),
    ({"o": 8}, math.pi * (6 ** 2 - 4 ** 2) * 37),
])
def test_volume_matches_hand_calc(simple, method, vals, dv):
    s, infos, params = simple
    r = cad.apply_offsets(s, face_offsets(params, vals, infos), method=method)
    assert r.method == method
    assert vol(r.shape) - vol(s) == pytest.approx(dv, rel=1e-6)
    assert r.origin.count(-1) == 0


def test_methods_agree_on_combination(simple):
    s, infos, params = simple
    offs = face_offsets(params, {"t": 14, "h": 42, "d": 32, "o": 8}, infos)
    a = vol(cad.apply_offsets(s, offs, method="offset").shape)
    b = vol(cad.apply_offsets(s, offs, method="boolean").shape)
    assert a == pytest.approx(b, rel=1e-9)


@pytest.fixture(scope="module")
def demo_shape():
    s = demo.make_demo_shape()
    return s, cad.all_face_info(s), demo.demo_params(s)


def test_demo_rib_falls_back_to_boolean(demo_shape):
    """リブがボスに付く形は面オフセットが失敗する → 自動で近似方式になる"""
    s, infos, params = demo_shape
    with pytest.raises(cad.CadError):
        cad.apply_offsets(s, face_offsets(params, {"p2": 42}, infos), method="offset")
    r = cad.apply_offsets(s, face_offsets(params, {"p2": 42}, infos))
    assert r.method == "boolean"
    assert vol(r.shape) - vol(s) == pytest.approx(math.pi * (14 ** 2 - 6 ** 2) * 5, rel=1e-6)


def test_boss_shrink_keeps_rib_attached(demo_shape):
    """ボスを細くしてもリブが新しい外径まで延びる（すき間ができない）"""
    s, infos, params = demo_shape
    r = cad.apply_offsets(s, face_offsets(params, {"p3": 25}, infos))
    ring = math.pi * (14 ** 2 - 12.5 ** 2) * 25
    rib_fill = 6 * 14 * (math.sqrt(14 ** 2 - 9) - math.sqrt(12.5 ** 2 - 9))  # 直線近似
    assert vol(r.shape) - vol(s) == pytest.approx(-ring + rib_fill, rel=2e-3)


def test_lhs_covers_each_bin():
    ps = [{"id": "a", "min": 0, "max": 10}, {"id": "b", "min": -1, "max": 1}]
    rows = doe.sample(ps, n=10, method="lhs", seed=3)
    for p in ps:
        bins = sorted(int((r[p["id"]] - p["min"]) / (p["max"] - p["min"]) * 10) for r in rows)
        assert bins == list(range(10))


def test_step_rounding_and_grid():
    ps = [{"id": "a", "min": 8, "max": 16, "step": 0.5}]
    assert all((r["a"] * 2) % 1 == 0 for r in doe.sample(ps, n=15, seed=1))
    assert [r["a"] for r in doe.sample(ps, method="grid", levels=3)] == [8, 12, 16]


def test_generate_writes_sample_folders(tmp_path):
    step = demo.write_demo(tmp_path / "models")
    model = load_model(step)
    params = demo.demo_params(model.infos)
    samples = doe.sample(params, n=3, seed=0)
    rows = generate(model, params, samples, tmp_path / "out", formats=("stl", "step"))
    tmp_path = tmp_path / "out"
    assert [r["status"] for r in rows] == ["ok"] * 3
    d = tmp_path / "sample_001"
    assert (d / "geometry.stl").stat().st_size > 84 and (d / "geometry.step").exists()
    with open(d / "conditions.csv", encoding="utf-8") as f:
        cond = {r["name"]: float(r["value"]) for r in csv.DictReader(f)}
    assert set(cond) == {p["key"] for p in params}
    # STEP を読み直しても同じ形
    assert vol(cad.load_shape(d / "geometry.step")) == pytest.approx(rows[0]["volume"], rel=1e-6)
    with open(tmp_path / "doe_table.csv", encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == 3


def test_cad_loader_rejects_stl(tmp_path):
    p = tmp_path / "x.stl"
    p.write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(cad.CadError, match="STEP"):
        cad.load_shape(p)


def test_constraints_filter_samples():
    ps = [{"id": "a", "key": "rib_h", "min": 20, "max": 30, "step": 0.5},
          {"id": "b", "key": "boss_h", "min": 22, "max": 36, "step": 0.5}]
    for method in ("lhs", "random", "grid"):
        rows = doe.sample(ps, n=15, method=method, seed=2, levels=5, constraints=["rib_h <= boss_h - 3"])
        assert rows and all(r["a"] <= r["b"] - 3 for r in rows)
    assert len(doe.sample(ps, n=15, seed=2, constraints=["rib_h <= boss_h - 3"])) == 15
    with pytest.raises(ValueError, match="列名"):
        doe.sample(ps, n=5, constraints=["rib_w <= 3"])
    with pytest.raises(ValueError, match="使えない"):
        doe.sample(ps, n=5, constraints=["__import__('os') <= 3"])
    with pytest.raises(ValueError, match="満たす組み合わせがありません"):
        doe.sample(ps, n=5, constraints=["rib_h >= boss_h + 20"])
