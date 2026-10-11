"""Production entry point: only the mobile app API.

    uvicorn xento_api.main:app --host 0.0.0.0 --port 8000

Never deploy `app.main` publicly: it also serves the data-platform endpoints and the SQL query runner.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from . import db, routes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.pool()
    yield
    db.close()


docs = os.environ.get("API_DOCS", "1") == "1"
app = FastAPI(title="XentoLab API", lifespan=lifespan, docs_url="/docs" if docs else None, redoc_url=None,
              openapi_url="/openapi.json" if docs else None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.include_router(routes.router)


@app.get("/api/health")
def health():
    row = db.one("SELECT version FROM app.dataset_info")
    return {"ok": True, "dataset": row["version"]}
