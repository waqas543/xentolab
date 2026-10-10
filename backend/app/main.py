import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.routers import companies, query_runner, stats
from data_platform.api import routes_app, routes_dashboard, routes_data, routes_pipelines


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.pool.open(wait=True)
    yield
    db.pool.close()
    from data_platform import db as platform_db

    platform_db.close_pools()


app = FastAPI(title="OSM Companies API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(stats.router)
app.include_router(query_runner.router)
app.include_router(routes_pipelines.router)
app.include_router(routes_data.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_app.router)


@app.get("/api/health")
def health():
    row = db.fetch_one("SELECT count(*) AS companies FROM osm_app.companies")
    return {"ok": True, **row}
