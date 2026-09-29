"""FastAPI 入口。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.debug import router as debug_router
from app.api.meetings import router as meetings_router
from app.api.speakers import router as speakers_router
from app.api.ws import router as ws_router
from app.config import settings
from app.db.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="meeting-asr", version="0.1.0")

# 一期 dev：Vue dev server (5173) 直连后端，允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await init_db()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(ws_router)
app.include_router(meetings_router)
app.include_router(speakers_router)
app.include_router(debug_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.server_host, port=settings.server_port)
