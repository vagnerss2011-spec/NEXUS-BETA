from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload, defer
from database import get_db
from models import Backup, Device, User, UserRole, TipoAtividade
from auth import require_role, get_current_user, is_master, ensure_empresa_access
from schemas import BackupOut, BackupWithDevice, BackupListWithDevice
from services.ssh_service import run_backup
from services.scheduler import _limpar_backups_antigos
from services import audit
from typing import List, Optional

router = APIRouter(prefix="/api/backups", tags=["backups"])

@router.get("/", response_model=List[BackupListWithDevice])
async def listar_backups(
    empresa_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # IMPORTANTE: NÃO traz `conteudo` (pode ter MBs por backup). `defer` tira
    # a coluna do SELECT e `func.length` devolve só o tamanho — o frontend usa
    # isso pra exibir KB/MB e detectar truncamento. Conteúdo é buscado sob
    # demanda no preview (GET /{id}/download). Antes a listagem trazia tudo
    # inline e gerava payloads de dezenas de MB que travavam o frontend.
    q = (
        select(Backup, func.length(Backup.conteudo).label("tamanho"))
        .join(Device, Backup.device_id == Device.id)
        .options(selectinload(Backup.device), defer(Backup.conteudo))
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
    rows = (await db.execute(q)).all()
    out = []
    for backup, tamanho in rows:
        item = BackupListWithDevice.model_validate(backup)
        item.tamanho_bytes = tamanho
        out.append(item)
    return out

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
    import time as _time
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, device.empresa_id)

    # Mede duração também em coletas manuais — alimenta a média histórica
    # usada pelo scheduler pra detectar picos. Sem isso, devices só rodados
    # manualmente nunca teriam baseline e ficariam fora da detecção.
    t_inicio = _time.monotonic()
    status, conteudo = run_backup(device)
    duracao_seg = int(round(_time.monotonic() - t_inicio))
    backup = Backup(
        device_id=device.id,
        status=status,
        conteudo=conteudo if status == "sucesso" else None,
        erro=conteudo if status == "falha" else None,
        origem="manual",
        duracao_segundos=duracao_seg,
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

@router.delete("/", status_code=204,
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_backups_em_massa(
    request: Request,
    status: str = Query(..., pattern="^(falha|sucesso)$"),
    empresa_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Apaga em massa todos os backups que casam com o filtro.

    Por enquanto só aceita filtro por `status` (falha|sucesso) — caso de uso
    principal é "limpar tudo que falhou" pra reduzir ruído na listagem.

    Escopo:
    - admin (master): pode apagar de qualquer empresa; passa empresa_id pra
      restringir, ou omite pra apagar global.
    - admin_empresa: força automaticamente pra empresa do user (ignora
      empresa_id no payload, mesmo padrão do DELETE de /atividades).
    """
    # Define o escopo de empresa que será apagado.
    if is_master(user):
        escopo_empresa = empresa_id  # None = global
    else:
        if user.empresa_id is None:
            raise HTTPException(status_code=403, detail="Usuário sem empresa associada")
        escopo_empresa = user.empresa_id

    # Conta antes pra registrar na auditoria e devolver 200 limpo se 0.
    # Usamos JOIN com devices pra aplicar filtro de empresa — backup não tem
    # empresa_id direto (vem via device).
    count_q = select(Backup.id).join(Device, Backup.device_id == Device.id).where(Backup.status == status)
    if escopo_empresa is not None:
        count_q = count_q.where(Device.empresa_id == escopo_empresa)
    ids = [row[0] for row in (await db.execute(count_q)).fetchall()]
    total = len(ids)

    if total == 0:
        # Idempotente — nada a apagar é resposta válida, não 404.
        return None

    # DELETE em lote por id (mais simples que tentar usar subquery com JOIN
    # no DELETE — PostgreSQL aceita mas SQLAlchemy 2.x async fica chato com
    # synchronize_session). 1 round-trip a mais mas é correto e seguro.
    await db.execute(delete(Backup).where(Backup.id.in_(ids)))

    # Audit: 1 registro consolidado com a contagem — não polui auditoria com
    # N entradas, mas o admin consegue ver "X backups com status=falha foram
    # removidos por fulano em tal momento".
    await audit.registrar(
        db, tipo=TipoAtividade.backup_removido, user=user, request=request,
        empresa_id=escopo_empresa,
        alvo_tipo="backup_bulk", alvo_nome=f"status={status}",
        detalhe=f"{total} backup(s) removidos em massa",
    )
    await db.commit()
    return None


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
    """Download do backup. Comportamento:

    - Conteúdo binário (push de .zip/.gz/.tar etc, prefixo 'BASE64:'): decodifica
      e retorna como octet-stream. Filename = nome_arquivo original (preserva
      extensão pro browser/SO reconhecerem corretamente — UNM2000 manda .zip
      e não pode chegar como .txt).
    - Conteúdo texto (configs CLI normais): retorna como texto. Filename usa
      nome_arquivo se disponível, senão fallback histórico backup_<id>.txt.
    """
    import base64
    from fastapi.responses import PlainTextResponse, Response
    result = await db.execute(
        select(Backup).options(selectinload(Backup.device)).where(Backup.id == backup_id)
    )
    backup = result.scalar_one_or_none()
    if not backup or not backup.conteudo:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    ensure_empresa_access(user, backup.device.empresa_id)

    nome = backup.nome_arquivo or f"backup_{backup_id}.txt"
    # Aspas duplas no filename pra cobrir nomes com espaços/caracteres especiais.
    disp = f'attachment; filename="{nome}"'

    if backup.conteudo.startswith("BASE64:"):
        try:
            raw = base64.b64decode(backup.conteudo[7:])
        except Exception:
            raise HTTPException(status_code=500, detail="Conteúdo binário corrompido")
        return Response(content=raw, media_type="application/octet-stream",
                        headers={"Content-Disposition": disp})

    return PlainTextResponse(content=backup.conteudo, media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": disp})
