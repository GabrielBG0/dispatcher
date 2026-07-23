from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import create_all


def create_app() -> FastAPI:
    app = FastAPI(title="dispatcher")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        create_all()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from app.routers import batches, config as config_router, dashboard, exports, imports, kanji, vocab

    app.include_router(imports.router)
    app.include_router(vocab.router)
    app.include_router(kanji.router)
    app.include_router(batches.router)
    app.include_router(exports.router)
    app.include_router(config_router.router)
    app.include_router(dashboard.router)

    if settings.frontend_dist_dir.exists():
        app.mount("/", StaticFiles(directory=settings.frontend_dist_dir, html=True), name="frontend")

    return app


app = create_app()
