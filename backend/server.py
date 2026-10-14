"""shape-param-lab の単体サーバ。

  uv run uvicorn server:app --host 127.0.0.1 --port 8200

画面（frontend/dist）があれば / で配信する。開発時は Vite（5190）から /api を転送。
他のアプリへ埋め込むときは shapelab.api.make_router だけを使う（README 参照）。
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from shapelab.api import make_router

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("SHAPE_LAB_DATA", ROOT / "data"))
FRONT = ROOT / "frontend" / "dist"

app = FastAPI(title="Shape Param Lab", version="0.1.0")
app.include_router(make_router(DATA), prefix="/api/shape")


@app.get("/health")
def health():
    return {"status": "ok"}


if FRONT.exists():
    app.mount("/", StaticFiles(directory=FRONT, html=True), name="front")
