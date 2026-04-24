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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
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
