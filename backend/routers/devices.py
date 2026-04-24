from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Device, UserRole
from auth import require_role, get_current_user
from schemas import DeviceCreate, DeviceUpdate, DeviceOut
from services.crypto import encrypt
from typing import List

router = APIRouter(prefix="/api/devices", tags=["devices"])

@router.get("/", response_model=List[DeviceOut])
async def listar_devices(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Device).order_by(Device.nome))
    return result.scalars().all()

@router.post("/", response_model=DeviceOut, dependencies=[Depends(require_role(UserRole.admin, UserRole.operador))])
async def criar_device(data: DeviceCreate, db: AsyncSession = Depends(get_db)):
    device = Device(
        nome=data.nome, ip=data.ip, porta=data.porta,
        fabricante=data.fabricante, usuario_ssh=data.usuario_ssh,
        senha_ssh_enc=encrypt(data.senha_ssh),
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device

@router.put("/{device_id}", response_model=DeviceOut, dependencies=[Depends(require_role(UserRole.admin, UserRole.operador))])
async def atualizar_device(device_id: int, data: DeviceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    update = data.model_dump(exclude_none=True)
    if "senha_ssh" in update:
        device.senha_ssh_enc = encrypt(update.pop("senha_ssh"))
    for field, value in update.items():
        setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    return device

@router.delete("/{device_id}", dependencies=[Depends(require_role(UserRole.admin))])
async def deletar_device(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    await db.delete(device)
    await db.commit()
    return {"ok": True}
