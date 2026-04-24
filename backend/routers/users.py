from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import User, UserRole, Empresa, TipoAtividade
from auth import hash_senha, require_role, get_current_user, is_master
from schemas import UserCreate, UserUpdate, UserOut
from services import audit
from typing import List, Optional

router = APIRouter(prefix="/api/users", tags=["users"])

async def _empresa_exists(db: AsyncSession, empresa_id: int) -> bool:
    r = await db.execute(select(Empresa.id).where(Empresa.id == empresa_id))
    return r.scalar_one_or_none() is not None

def _can_manage_users(user: User) -> bool:
    return user.role in (UserRole.admin, UserRole.admin_empresa)

@router.get("/", response_model=List[UserOut])
async def listar_usuarios(
    empresa_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage_users(user):
        raise HTTPException(status_code=403, detail="Permissão insuficiente")
    q = select(User).order_by(User.nome)
    if is_master(user):
        if empresa_id is not None:
            q = q.where(User.empresa_id == empresa_id)
    else:
        # admin_empresa: só vê usuários da própria empresa
        q = q.where(User.empresa_id == user.empresa_id)
    result = await db.execute(q)
    return result.scalars().all()

@router.post("/", response_model=UserOut)
async def criar_usuario(
    data: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage_users(user):
        raise HTTPException(status_code=403, detail="Permissão insuficiente")

    # Regras de role/empresa
    if is_master(user):
        # Master pode criar qualquer coisa. Se criar não-master, empresa_id é obrigatório.
        if data.role != UserRole.admin and data.empresa_id is None:
            raise HTTPException(status_code=400, detail="empresa_id é obrigatório para esta role")
        empresa_id = None if data.role == UserRole.admin else data.empresa_id
    else:
        # admin_empresa: só pode criar dentro da própria empresa e não pode promover a admin master
        if data.role == UserRole.admin:
            raise HTTPException(status_code=403, detail="Não pode criar admin master")
        empresa_id = user.empresa_id

    if empresa_id is not None and not await _empresa_exists(db, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")
    novo = User(
        nome=data.nome, email=data.email,
        senha_hash=hash_senha(data.senha),
        role=data.role,
        empresa_id=empresa_id,
    )
    db.add(novo)
    await db.commit()
    await db.refresh(novo)
    await audit.registrar(
        db, tipo=TipoAtividade.usuario_criado, user=user, request=request,
        empresa_id=novo.empresa_id if novo.empresa_id is not None else user.empresa_id,
        alvo_tipo="user", alvo_nome=novo.nome,
        detalhe=f"{novo.role.value} · {novo.email}",
    )
    return novo

@router.put("/{user_id}", response_model=UserOut)
async def atualizar_usuario(
    user_id: int, data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage_users(user):
        raise HTTPException(status_code=403, detail="Permissão insuficiente")
    result = await db.execute(select(User).where(User.id == user_id))
    alvo = result.scalar_one_or_none()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if not is_master(user):
        # admin_empresa só edita usuários da própria empresa e não mexe em admin master
        if alvo.empresa_id != user.empresa_id or alvo.role == UserRole.admin:
            raise HTTPException(status_code=403, detail="Sem permissão sobre este usuário")

    update = data.model_dump(exclude_none=True)

    # Admin_empresa não pode promover para admin master nem mover para outra empresa
    if not is_master(user):
        if update.get("role") == UserRole.admin:
            raise HTTPException(status_code=403, detail="Não pode promover a admin master")
        if "empresa_id" in update and update["empresa_id"] != user.empresa_id:
            raise HTTPException(status_code=403, detail="Não pode mover para outra empresa")

    if "empresa_id" in update and update["empresa_id"] is not None:
        if not await _empresa_exists(db, update["empresa_id"]):
            raise HTTPException(status_code=404, detail="Empresa não encontrada")

    if "senha" in update:
        alvo.senha_hash = hash_senha(update.pop("senha"))
    for field, value in update.items():
        setattr(alvo, field, value)
    await db.commit()
    await db.refresh(alvo)
    return alvo

@router.delete("/{user_id}")
async def deletar_usuario(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _can_manage_users(user):
        raise HTTPException(status_code=403, detail="Permissão insuficiente")
    result = await db.execute(select(User).where(User.id == user_id))
    alvo = result.scalar_one_or_none()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if alvo.id == user.id:
        raise HTTPException(status_code=400, detail="Não pode deletar a si mesmo")
    if not is_master(user):
        if alvo.empresa_id != user.empresa_id or alvo.role == UserRole.admin:
            raise HTTPException(status_code=403, detail="Sem permissão sobre este usuário")
    await db.delete(alvo)
    await db.commit()
    return {"ok": True}
