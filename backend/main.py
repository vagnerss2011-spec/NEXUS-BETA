from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from database import engine, Base
from routers import auth, users, devices, backups
from services.scheduler import iniciar_scheduler, scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    iniciar_scheduler()
    yield
    scheduler.shutdown()

app = FastAPI(title="NEXUS BETA - Backup Manager", version="1.0.0", lifespan=lifespan)

RFC1918_REGEX = (
    r"http://(localhost|127\.0\.0\.1"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})"
    r"(:\d+)?"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=RFC1918_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(devices.router)
app.include_router(backups.router)

@app.get("/")
async def root():
    return {"status": "NEXUS BETA online"}
