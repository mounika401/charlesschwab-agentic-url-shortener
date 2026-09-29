"""Run the service: ``python -m shortener`` (honours SHORTENER_* env vars)."""

import os

import uvicorn

from .app import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("SHORTENER_HOST", "127.0.0.1"),
        port=int(os.environ.get("SHORTENER_PORT", "8000")),
    )
