"""Mock Server — FastAPI application factory and CLI entry point."""

from __future__ import annotations

import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .data import load_seed, reset
from .routers import auth, business, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_path = Path(settings.data_dir) / settings.seed_file
    load_seed(seed_path)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mock Business Server",
        description="Simulated business API for AriesAgent development & testing",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(business.router)

    @app.post("/_reset", status_code=200, tags=["system"])
    def reset_data() -> dict[str, str]:
        """Reset all data to seed defaults."""
        reset()
        return {"status": "reset"}

    return app


app = create_app()


def cli_entry(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Mock Business Server")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--data-dir", default=settings.data_dir)
    args = parser.parse_args(argv)

    settings.host = args.host
    settings.port = args.port
    settings.data_dir = args.data_dir

    import uvicorn

    uvicorn.run("mock_server.main:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    cli_entry(sys.argv[1:])
