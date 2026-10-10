"""FastAPI 入口。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.debug import router as debug_router
from app.api.enroll import router as enroll_router
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
    # 崩溃/重启残留 sweep：上次进程死掉时 status='live' 的会议已无人写入，
    # 标记为 interrupted（已中断），否则历史列表永远显示"进行中"
    from sqlalchemy import update

    from app.db.database import SessionLocal
    from app.db.models import Meeting

    async with SessionLocal() as db:
        r = await db.execute(
            update(Meeting).where(Meeting.status == "live").values(status="interrupted")
        )
        await db.commit()
        if r.rowcount:
            logging.getLogger(__name__).info("swept %d stale live meetings -> interrupted", r.rowcount)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(ws_router)
app.include_router(meetings_router)
app.include_router(speakers_router)
app.include_router(enroll_router)
app.include_router(debug_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.server_host, port=settings.server_port)
