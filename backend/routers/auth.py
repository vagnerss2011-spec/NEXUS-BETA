from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from database import get_db
from models import User, TipoAtividade
from auth import hash_senha, verificar_senha, criar_token, get_current_user, get_current_user_basic
from schemas import Token, UserOut, ChangePasswordIn
from services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Lockout: trava conta por LOCKOUT_MINUTES após MAX_FAILED_ATTEMPTS senhas erradas.
# Valores conservadores — atrapalha bruteforce mas não usuário legítimo que digitou
# errado algumas vezes em sequência. Janela curta porque rate limit do nginx já
# está cuidando do volume.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    # Conta bloqueada? Responde 423 Locked com tempo restante (em minutos arredondado p/ cima).
    if user and user.bloqueado_ate and user.bloqueado_ate > now:
        restante_seg = (user.bloqueado_ate - now).total_seconds()
        restante_min = max(1, int(restante_seg // 60) + (1 if restante_seg % 60 else 0))
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Conta bloqueada por excesso de tentativas. Tente novamente em ~{restante_min} min.",
        )

    if not user or not verificar_senha(form.password, user.senha_hash):
        # Senha errada: incrementa contador e trava se atingiu o limite.
        # Importante: só faz isso se o user existe — caso contrário, só erro genérico
        # (não criamos linha de "tentativa contra usuário inexistente" pra não
        # dar pista ao atacante de quais emails são válidos).
        if user:
            user.tentativas_falhas = (user.tentativas_falhas or 0) + 1
            if user.tentativas_falhas >= MAX_FAILED_ATTEMPTS:
                user.bloqueado_ate = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.tentativas_falhas = 0  # zera contador junto com o lock
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    if not user.ativo:
        raise HTTPException(status_code=403, detail="Usuário inativo")

    # Sucesso: zera contador e qualquer lock pendente
    if user.tentativas_falhas or user.bloqueado_ate:
        user.tentativas_falhas = 0
        user.bloqueado_ate = None
        await db.commit()

    token = criar_token({"sub": str(user.id)})
    await audit.registrar(db, tipo=TipoAtividade.login, user=user, request=request)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_basic),
):
    await audit.registrar(db, tipo=TipoAtividade.logout, user=current_user, request=request)
    return {"ok": True}

@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user_basic)):
    """Permite obter os dados do próprio usuário mesmo com senha temporária —
    o frontend usa esse endpoint pra detectar a flag e redirecionar."""
    return current_user


@router.post("/change-password", response_model=UserOut)
async def change_password(
    data: ChangePasswordIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_basic),
):
    """Troca a senha do usuário logado. Funciona mesmo com senha_temporaria=True
    (caso de primeiro login)."""
    if not verificar_senha(data.senha_atual, user.senha_hash):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if verificar_senha(data.senha_nova, user.senha_hash):
        raise HTTPException(status_code=400, detail="A nova senha precisa ser diferente da atual")
    user.senha_hash = hash_senha(data.senha_nova)
    user.senha_temporaria = False
    # Reset de qualquer lock pendente — após trocar senha, a sessão limpa.
    user.tentativas_falhas = 0
    user.bloqueado_ate = None
    await db.commit()
    await db.refresh(user)
    return user
