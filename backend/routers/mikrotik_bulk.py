"""Endpoints do módulo Operações — execução em massa de ações Mikrotik.

3 endpoints:
  - POST /api/mikrotik-bulk/executar     -> dispara uma ação em N devices
  - GET  /api/mikrotik-bulk/historico    -> lista paginada do OperacaoMassaLog
  - GET  /api/mikrotik-bulk/historico/{id} -> detalhe (inclui resultados por device)

Why endpoint único pra executar (em vez de 8 endpoints):
- O fluxo de orquestração é IDÊNTICO pra toda ação: valida acesso, grava
  OperacaoMassaLog ANTES, dispara workers paralelos, agrega resultado.
- O que varia é `acao` + `params` — perfeito pra parametrizar via body.
- Frontend manda 1 chamada por execução; UX fica simples.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, is_master, require_role
from database import get_db
from models import (
    Device,
    DeviceVendor,
    OperacaoMassaLog,
    Protocolo,
    User,
    UserRole,
)
from services.mikrotik_bulk import ACOES, is_mikrotik

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mikrotik-bulk", tags=["mikrotik-bulk"])

# Limite de paralelismo em operações em massa. Mais conservador que o scheduler
# (que tem AIMD dinâmico) porque aqui é one-shot manual: não há critério adaptativo,
# então um teto fixo previne saturar o servidor. 5 cobre bem a maioria dos casos.
MAX_PARALELISMO = 5


class ExecutarRequest(BaseModel):
    acao: str = Field(..., description="Slug da ação (ver services.mikrotik_bulk.ACOES)")
    device_ids: list[int] = Field(..., min_length=1, description="IDs dos devices alvo")
    params: dict = Field(default_factory=dict, description="Parâmetros específicos da ação")


class ResultadoDevice(BaseModel):
    device_id: int
    device_nome: Optional[str] = None
    status: str  # 'sucesso' | 'falha'
    output: str
    duracao_ms: int


class ExecutarResponse(BaseModel):
    operacao_id: int
    total: int
    sucessos: int
    falhas: int
    duracao_ms: int
    resultados: list[ResultadoDevice]


class HistoricoItem(BaseModel):
    id: int
    acao: str
    usuario_nome: str
    total: int
    sucessos: int
    falhas: int
    iniciado_em: datetime
    concluido_em: Optional[datetime]


class HistoricoDetalhe(BaseModel):
    id: int
    acao: str
    usuario_nome: str
    device_ids: list[int]
    params: dict
    total: int
    sucessos: int
    falhas: int
    iniciado_em: datetime
    concluido_em: Optional[datetime]
    resultados: dict  # device_id (str) → {status, output, duracao_ms}


async def _executar_acao_no_device(
    func, device: Device, params: dict
) -> tuple[str, str, int]:
    """Roda a função síncrona em thread separada e mede duração.
    Retorna (status, output, duracao_ms)."""
    inicio = time.monotonic()
    try:
        status, output = await asyncio.to_thread(func, device, params)
    except Exception as e:
        # Última linha de defesa — função não deveria lançar (sempre devolve tupla),
        # mas se algo passar do try/except interno, vira falha registrada.
        log.exception("Exceção não tratada em ação massa device=%s", device.id)
        status, output = "falha", f"Exceção não tratada: {e}"
    duracao_ms = int((time.monotonic() - inicio) * 1000)
    return status, output, duracao_ms


@router.post(
    "/executar",
    response_model=ExecutarResponse,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def executar(
    data: ExecutarRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 1) Validações iniciais
    if data.acao not in ACOES:
        raise HTTPException(status_code=400, detail=f"Ação desconhecida: '{data.acao}'")
    func, exige_master, sanitizar = ACOES[data.acao]
    if exige_master and not is_master(user):
        raise HTTPException(
            status_code=403,
            detail="Esta ação exige permissão de admin master.",
        )

    # 2) Carrega devices alvo (filtrando por empresa se não for master)
    q = select(Device).where(Device.id.in_(data.device_ids))
    if not is_master(user):
        if user.empresa_id is None:
            raise HTTPException(status_code=403, detail="Usuário sem empresa vinculada")
        q = q.where(Device.empresa_id == user.empresa_id)
    devices = (await db.execute(q)).scalars().all()
    if not devices:
        raise HTTPException(
            status_code=404,
            detail="Nenhum device acessível encontrado entre os IDs informados.",
        )

    # 3) Filtra só Mikrotik (módulo é específico) e protocolo API
    # (devices Mikrotik com protocolo SSH não funcionariam — librouteros exige API).
    invalidos = []
    validos = []
    for d in devices:
        if not is_mikrotik(d):
            invalidos.append((d, f"fabricante={d.fabricante.value if d.fabricante else 'none'} (não é Mikrotik)"))
            continue
        if d.protocolo != Protocolo.api:
            invalidos.append((d, f"protocolo={d.protocolo.value if d.protocolo else 'none'} (precisa ser 'api')"))
            continue
        validos.append(d)

    if not validos:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nenhum device elegível. Operações em massa só funcionam com "
                "fabricante Mikrotik (v6/v7) E protocolo='api'. "
                f"Motivos: {'; '.join(f'#{d.id}: {m}' for d, m in invalidos[:10])}"
            ),
        )

    # 4) Grava OperacaoMassaLog ANTES de disparar — rastreabilidade garantida
    # mesmo se o backend crashar no meio.
    params_log = sanitizar(data.params) if sanitizar else data.params
    op = OperacaoMassaLog(
        acao=data.acao,
        usuario_id=user.id,
        usuario_nome=user.nome,
        empresa_id=user.empresa_id if not is_master(user) else None,
        device_ids=[d.id for d in validos],
        params=params_log,
        total=len(validos),
        sucessos=0,
        falhas=0,
    )
    db.add(op)
    await db.commit()
    await db.refresh(op)
    op_id = op.id

    # 5) Dispara em paralelo com semáforo (cap MAX_PARALELISMO).
    inicio = time.monotonic()
    semaforo = asyncio.Semaphore(MAX_PARALELISMO)

    async def _wrapped(device: Device):
        async with semaforo:
            return device, *await _executar_acao_no_device(func, device, data.params)

    resultados_brutos = await asyncio.gather(*[_wrapped(d) for d in validos])

    # 6) Agrega
    resultados_dict: dict = {}
    sucessos = 0
    falhas = 0
    resultados_list: list[ResultadoDevice] = []
    for device, status, output, duracao_ms in resultados_brutos:
        resultados_dict[str(device.id)] = {
            "status": status,
            "output": output,
            "duracao_ms": duracao_ms,
        }
        resultados_list.append(ResultadoDevice(
            device_id=device.id,
            device_nome=device.nome,
            status=status,
            output=output,
            duracao_ms=duracao_ms,
        ))
        if status == "sucesso":
            sucessos += 1
        else:
            falhas += 1

    # Inclui inelegíveis no resultado pra UI poder mostrar que foram pulados.
    for d, motivo in invalidos:
        resultados_dict[str(d.id)] = {
            "status": "falha",
            "output": f"Device pulado: {motivo}",
            "duracao_ms": 0,
        }
        resultados_list.append(ResultadoDevice(
            device_id=d.id,
            device_nome=d.nome,
            status="falha",
            output=f"Device pulado: {motivo}",
            duracao_ms=0,
        ))
        falhas += 1

    duracao_total_ms = int((time.monotonic() - inicio) * 1000)

    # 7) Atualiza o OperacaoMassaLog com resultados
    op.resultados = resultados_dict
    op.sucessos = sucessos
    op.falhas = falhas
    op.total = sucessos + falhas
    op.concluido_em = datetime.utcnow()
    await db.commit()

    return ExecutarResponse(
        operacao_id=op_id,
        total=sucessos + falhas,
        sucessos=sucessos,
        falhas=falhas,
        duracao_ms=duracao_total_ms,
        resultados=resultados_list,
    )


@router.get("/historico", response_model=list[HistoricoItem])
async def listar_historico(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(OperacaoMassaLog).order_by(OperacaoMassaLog.iniciado_em.desc())
    if not is_master(user):
        # Não-master vê só execuções da própria empresa.
        if user.empresa_id is None:
            return []
        q = q.where(OperacaoMassaLog.empresa_id == user.empresa_id)
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [
        HistoricoItem(
            id=r.id,
            acao=r.acao,
            usuario_nome=r.usuario_nome,
            total=r.total,
            sucessos=r.sucessos,
            falhas=r.falhas,
            iniciado_em=r.iniciado_em,
            concluido_em=r.concluido_em,
        )
        for r in rows
    ]


@router.get("/historico/{op_id}", response_model=HistoricoDetalhe)
async def detalhe_historico(
    op_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = (await db.execute(select(OperacaoMassaLog).where(OperacaoMassaLog.id == op_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Operação não encontrada")
    if not is_master(user) and r.empresa_id != user.empresa_id:
        raise HTTPException(status_code=403, detail="Sem acesso a esta operação")
    return HistoricoDetalhe(
        id=r.id,
        acao=r.acao,
        usuario_nome=r.usuario_nome,
        device_ids=r.device_ids or [],
        params=r.params or {},
        total=r.total,
        sucessos=r.sucessos,
        falhas=r.falhas,
        iniciado_em=r.iniciado_em,
        concluido_em=r.concluido_em,
        resultados=r.resultados or {},
    )


@router.get("/acoes")
async def listar_acoes(user: User = Depends(get_current_user)):
    """Lista ações disponíveis pro user (filtra master-only se não for master).
    Frontend usa pra montar o dropdown sem hardcode."""
    master = is_master(user)
    return [
        {"slug": slug, "exige_master": exige_master}
        for slug, (_func, exige_master, _san) in ACOES.items()
        if master or not exige_master
    ]
