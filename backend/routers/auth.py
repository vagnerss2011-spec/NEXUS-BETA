from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import User, TipoAtividade
from auth import hash_senha, verificar_senha, criar_token, get_current_user
from schemas import Token, UserOut
from services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if not user or not verificar_senha(form.password, user.senha_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    if not user.ativo:
        raise HTTPException(status_code=403, detail="Usuário inativo")
    token = criar_token({"sub": str(user.id)})
    await audit.registrar(db, tipo=TipoAtividade.login, user=user, request=request)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await audit.registrar(db, tipo=TipoAtividade.logout, user=current_user, request=request)
    return {"ok": True}

@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user
