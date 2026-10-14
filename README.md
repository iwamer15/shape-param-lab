# Shape Param Lab（形状パラメータ・ラボ）

サロゲートモデルの学習データ用に、**CAD（STEP / IGES）の寸法をパラメータ化**し、
スライダーで形を変えて 3D で確認しながら、**実験計画（LHS など）に沿って形状を一括生成**するツール。

- 面をクリックして「板厚」「ボス高さ」「穴径」「リブ幅」などの寸法を定義
- スライダーを動かすと、サーバ（OpenCascade）で形状を作り直して 0.2〜0.5 秒ほどで表示
- 範囲チェック（各寸法を最小・最大にしても形が作れるか）
- 一括生成: `sample_001/geometry.stl + geometry.step + conditions.csv` の形式で書き出し
  （ml-process-lab / surrogate-manager のデータフォルダ形式に合わせている）

---

## 起動

| OS | 方法 |
|---|---|
| Windows | `start.bat` をダブルクリック（初回はライブラリ導入と画面ビルドを自動実行）→ http://127.0.0.1:8200 |
| Mac / Linux | `./start.sh` |

前提: [uv](https://docs.astral.sh/uv/)（Python 3.11 は uv が自動取得）、Node.js 22.12 以上（初回ビルド時のみ）

### 開発時
```bash
cd backend && uv run uvicorn server:app --host 127.0.0.1 --port 8200 --reload
cd frontend && npm run dev     # http://localhost:5190（/api は 8200 へ転送）
cd backend && uv run pytest -q # テスト
```

---

## 使い方

1. 「CAD を開く」で STEP / IGES を読み込む（「デモ形状」で板＋ボス＋穴＋リブ＋窓の例と寸法 7 個が入る）
2. 3D の面をクリック →「選択中の面」から寸法を作る
   - 平面 → **寸法（距離）**: 平行な基準面との距離（板厚・高さ・幅）。基準面は反対向きの一番近い平面が自動で入る
   - 円筒面 → **直径**: 穴径・ボス外径
   - どの面でも → **面の移動量**: 法線方向（外側が +）に動かす量
   - 1 つの寸法で複数の面を動かせる（係数付き。例: リブ幅は両側の面を 0.5 ずつ）
3. スライダーで値を変えて形を確認（赤線＝元の形）。各寸法の範囲（最小・最大・刻み）を決める
4. 右の「実験計画」で方法と数を選んで「計画を作る」→ 散布図・表の点をクリックするとその形を表示
5. 「範囲チェック」で端の値でも作れるか確認 →「生成」
6. 定義は「保存」で `data/projects/<名前>.json` に残る（CLI からも使える）

### 実験計画の方法
| 方法 | 内容 |
|---|---|
| ラテン超方格（LHS） | 各寸法の範囲を形状数で等分し、どの区間も 1 回ずつ使う（標準。簡易 maximin で偏りも抑える） |
| ランダム | 一様乱数 |
| 格子 | 各寸法を水準数で等分した全組み合わせ |
| 1因子ずつ | 他を元の値に固定し 1 つずつ振る（感度確認） |

### 出力
```
data/outputs/<出力名>/
  doe_table.csv        … 全サンプルの寸法・体積・表面積・方式・成否（Excel で開ける BOM 付き UTF-8）
  project.json         … 生成に使った定義とサンプル値（再現用）
  sample_001/
    geometry.stl       … 学習・ボクセル化用
    geometry.step      … CAE にそのまま渡す用
    conditions.csv     … name,value 形式の寸法（学習の入力条件）
    shape.json         … 寸法・各面の移動量・体積など
```
1 件失敗しても止まらず、`doe_table.csv` に `failed` と理由が残る。

### CLI
```bash
cd backend
uv run python -m shapelab demo                                   # デモ形状と data/projects/demo.json
uv run python -m shapelab info ../data/models/part.step          # 面の一覧
uv run python -m shapelab generate ../data/projects/demo.json -o ../data/outputs/run1 -n 50 --method lhs --seed 1
```

---

## 形状の変え方（しくみ）

STEP には CAD の履歴（スケッチ寸法・フィーチャー）が残らないため、**ダイレクトモデリング**（面を動かす）で寸法を変える。

| 方式 | 内容 | 使われる場面 |
|---|---|---|
| 精密（面オフセット） | OpenCascade の `BRepOffset_MakeOffset` で選んだ面を法線方向に動かす。隣の面は自然に伸び縮みし、抜き勾配も保たれる | 単純な形 |
| 近似（押し出し/削り） | 平面はその面の押し出しを足す/削る、円筒面は筒を足す/削る（ブーリアン）。ボスを細くしたときは付いていたリブを新しい外径まで延ばす | 精密が失敗する形（**リブがボスに付く形**など、鋳物では多い） |

既定の「自動」は精密を試して、だめなら近似で作る。使った方式は画面と `doe_table.csv` に出る。
両方式が使える形では体積が一致することをテストで確認している（`backend/tests`）。

### 制限
- 入力は STEP / IGES / BREP のソリッド。**STL は不可**（面・穴径の情報が無いため）
- 動かせる面: 精密は全種類、近似は平面と円筒面のみ
- 近似方式では、動かした面の隣の面は「動かす面の法線方向にまっすぐ」延びる（抜き勾配・フィレットは追従しない）
- 穴やボスの**位置の移動**、フィレット半径の変更は未対応
- 面を大きく動かして隣の面が消える・交差する場合は作れない（範囲チェックで事前に確認）
- 面番号は読み込んだ CAD に対して振られる。CAD を差し替えると定義の面番号がずれるので作り直す

---

## 他のアプリへの埋め込み（ml-process-lab など）

バックエンドは `APIRouter` を返す関数、フロントは 1 つの React コンポーネントになっている。

**バックエンド**（`backend/shapelab/` をコピーし、依存に `cadquery-ocp` `python-multipart` を追加）
```python
from pathlib import Path
from shapelab.api import make_router
app.include_router(make_router(Path("data/shape")), prefix="/api/shape")
```

**フロントエンド**（`frontend/src/shapelab/` をコピー。依存は react / three のみ）
```tsx
import { ShapeLab } from './shapelab'
<div style={{ height: '100vh' }}><ShapeLab apiBase="/api/shape" /></div>
```
CSS はすべて `.sl-root` 内・`sl-` 接頭辞なので、埋め込み先のスタイルと衝突しない。

## 構成
```
backend/
  server.py              単体サーバ（/api/shape と画面の配信）
  shapelab/cad.py        CAD 読み書き・面情報・面移動（精密/近似）・メッシュ化
  shapelab/params.py     寸法パラメータ → 面ごとの移動量
  shapelab/doe.py        実験計画（LHS / ランダム / 格子 / 1因子ずつ）
  shapelab/generate.py   一括生成
  shapelab/api.py        API（make_router）
  shapelab/demo.py       デモ形状
  tests/                 体積の手計算との照合など
frontend/src/shapelab/   画面（React + three.js）
data/                    models / projects / outputs
```

---

## ライセンス
MIT（[LICENSE](LICENSE)）。CAD カーネルに [OpenCascade](https://dev.opencascade.org/)（LGPL-2.1、[cadquery-ocp](https://github.com/CadQuery/OCP) 経由）を使用。
