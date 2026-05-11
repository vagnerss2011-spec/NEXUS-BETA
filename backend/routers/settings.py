from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database import get_db
from models import Configuracao, UserRole
from schemas import (
    ScheduleOut, ScheduleUpdate,
    TelegramConfigOut, TelegramConfigUpdate, TelegramTestRequest,
    DbExportConfigOut, DbExportConfigUpdate,
)
from auth import require_role
from services.scheduler import (
    atualizar_scheduler, atualizar_db_export_local, atualizar_db_export_remoto,
)
from services.crypto import encrypt
from services.telegram import enviar_teste_async
from services import db_export as _dbex

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
    # Paralelismo: cap hard absoluto = 8 (mesmo se admin tentar pôr 50, fica 8 no banco).
    # CPU/RAM limites entre 30 e 95% (abaixo é exagero, acima não dá segurança).
    if body.backup_workers_max_api is not None and not (1 <= body.backup_workers_max_api <= 8):
        raise HTTPException(status_code=422, detail="Workers max API deve estar entre 1 e 8")
    if body.backup_workers_max_ssh is not None and not (1 <= body.backup_workers_max_ssh <= 8):
        raise HTTPException(status_code=422, detail="Workers max SSH deve estar entre 1 e 8")
    if body.backup_cpu_limite_pct is not None and not (30 <= body.backup_cpu_limite_pct <= 95):
        raise HTTPException(status_code=422, detail="Limite de CPU deve estar entre 30 e 95%")
    if body.backup_mem_limite_pct is not None and not (30 <= body.backup_mem_limite_pct <= 95):
        raise HTTPException(status_code=422, detail="Limite de memória deve estar entre 30 e 95%")

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
    if body.backup_workers_max_api is not None:
        config.backup_workers_max_api = body.backup_workers_max_api
    if body.backup_workers_max_ssh is not None:
        config.backup_workers_max_ssh = body.backup_workers_max_ssh
    if body.backup_cpu_limite_pct is not None:
        config.backup_cpu_limite_pct = body.backup_cpu_limite_pct
    if body.backup_mem_limite_pct is not None:
        config.backup_mem_limite_pct = body.backup_mem_limite_pct
    if body.backup_workers_auto is not None:
        config.backup_workers_auto = body.backup_workers_auto
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


# ===== Export do banco (.nxbak — v2.0.0) =====
# Só admin master gerencia. A senha do servidor remoto NUNCA é exposta pela
# API — só um booleano "configurada".

async def _montar_db_export_out(db: AsyncSession) -> DbExportConfigOut:
    """Helper que monta o response do GET/PUT — reusado por ambos endpoints."""
    config = await _get_or_create_config(db)
    fernet = _dbex.get_fernet()
    arquivos = _dbex.listar_arquivos_locais()
    return DbExportConfigOut(
        db_export_enabled=config.db_export_enabled,
        db_export_hour=config.db_export_hour,
        db_export_minute=config.db_export_minute,
        db_export_remote_enabled=config.db_export_remote_enabled,
        db_export_remote_protocolo=config.db_export_remote_protocolo,
        db_export_remote_host=config.db_export_remote_host,
        db_export_remote_porta=config.db_export_remote_porta,
        db_export_remote_user=config.db_export_remote_user,
        db_export_remote_senha_configurada=bool(config.db_export_remote_senha_enc),
        db_export_remote_path=config.db_export_remote_path,
        db_export_remote_dia_semana=config.db_export_remote_dia_semana,
        db_export_remote_hora=config.db_export_remote_hora,
        db_export_remote_minute=config.db_export_remote_minute,
        chave_configurada=fernet is not None,
        arquivos_locais=[
            {
                "nome": f.name,
                "tamanho_bytes": f.stat().st_size,
                "modificado_em": f.stat().st_mtime,
            } for f in arquivos
        ],
    )


@router.get("/db-export", response_model=DbExportConfigOut)
async def get_db_export(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    return await _montar_db_export_out(db)


@router.put("/db-export", response_model=DbExportConfigOut)
async def put_db_export(
    body: DbExportConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    # Validações
    if body.db_export_hour is not None and not (0 <= body.db_export_hour <= 23):
        raise HTTPException(status_code=422, detail="Hora do export deve ser 0-23")
    if body.db_export_minute is not None and not (0 <= body.db_export_minute <= 59):
        raise HTTPException(status_code=422, detail="Minuto do export deve ser 0-59")
    if body.db_export_remote_protocolo is not None and body.db_export_remote_protocolo not in ("sftp", "ftp"):
        raise HTTPException(status_code=422, detail="Protocolo deve ser 'sftp' ou 'ftp'")
    if body.db_export_remote_porta is not None and not (1 <= body.db_export_remote_porta <= 65535):
        raise HTTPException(status_code=422, detail="Porta inválida")
    if body.db_export_remote_dia_semana is not None and not (0 <= body.db_export_remote_dia_semana <= 6):
        raise HTTPException(status_code=422, detail="Dia da semana deve ser 0-6 (0=segunda, 6=domingo)")
    if body.db_export_remote_hora is not None and not (0 <= body.db_export_remote_hora <= 23):
        raise HTTPException(status_code=422, detail="Hora remota deve ser 0-23")
    if body.db_export_remote_minute is not None and not (0 <= body.db_export_remote_minute <= 59):
        raise HTTPException(status_code=422, detail="Minuto remoto deve ser 0-59")

    config = await _get_or_create_config(db)
    # Aplica apenas campos enviados (não-None).
    for campo in (
        "db_export_enabled", "db_export_hour", "db_export_minute",
        "db_export_remote_enabled", "db_export_remote_protocolo",
        "db_export_remote_host", "db_export_remote_porta",
        "db_export_remote_user", "db_export_remote_path",
        "db_export_remote_dia_semana", "db_export_remote_hora",
        "db_export_remote_minute",
    ):
        val = getattr(body, campo)
        if val is not None:
            setattr(config, campo, val)
    # Senha: regra especial (None = não tocar; '' = limpar; valor = encrypt).
    if body.db_export_remote_senha is not None:
        if body.db_export_remote_senha == "":
            config.db_export_remote_senha_enc = None
        else:
            config.db_export_remote_senha_enc = encrypt(body.db_export_remote_senha)

    await db.commit()
    await db.refresh(config)

    # Reagenda os jobs do scheduler pra refletir mudança de horário.
    try:
        atualizar_db_export_local(config.db_export_hour, config.db_export_minute)
        atualizar_db_export_remoto(
            config.db_export_remote_dia_semana,
            config.db_export_remote_hora, config.db_export_remote_minute,
        )
    except Exception:
        # Reagendar pode falhar se o scheduler ainda não iniciou (raro).
        # Não bloqueia o save — próxima boot lê do banco.
        pass

    return await _montar_db_export_out(db)


@router.post("/db-export/run-now")
async def post_db_export_run_now(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Botão 'Exportar agora' — dispara export local imediato pra testar
    sem esperar a próxima janela. Mesmo fluxo do job scheduler diário."""
    arquivo = await _dbex.exportar_para_arquivo(db)
    if arquivo is None:
        raise HTTPException(
            status_code=400,
            detail="DB_EXPORT_KEY não configurada no .env — export desabilitado",
        )
    _dbex.aplicar_retencao()
    return {"ok": True, "arquivo": arquivo.name, "tamanho_bytes": arquivo.stat().st_size}


@router.post("/db-export/upload-now")
async def post_db_export_upload_now(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_role(UserRole.admin)),
):
    """Botão 'Enviar agora' — força upload do .nxbak mais recente pro remoto.
    Útil pra validar config (host/user/senha/path) sem esperar a janela semanal."""
    ok, msg = await _dbex.enviar_mais_recente_pra_nuvem(db)
    return {"ok": ok, "mensagem": msg}
