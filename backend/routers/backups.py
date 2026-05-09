from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database import get_db
from models import Backup, Device, User, UserRole, TipoAtividade
from auth import require_role, get_current_user, is_master, ensure_empresa_access
from schemas import BackupOut, BackupWithDevice
from services.ssh_service import run_backup
from services.scheduler import _limpar_backups_antigos
from services import audit
from typing import List, Optional

router = APIRouter(prefix="/api/backups", tags=["backups"])

@router.get("/", response_model=List[BackupWithDevice])
async def listar_backups(
    empresa_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = (
        select(Backup)
        .join(Device, Backup.device_id == Device.id)
        .options(selectinload(Backup.device))
        .order_by(Backup.criado_em.desc())
        .limit(200)
    )
    if is_master(user):
        if empresa_id is not None:
            q = q.where(Device.empresa_id == empresa_id)
    else:
        if user.empresa_id is None:
            return []
        q = q.where(Device.empresa_id == user.empresa_id)
    result = await db.execute(q)
    return result.scalars().all()

@router.get("/device/{device_id}", response_model=List[BackupOut])
async def backups_por_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    dev = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, dev.empresa_id)
    result = await db.execute(
        select(Backup)
        .where(Backup.device_id == device_id)
        .order_by(Backup.criado_em.desc())
    )
    return result.scalars().all()

@router.post("/run/{device_id}", response_model=BackupOut,
             dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))])
async def executar_backup_manual(
    device_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, device.empresa_id)

    status, conteudo = run_backup(device)
    backup = Backup(
        device_id=device.id,
        status=status,
        conteudo=conteudo if status == "sucesso" else None,
        erro=conteudo if status == "falha" else None,
        origem="manual",
    )
    db.add(backup)
    await db.flush()
    await _limpar_backups_antigos(db, device.id)
    await db.commit()
    await db.refresh(backup)

    tipo_ev = TipoAtividade.device_teste_sucesso if status == "sucesso" else TipoAtividade.device_teste_falha
    await audit.registrar(
        db, tipo=tipo_ev, user=user, request=request,
        empresa_id=device.empresa_id,
        alvo_tipo="device", alvo_nome=device.nome,
        detalhe=(conteudo[:180] if status == "falha" and conteudo else None),
    )
    return backup

@router.delete("/{backup_id}", status_code=204,
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_backup(
    backup_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Backup).options(selectinload(Backup.device)).where(Backup.id == backup_id)
    )
    backup = result.scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    ensure_empresa_access(user, backup.device.empresa_id)

    device_nome = backup.device.nome
    empresa_id = backup.device.empresa_id
    criado_em = backup.criado_em

    await db.delete(backup)
    await audit.registrar(
        db, tipo=TipoAtividade.backup_removido, user=user, request=request,
        empresa_id=empresa_id,
        alvo_tipo="device", alvo_nome=device_nome,
        detalhe=f"backup #{backup_id} de {criado_em.strftime('%d/%m/%Y %H:%M')}",
    )
    await db.commit()
    return None

@router.get("/{backup_id}/download")
async def download_backup(
    backup_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from fastapi.responses import PlainTextResponse
    result = await db.execute(
        select(Backup).options(selectinload(Backup.device)).where(Backup.id == backup_id)
    )
    backup = result.scalar_one_or_none()
    if not backup or not backup.conteudo:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    ensure_empresa_access(user, backup.device.empresa_id)
    return PlainTextResponse(content=backup.conteudo, media_type="text/plain",
                             headers={"Content-Disposition": f"attachment; filename=backup_{backup_id}.txt"})
