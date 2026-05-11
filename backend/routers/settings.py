from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Configuracao, UserRole
from schemas import (
    ScheduleOut, ScheduleUpdate,
    TelegramConfigOut, TelegramConfigUpdate, TelegramTestRequest,
)
from auth import require_role
from services.scheduler import atualizar_scheduler
from services.crypto import encrypt
from services.telegram import enviar_teste_async

router = APIRouter(prefix="/api/settings", tags=["settings"])


async def _get_or_create_config(db: AsyncSession) -> Configuracao:
    result = await db.execute(select(Configuracao))
    config = result.scalar_one_or_none()
    if config is None:
        config = Configuracao(backup_hour=2, backup_minute=0)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


@router.get("/schedule", response_model=ScheduleOut)
async def get_schedule(db: AsyncSession = Depends(get_db)):
    return await _get_or_create_config(db)


@router.put("/schedule", response_model=ScheduleOut)
async def update_schedule(
    body: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador)),
):
    if not (0 <= body.backup_hour <= 23):
        raise HTTPException(status_code=422, detail="Hora deve ser entre 0 e 23")
    if not (0 <= body.backup_minute <= 59):
        raise HTTPException(status_code=422, detail="Minuto deve ser entre 0 e 59")
    if body.log_retention_days is not None and not (0 <= body.log_retention_days <= 3650):
        raise HTTPException(status_code=422, detail="Retenção de logs deve ser entre 0 e 3650 dias")
    # Limites de tuning do delay: valores grandes demais travariam a janela noturna,
    # valores negativos não fazem sentido.
    if body.backup_delay_min_seg is not None and not (0 <= body.backup_delay_min_seg <= 600):
        raise HTTPException(status_code=422, detail="Delay mínimo deve ser entre 0 e 600 segundos")
    if body.backup_delay_fator is not None and not (0.0 <= body.backup_delay_fator <= 10.0):
        raise HTTPException(status_code=422, detail="Fator de delay deve ser entre 0 e 10")
    if body.backup_pico_fator_critico is not None and not (1.0 <= body.backup_pico_fator_critico <= 100.0):
        raise HTTPException(status_code=422, detail="Fator de pico crítico deve ser ≥ 1 (1 = qualquer aumento conta)")

    config = await _get_or_create_config(db)
    config.backup_hour = body.backup_hour
    config.backup_minute = body.backup_minute
    if body.log_retention_days is not None:
        config.log_retention_days = body.log_retention_days
    if body.backup_delay_min_seg is not None:
        config.backup_delay_min_seg = body.backup_delay_min_seg
    if body.backup_delay_fator is not None:
        config.backup_delay_fator = body.backup_delay_fator
    if body.backup_pico_fator_critico is not None:
        config.backup_pico_fator_critico = body.backup_pico_fator_critico
    await db.commit()
    await db.refresh(config)

    atualizar_scheduler(config.backup_hour, config.backup_minute)
    return config


# ===== Telegram =====
# Notificações de falha/corrupção. Apenas admin master gerencia config global.
# Override por empresa fica no router de empresas.

@router.get("/telegram", response_model=TelegramConfigOut)
async def get_telegram(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Estado atual. Token nunca é exposto cru — só um booleano 'configurado'."""
    config = await _get_or_create_config(db)
    return TelegramConfigOut(
        bot_configurado=bool(config.telegram_bot_token_enc),
        chat_id_default=config.telegram_chat_id_default,
        alerta_falha_backup=config.telegram_alerta_falha_backup,
        alerta_push_negado=config.telegram_alerta_push_negado,
        alerta_volume_alto=config.telegram_alerta_volume_alto,
    )


@router.put("/telegram", response_model=TelegramConfigOut)
async def update_telegram(
    body: TelegramConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Atualiza config Telegram. bot_token vazio (string vazia) = limpa/desabilita;
    None = mantém o valor atual (não tocar no token criptografado)."""
    config = await _get_or_create_config(db)

    if body.bot_token is not None:
        # Validação rápida do formato: '<numero>:<base64-ish>' ~46 chars.
        if body.bot_token == "":
            config.telegram_bot_token_enc = None
        else:
            tk = body.bot_token.strip()
            if ":" not in tk or len(tk) < 20:
                raise HTTPException(status_code=422, detail="Token do bot Telegram com formato inválido (esperado '<id>:<hash>').")
            config.telegram_bot_token_enc = encrypt(tk)

    if body.chat_id_default is not None:
        config.telegram_chat_id_default = body.chat_id_default.strip() or None

    if body.alerta_falha_backup is not None:
        config.telegram_alerta_falha_backup = body.alerta_falha_backup
    if body.alerta_push_negado is not None:
        config.telegram_alerta_push_negado = body.alerta_push_negado
    if body.alerta_volume_alto is not None:
        config.telegram_alerta_volume_alto = body.alerta_volume_alto

    await db.commit()
    await db.refresh(config)
    return TelegramConfigOut(
        bot_configurado=bool(config.telegram_bot_token_enc),
        chat_id_default=config.telegram_chat_id_default,
        alerta_falha_backup=config.telegram_alerta_falha_backup,
        alerta_push_negado=config.telegram_alerta_push_negado,
        alerta_volume_alto=config.telegram_alerta_volume_alto,
    )


@router.post("/telegram/test")
async def post_telegram_test(
    body: TelegramTestRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin, UserRole.admin_empresa)),
):
    """Botão 'Enviar teste'. admin_empresa pode testar pra validar o chat_id da
    própria empresa antes de salvar; admin master testa o default global ou
    qualquer chat fornecido."""
    ok, msg = await enviar_teste_async(db, chat_id_explicito=body.chat_id)
    return {"ok": ok, "mensagem": msg}
