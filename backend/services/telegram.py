"""Cliente do Telegram Bot API para alertas de falha/corrupção.

Topologia:
- 1 bot único (token global em Configuracao.telegram_bot_token_enc)
- Default chat_id em Configuracao.telegram_chat_id_default
- Override por empresa via Empresa.telegram_chat_id

Usado por:
- services/scheduler.py — falha de backup SSH/Telnet
- services/push_backup.py — IP fora whitelist, volume alto, falha de upload
- routers/settings.py — POST /telegram/test (botão "Enviar teste" no painel)

Implementação sync com httpx — escolhemos sync porque os hooks principais
rodam em threads (scheduler do APScheduler, FTP/SFTP/TFTP servers) que
não têm event loop. Endpoints async chamam via asyncio.to_thread.

Falha-tolerante: NUNCA propaga exceção pra cima — log + return False. Um
Telegram fora do ar não pode quebrar a coleta de backups.
"""
from __future__ import annotations
import logging
from typing import Optional
import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select
from models import Configuracao, Empresa
from services.crypto import decrypt

log = logging.getLogger("nexus.telegram")

API_BASE = "https://api.telegram.org/bot"
TIMEOUT = 10.0  # segundos — Telegram costuma responder em <1s


def _resolve_chat_id(db: Session, empresa_id: Optional[int], cfg: Configuracao) -> Optional[str]:
    """Decide qual chat_id usar: override da empresa OU default global.

    Retorna None se nenhum dos dois estiver setado (alerta vai pro vazio).
    """
    if empresa_id is not None:
        emp = db.execute(
            select(Empresa).where(Empresa.id == empresa_id)
        ).scalar_one_or_none()
        if emp and emp.telegram_chat_id:
            return emp.telegram_chat_id.strip()
    return (cfg.telegram_chat_id_default or "").strip() or None


def _carregar_config(db: Session) -> Optional[Configuracao]:
    """Lê a Configuracao singleton. Retorna None se não existir."""
    return db.execute(select(Configuracao)).scalar_one_or_none()


def _enviar_raw(token: str, chat_id: str, texto: str) -> bool:
    """POST /sendMessage. Retorna True se a Bot API confirmou recebimento."""
    url = f"{API_BASE}{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "HTML",
        # Notificação silenciosa em massa atrapalha. Deixa default (com som).
        "disable_web_page_preview": True,
    }
    try:
        r = httpx.post(url, json=payload, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("ok"):
            return True
        log.warning(
            "Telegram sendMessage falhou: status=%s body=%s chat_id=%s",
            r.status_code, r.text[:200], chat_id,
        )
        return False
    except httpx.HTTPError as e:
        log.warning("Telegram sendMessage erro HTTP: %s chat_id=%s", e, chat_id)
        return False
    except Exception:
        log.exception("Telegram sendMessage exception chat_id=%s", chat_id)
        return False


def enviar_alerta(
    db: Session,
    titulo: str,
    detalhes: str,
    empresa_id: Optional[int] = None,
    categoria: str = "falha_backup",
) -> bool:
    """Envia alerta pro chat resolvido (empresa ou default).

    `categoria` controla qual flag de toggle é checado:
    - 'falha_backup' → telegram_alerta_falha_backup
    - 'push_negado'  → telegram_alerta_push_negado
    - 'volume_alto'  → telegram_alerta_volume_alto

    Retorna False sem erro se Telegram desconfigurado, categoria desligada,
    ou chat_id não resolvido — chamadores não devem se preocupar com isso.
    """
    cfg = _carregar_config(db)
    if cfg is None or not cfg.telegram_bot_token_enc:
        return False  # Telegram não configurado — silencioso

    # Verifica toggle por categoria
    flag_attr = f"telegram_alerta_{categoria}"
    if not getattr(cfg, flag_attr, True):
        return False  # categoria desligada

    chat_id = _resolve_chat_id(db, empresa_id, cfg)
    if not chat_id:
        log.info("Telegram: alerta '%s' sem chat_id resolvido (empresa_id=%s)", titulo, empresa_id)
        return False

    try:
        token = decrypt(cfg.telegram_bot_token_enc)
    except Exception:
        log.exception("Telegram: token criptografado inválido — não envia")
        return False

    # Formato HTML compacto. Usa <b>/<code>/<i> que o Telegram aceita.
    texto = f"<b>{titulo}</b>\n{detalhes}"
    return _enviar_raw(token, chat_id, texto)


def enviar_teste(db: Session, chat_id_explicito: Optional[str] = None) -> tuple[bool, str]:
    """Botão 'Enviar teste' do painel. Retorna (ok, mensagem_pra_ui).

    Se chat_id_explicito for passado, usa esse (admin testando o chat de uma
    empresa específica antes de salvar). Caso contrário usa o default global.
    """
    cfg = _carregar_config(db)
    if cfg is None or not cfg.telegram_bot_token_enc:
        return False, "Bot não configurado — preencha o token primeiro."

    chat_id = chat_id_explicito or cfg.telegram_chat_id_default
    if not chat_id:
        return False, "Sem chat_id — preencha um chat default ou um por empresa."

    try:
        token = decrypt(cfg.telegram_bot_token_enc)
    except Exception:
        return False, "Token criptografado inválido — re-cadastre o token."

    ok = _enviar_raw(token, chat_id.strip(), "✅ <b>NEXUS BETA</b>\nTeste de notificação OK.")
    if ok:
        return True, f"Mensagem enviada com sucesso ao chat {chat_id}."
    return False, f"Falha ao enviar — verifique se o bot está no grupo {chat_id} e se o token está correto."


# ===== Versões async =====
# Scheduler e endpoints FastAPI usam AsyncSession. Para evitar conversão de
# session async→sync (criaria conexão extra), duplicamos a lógica de leitura
# aqui. O envio HTTP em si reusa _enviar_raw via asyncio.to_thread.
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_chat_id_async(
    db: AsyncSession, empresa_id: Optional[int], cfg: Configuracao,
) -> Optional[str]:
    if empresa_id is not None:
        emp = (await db.execute(
            select(Empresa).where(Empresa.id == empresa_id)
        )).scalar_one_or_none()
        if emp and emp.telegram_chat_id:
            return emp.telegram_chat_id.strip()
    return (cfg.telegram_chat_id_default or "").strip() or None


async def enviar_alerta_async(
    db: AsyncSession,
    titulo: str,
    detalhes: str,
    empresa_id: Optional[int] = None,
    categoria: str = "falha_backup",
) -> bool:
    """Versão async — usa AsyncSession do scheduler/endpoints."""
    cfg = (await db.execute(select(Configuracao))).scalar_one_or_none()
    if cfg is None or not cfg.telegram_bot_token_enc:
        return False
    if not getattr(cfg, f"telegram_alerta_{categoria}", True):
        return False

    chat_id = await _resolve_chat_id_async(db, empresa_id, cfg)
    if not chat_id:
        return False

    try:
        token = decrypt(cfg.telegram_bot_token_enc)
    except Exception:
        log.exception("Telegram: token criptografado inválido — não envia")
        return False

    texto = f"<b>{titulo}</b>\n{detalhes}"
    # Roda o POST sync em threadpool pra não bloquear o event loop
    return await asyncio.to_thread(_enviar_raw, token, chat_id, texto)


async def enviar_teste_async(
    db: AsyncSession, chat_id_explicito: Optional[str] = None,
) -> tuple[bool, str]:
    """Versão async do botão 'Enviar teste' — usada pelo endpoint /telegram/test."""
    cfg = (await db.execute(select(Configuracao))).scalar_one_or_none()
    if cfg is None or not cfg.telegram_bot_token_enc:
        return False, "Bot não configurado — preencha o token primeiro."

    chat_id = chat_id_explicito or cfg.telegram_chat_id_default
    if not chat_id:
        return False, "Sem chat_id — preencha um chat default ou um por empresa."

    try:
        token = decrypt(cfg.telegram_bot_token_enc)
    except Exception:
        return False, "Token criptografado inválido — re-cadastre o token."

    ok = await asyncio.to_thread(
        _enviar_raw, token, chat_id.strip(),
        "✅ <b>NEXUS BETA</b>\nTeste de notificação OK.",
    )
    if ok:
        return True, f"Mensagem enviada com sucesso ao chat {chat_id}."
    return False, f"Falha ao enviar — verifique se o bot está no grupo {chat_id} e se o token está correto."
