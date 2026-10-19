"""STL / .inp（メッシュ）の面の自動認識・メッシュ変形・書き出しのテスト。"""

import math

import numpy as np
import pytest

from shapelab import demo, doe, meshio_
from shapelab.generate import generate
from shapelab.models import load_model
from shapelab.params import check_params, face_offsets


# ---- STL（デモ形状を三角形にしたもの） ---------------------------------------

@pytest.fixture(scope="module")
def stl_and_cad(tmp_path_factory):
    d = tmp_path_factory.mktemp("m")
    return load_model(demo.write_demo_stl(d)), load_model(demo.write_demo(d))


def test_stl_faces_match_cad(stl_and_cad):
    stl, _ = stl_and_cad
    desc = stl.describe()
    assert (desc["planes"], desc["cylinders"], desc["others"]) == (15, 3, 0)
    radii = sorted(round(2 * f.radius, 3) for f in stl.infos if f.kind_code == 1)
    assert radii == [12, 20, 28]
    assert all(c["error"] is None for c in check_params(demo.demo_params(stl.infos), stl.infos))


@pytest.mark.parametrize("vals", [{"p1": 14}, {"p1": 9}, {"p2": 42}, {"p3": 32}, {"p3": 25}, {"p4": 15}, {"p7": 21},
                                  {"p1": 14, "p2": 42, "p3": 32, "p4": 10, "p5": 34, "p6": 8, "p7": 28}])
def test_stl_morph_matches_cad(stl_and_cad, vals):
    """メッシュ変形の体積変化が CAD（B-rep）での寸法変更とほぼ同じ（差は三角形近似の分）"""
    stl, cadm = stl_and_cad
    ps, pc = demo.demo_params(stl.infos), demo.demo_params(cadm.infos)
    dv_s = stl.morph(face_offsets(ps, vals, stl.infos)).mass["volume"] - stl.mass()["volume"]
    dv_c = cadm.morph(face_offsets(pc, vals, cadm.infos)).mass["volume"] - cadm.mass()["volume"]
    assert dv_s == pytest.approx(dv_c, rel=0.02, abs=20)


def test_stl_too_large_move_fails(stl_and_cad):
    stl, _ = stl_and_cad
    ps = demo.demo_params(stl.infos)
    with pytest.raises(Exception, match="裏返"):
        stl.morph(face_offsets(ps, {"p1": -30}, stl.infos))   # 板厚 12 → -30


# ---- INP（直方体の構造格子メッシュ） ---------------------------------------------

def box_inp(path, etype="C3D8", L=40.0, W=20.0, H=10.0, n=(8, 4, 2)):
    nx, ny, nz = n
    xs, ys, zs = np.linspace(0, L, nx + 1), np.linspace(0, W, ny + 1), np.linspace(0, H, nz + 1)
    idx = lambda i, j, k: (k * (ny + 1) + j) * (nx + 1) + i + 1
    nodes = {idx(i, j, k): (xs[i], ys[j], zs[k]) for k in range(nz + 1) for j in range(ny + 1) for i in range(nx + 1)}
    hexes = [[idx(i, j, k), idx(i + 1, j, k), idx(i + 1, j + 1, k), idx(i, j + 1, k),
              idx(i, j, k + 1), idx(i + 1, j, k + 1), idx(i + 1, j + 1, k + 1), idx(i, j + 1, k + 1)]
             for k in range(nz) for j in range(ny) for i in range(nx)]
    elems = hexes
    if etype == "C3D4":   # 1 つの六面体を 6 つの四面体に
        T6 = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)]
        elems = [[h[a] for a in t] for h in hexes for t in T6]
    if etype == "C3D20":  # 辺の中点に節点を足す
        E = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
        mid, nid, elems = {}, max(nodes) + 1, []
        for h in hexes:
            row = list(h)
            for a, b in E:
                key = tuple(sorted((h[a], h[b])))
                if key not in mid:
                    mid[key] = nid
                    nodes[nid] = tuple((np.array(nodes[h[a]]) + np.array(nodes[h[b]])) / 2)
                    nid += 1
                row.append(mid[key])
            elems.append(row)
    lines = ["*HEADING", "test box", "*NODE, NSET=ALL"]
    lines += [f"{k}, {x:.6f}, {y:.6f}, {z:.6f}" for k, (x, y, z) in nodes.items()]
    lines.append(f"*ELEMENT, TYPE={etype}, ELSET=EALL")
    for e, row in enumerate(elems, 1):
        vals = [str(e)] + [str(v) for v in row]
        if len(vals) > 16:   # Abaqus の続き行
            lines += [", ".join(vals[:16]) + ",", ", ".join(vals[16:])]
        else:
            lines.append(", ".join(vals))
    lines += ["*MATERIAL, NAME=AL", "*DENSITY", "2.7e-9", "*STEP", "*STATIC", "*END STEP"]
    path.write_text("\n".join(lines) + "\n")
    return path


def _plane(model, normal):
    return next(f.id for f in model.infos if f.kind_code == 0 and np.allclose(f.normal, normal, atol=1e-6))


@pytest.mark.parametrize("etype", ["C3D8", "C3D20", "C3D4"])
def test_inp_box_thickness(tmp_path, etype):
    m = load_model(box_inp(tmp_path / f"box_{etype}.inp", etype))
    d = m.describe()
    assert d["planes"] == 6 and d["faces"] == 6
    assert m.mass()["volume"] == pytest.approx(40 * 20 * 10)
    top, bot = _plane(m, [0, 0, 1]), _plane(m, [0, 0, -1])
    side = _plane(m, [1, 0, 0])
    p = [{"id": "t", "key": "t", "kind": "distance", "faces": [{"face": top, "coef": 1}], "ref_face": bot},
         {"id": "l", "key": "l", "kind": "distance", "faces": [{"face": side, "coef": 1}], "ref_face": _plane(m, [-1, 0, 0])}]
    assert [c["base"] for c in check_params(p, m.infos)] == pytest.approx([10, 40])
    r = m.morph(face_offsets(p, {"t": 13, "l": 35}, m.infos))
    assert r.mass["volume"] == pytest.approx(35 * 20 * 13, rel=1e-9)
    assert r.quality["inverted_elements"] == 0
    # 書き出すと節点の行だけ変わり、他の行（材料・ステップ）は残る
    out = tmp_path / "out.inp"
    meshio_.write_inp(m.data.inp, r.handle, out)
    a, b = (tmp_path / f"box_{etype}.inp").read_text().splitlines(), out.read_text().splitlines()
    assert len(a) == len(b) and a[-6:] == b[-6:]
    assert load_model(out).mass()["volume"] == pytest.approx(35 * 20 * 13, rel=1e-9)


def test_inp_inverted_elements_fail(tmp_path):
    m = load_model(box_inp(tmp_path / "b.inp"))
    top, bot = _plane(m, [0, 0, 1]), _plane(m, [0, 0, -1])
    p = [{"id": "t", "key": "t", "kind": "distance", "faces": [{"face": top, "coef": 1}], "ref_face": bot}]
    with pytest.raises(Exception, match="裏返"):
        m.morph(face_offsets(p, {"t": -2}, m.infos))


def test_generate_inp(tmp_path):
    src = box_inp(tmp_path / "plate.inp", "C3D8")
    m = load_model(src)
    top, bot = _plane(m, [0, 0, 1]), _plane(m, [0, 0, -1])
    p = [{"id": "t", "key": "plate_t", "kind": "distance", "faces": [{"face": top, "coef": 1}], "ref_face": bot,
          "min": 8, "max": 12}]
    rows = generate(m, p, doe.sample(p, n=3, seed=1), tmp_path / "out", formats=("stl", "step", "inp"))
    assert [r["status"] for r in rows] == ["ok"] * 3
    d = tmp_path / "out" / "sample_001"
    assert (d / "plate.inp").exists() and (d / "geometry.stl").exists() and not (d / "geometry.step").exists()
    t = rows[0]["plate_t"]
    assert load_model(d / "plate.inp").mass()["volume"] == pytest.approx(40 * 20 * t, rel=1e-9)


def test_unsupported_include(tmp_path):
    p = tmp_path / "x.inp"
    p.write_text("*NODE\n1,0,0,0\n*INCLUDE, INPUT=mesh.inp\n")
    with pytest.raises(Exception, match="INCLUDE"):
        load_model(p)


# ---- フィレット・抜き勾配付きの形（実物に近い確認） -------------------------------

@pytest.fixture(scope="module")
def fillet_stl(tmp_path_factory):
    return load_model(demo.write_fillet_demo_stl(tmp_path_factory.mktemp("f")))


def test_fillet_shape_recognition(fillet_stl):
    """フィレットでなめらかにつながっていても、ボス・穴・抜き勾配ボス・フィレットを別々の面として認識する"""
    labels = [f.to_dict()["label"] for f in fillet_stl.infos]
    kinds = {f.kind_code for f in fillet_stl.infos}
    cyl = sorted(2 * f.radius for f in fillet_stl.infos if f.kind_code == 1 and f.fillet_r is None)
    assert cyl == pytest.approx([12, 20, 28], abs=0.1)             # 穴・ボスB・ボスA（差は三角形近似の分）
    cone = [f for f in fillet_stl.infos if f.kind_code == 2 and f.semi_angle and f.semi_angle < 30]
    assert len(cone) == 1 and cone[0].semi_angle == pytest.approx(math.degrees(math.atan(2 / 16)), abs=0.3)
    assert sum(f.kind_code == 4 for f in fillet_stl.infos) == 3    # ボスの付け根の輪のフィレット
    assert all(f.fillet_r == pytest.approx(1.5, abs=0.05) for f in fillet_stl.infos if f.kind_code == 4)
    assert 4 in kinds and labels


def test_fillet_shape_boss_diameter(fillet_stl):
    """フィレット付きでもボス径を変えられ、体積変化が手計算の範囲に入る
    （円筒部分は付け根のフィレット R1.5 の分だけ短い 23.5mm。リブと重なる分は増えない）"""
    m = fillet_stl
    boss = next(f for f in m.infos if f.kind_code == 1 and f.fillet_r is None and abs(f.radius - 14) < 0.05)
    p = [{"id": "d", "key": "d", "kind": "diameter", "faces": [{"face": boss.id, "coef": 1}]}]
    r = m.morph(face_offsets(p, {"d": 32}, m.infos))
    ring = math.pi * (16 ** 2 - 14 ** 2)
    dv = r.mass["volume"] - m.mass()["volume"]
    assert ring * 23.5 * 0.95 < dv < ring * 25
