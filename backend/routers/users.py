from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import User, UserRole
from auth import hash_senha, require_role
from schemas import UserCreate, UserUpdate, UserOut
from typing import List

router = APIRouter(prefix="/api/users", tags=["users"])

@router.get("/", response_model=List[UserOut], dependencies=[Depends(require_role(UserRole.admin))])
async def listar_usuarios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.nome))
    return result.scalars().all()

@router.post("/", response_model=UserOut, dependencies=[Depends(require_role(UserRole.admin))])
async def criar_usuario(data: UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado")
    user = User(nome=data.nome, email=data.email, senha_hash=hash_senha(data.senha), role=data.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.put("/{user_id}", response_model=UserOut, dependencies=[Depends(require_role(UserRole.admin))])
async def atualizar_usuario(user_id: int, data: UserUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/{user_id}", dependencies=[Depends(require_role(UserRole.admin))])
async def deletar_usuario(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    await db.delete(user)
    await db.commit()
    return {"ok": True}
