from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request
from models import Atividade, TipoAtividade, User


def _extrair_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def registrar(
    db: AsyncSession,
    *,
    tipo: TipoAtividade,
    user: Optional[User],
    usuario_nome: Optional[str] = None,
    empresa_id: Optional[int] = None,
    request: Optional[Request] = None,
    ip: Optional[str] = None,
    alvo_tipo: Optional[str] = None,
    alvo_nome: Optional[str] = None,
    detalhe: Optional[str] = None,
    commit: bool = True,
) -> None:
    if user is None and not usuario_nome:
        return
    atv = Atividade(
        tipo=tipo,
        usuario_id=user.id if user else None,
        usuario_nome=usuario_nome or (user.nome if user else "desconhecido"),
        empresa_id=empresa_id if empresa_id is not None else (user.empresa_id if user else None),
        ip=ip or _extrair_ip(request),
        alvo_tipo=alvo_tipo,
        alvo_nome=alvo_nome,
        detalhe=detalhe,
    )
    db.add(atv)
    if commit:
        await db.commit()
