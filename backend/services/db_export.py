"""Export criptografado do banco de backups em arquivos .nxbak (v2.0.0).

Objetivo:
- Snapshot diário de TODOS os backups armazenados, num formato proprietário
  binário, compactado (gzip) e criptografado (Fernet).
- Arquivos vão pra /infra/db-export (volume Docker), retenção 7 arquivos.
- 1× por semana, o mais recente é enviado pra "nuvem de segurança"
  externa via SFTP ou FTP (configurável em Settings).
- A criptografia usa DB_EXPORT_KEY (Fernet, no .env) — chave SEPARADA da
  ENCRYPTION_KEY que cifra senhas SSH. Defesa em profundidade: se uma
  vazar, a outra mantém os dados seguros.

Formato do arquivo .nxbak (binário, byte layout):

    offset 0    : 16 bytes  magic ASCII "NEXUSBACKUPv200\n"
    offset 16   :  1 byte   format_version (0x01)
    offset 17   :  8 bytes  payload_length (uint64 big-endian)
    offset 25   :  N bytes  Fernet token (base64 textual, mas tratado como bytes)

Dentro do Fernet, após decifrar:

    gzip blob → JSON UTF-8:
    {
      "metadata": {format_version, exported_at, instance, total_*},
      "empresas": [...],
      "devices":  [...],          (sem senhas cifradas — só metadata)
      "backups":  [...]           (conteúdo completo, binário em base64)
    }

Pra abrir em ferramenta externa: ler magic+versão+length, extrair token,
Fernet.decrypt(token), gzip.decompress, json.loads. Documentado em
docs/NXBAK_FORMAT.md.
"""

import base64
import gzip
import io
import json
import logging
import os
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Backup, Device, Empresa, Configuracao
from services.crypto import decrypt as decrypt_db_value

log = logging.getLogger(__name__)


# Diretório onde os .nxbak são salvos. Em prod, montado como volume do compose
# em ./infra/db-export → /app/infra/db-export. Default fallback funciona em dev.
DB_EXPORT_DIR = Path(os.environ.get("DB_EXPORT_DIR", "/app/infra/db-export"))
# Retenção igual aos backups normais (7 arquivos), mas sob nome diferente
# pra deixar claro que não é o BACKUP_RETENTION_DAYS do .env.
DB_EXPORT_RETENCAO = 7

# Header binário do .nxbak. Tamanho fixo = 16+1+8 = 25 bytes.
NXBAK_MAGIC = b"NEXUSBACKUPv200\n"  # 16 bytes
NXBAK_FORMAT_VERSION = 0x01
NXBAK_HEADER_SIZE = 16 + 1 + 8


def get_fernet() -> Optional[Fernet]:
    """Retorna o Fernet pronto pra cifrar/decifrar, ou None se DB_EXPORT_KEY
    não está configurada. Chamador deve checar None antes de prosseguir."""
    key = settings.DB_EXPORT_KEY
    if not key or len(key) < 32:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        log.error("DB_EXPORT_KEY inválida: %s", e)
        return None


def _instance_id() -> str:
    """Identificador estável da instância (hostname + 8 chars hash do SECRET_KEY).
    Vai dentro do .nxbak pra ferramenta externa saber de qual servidor veio,
    mas sem expor o secret. Não-sensível."""
    import hashlib
    host = socket.gethostname()
    secret_hash = hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:8]
    return f"{host}-{secret_hash}"


async def _coletar_dados(db: AsyncSession) -> dict:
    """Lê tudo do banco e monta o dict que vai ser serializado.

    O que vai:
    - empresas: id, nome, cnpj (sem telegram_chat_id — config interna)
    - devices: id, nome, ip, porta, fabricante, tipo, protocolo, empresa_id, criado_em
      (SEM senhas/chaves cifradas — sem a ENCRYPTION_KEY local elas seriam
      inúteis na ferramenta externa, e expor o ciphertext não agrega valor)
    - backups: tudo (id, device_id, status, conteúdo, erro, origem,
      nome_arquivo, criado_em, log_scheduler_id, duracao_segundos)
    """
    empresas_rows = (await db.execute(select(Empresa))).scalars().all()
    devices_rows = (await db.execute(select(Device))).scalars().all()
    # Ordena backups por criado_em pra que ferramenta externa receba
    # cronologicamente — é como o usuário vai querer ver.
    backups_rows = (
        await db.execute(select(Backup).order_by(Backup.criado_em))
    ).scalars().all()

    return {
        "metadata": {
            "format_version": NXBAK_FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "instance_id": _instance_id(),
            "total_empresas": len(empresas_rows),
            "total_devices": len(devices_rows),
            "total_backups": len(backups_rows),
        },
        "empresas": [
            {
                "id": e.id,
                "nome": e.nome,
                "cnpj": e.cnpj,
                "criado_em": e.criado_em.isoformat() if e.criado_em else None,
            } for e in empresas_rows
        ],
        "devices": [
            {
                "id": d.id,
                "nome": d.nome,
                "ip": d.ip,
                "porta": d.porta,
                "fabricante": d.fabricante.value if d.fabricante else None,
                "tipo": d.tipo.value if d.tipo else None,
                "protocolo": d.protocolo.value if d.protocolo else None,
                "empresa_id": d.empresa_id,
                "criado_em": d.criado_em.isoformat() if d.criado_em else None,
            } for d in devices_rows
        ],
        "backups": [
            {
                "id": b.id,
                "device_id": b.device_id,
                "status": b.status,
                "origem": b.origem,
                "nome_arquivo": b.nome_arquivo,
                "log_scheduler_id": b.log_scheduler_id,
                "duracao_segundos": b.duracao_segundos,
                "criado_em": b.criado_em.isoformat() if b.criado_em else None,
                "conteudo": b.conteudo,  # já vem com prefixo BASE64: se binário
                "erro": b.erro,
            } for b in backups_rows
        ],
    }


def _empacotar(payload_json: bytes, fernet: Fernet) -> bytes:
    """Comprime + criptografa o payload e gera os bytes finais do .nxbak."""
    # gzip nível 9 — export roda 1x/dia, vale gastar CPU pra economizar disco.
    compressed = gzip.compress(payload_json, compresslevel=9)
    token = fernet.encrypt(compressed)  # bytes (base64-textual)

    header = NXBAK_MAGIC + bytes([NXBAK_FORMAT_VERSION]) + struct.pack(">Q", len(token))
    assert len(header) == NXBAK_HEADER_SIZE
    return header + token


async def exportar_para_arquivo(db: AsyncSession) -> Optional[Path]:
    """Gera o .nxbak do estado atual do banco. Retorna o Path do arquivo
    gerado ou None se DB_EXPORT_KEY não configurada."""
    fernet = get_fernet()
    if fernet is None:
        log.warning("DB_EXPORT_KEY não configurada — export pulado")
        return None

    DB_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    arquivo = DB_EXPORT_DIR / f"nexus-db-{timestamp}.nxbak"

    t0 = time.monotonic()
    dados = await _coletar_dados(db)
    payload = json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8")
    bytes_finais = _empacotar(payload, fernet)

    # Escreve atômico: arquivo temp + rename. Evita ler arquivo meio-gravado
    # se a ferramenta externa rodar concomitante.
    tmp = arquivo.with_suffix(".nxbak.tmp")
    tmp.write_bytes(bytes_finais)
    os.chmod(tmp, 0o600)  # só o user do container lê
    tmp.rename(arquivo)

    dur = time.monotonic() - t0
    log.warning(
        "DB export OK: arquivo=%s tamanho=%d bytes total_backups=%d duracao=%.1fs",
        arquivo.name, len(bytes_finais), dados["metadata"]["total_backups"], dur,
    )
    return arquivo


def listar_arquivos_locais() -> list[Path]:
    """Lista .nxbak existentes, ordenados do mais novo pro mais antigo."""
    if not DB_EXPORT_DIR.exists():
        return []
    arquivos = sorted(
        DB_EXPORT_DIR.glob("nexus-db-*.nxbak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return arquivos


def aplicar_retencao() -> int:
    """Mantém apenas os últimos DB_EXPORT_RETENCAO arquivos. Retorna quantos
    foram apagados."""
    arquivos = listar_arquivos_locais()
    excedentes = arquivos[DB_EXPORT_RETENCAO:]
    for f in excedentes:
        try:
            f.unlink()
            log.info("DB export retencao: removido %s", f.name)
        except Exception as e:
            log.warning("DB export retencao: falha ao remover %s: %s", f.name, e)
    return len(excedentes)


# ─────────────────────────────────────────────────────────────────────────────
# Upload pra nuvem de segurança (SFTP ou FTP)
# ─────────────────────────────────────────────────────────────────────────────


def _upload_sftp(arquivo: Path, host: str, porta: int, user: str, senha: str,
                 remote_path: str) -> None:
    """Envia via SFTP usando paramiko (já presente no projeto). Cria
    diretório remoto se não existir."""
    import paramiko
    transport = paramiko.Transport((host, porta))
    transport.connect(username=user, password=senha)
    try:
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            # Cria diretório remoto recursivamente se não existir.
            # Tenta cd; falha = cria.
            partes = [p for p in remote_path.split("/") if p]
            caminho = ""
            for parte in partes:
                caminho += "/" + parte
                try:
                    sftp.stat(caminho)
                except FileNotFoundError:
                    sftp.mkdir(caminho)

            # Upload — sobrescreve se existir (mesmo nome de timestamp é evento raro
            # mas safe demais ignorar conflito do que falhar).
            destino = f"{remote_path.rstrip('/')}/{arquivo.name}"
            sftp.put(str(arquivo), destino)
        finally:
            sftp.close()
    finally:
        transport.close()


def _upload_ftp(arquivo: Path, host: str, porta: int, user: str, senha: str,
                remote_path: str) -> None:
    """Envia via FTP plano (stdlib ftplib). CREDENCIAL VAI EM CLARO PELA REDE —
    o conteúdo do .nxbak já é criptografado, mas usuário/senha não."""
    from ftplib import FTP
    ftp = FTP()
    ftp.connect(host, porta, timeout=60)
    try:
        ftp.login(user=user, passwd=senha)
        # Garante diretório remoto. FTP não tem mkdir recursivo padrão.
        partes = [p for p in remote_path.split("/") if p]
        ftp.cwd("/")
        for parte in partes:
            try:
                ftp.cwd(parte)
            except Exception:
                ftp.mkd(parte)
                ftp.cwd(parte)
        # Upload binário
        with arquivo.open("rb") as f:
            ftp.storbinary(f"STOR {arquivo.name}", f)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


async def enviar_mais_recente_pra_nuvem(db: AsyncSession) -> tuple[bool, str]:
    """Pega o .nxbak mais recente e envia pro servidor remoto configurado.
    Retorna (sucesso, mensagem)."""
    config = (await db.execute(select(Configuracao))).scalar_one_or_none()
    if config is None or not config.db_export_remote_enabled:
        return False, "Upload remoto desabilitado"

    arquivos = listar_arquivos_locais()
    if not arquivos:
        return False, "Nenhum .nxbak disponível pra enviar"
    arquivo = arquivos[0]  # mais recente

    if not config.db_export_remote_host or not config.db_export_remote_user:
        return False, "Host/usuário remoto não configurado"
    if not config.db_export_remote_senha_enc:
        return False, "Senha remota não configurada"

    try:
        senha = decrypt_db_value(config.db_export_remote_senha_enc)
    except Exception as e:
        return False, f"Falha ao decifrar senha remota: {e}"

    protocolo = (config.db_export_remote_protocolo or "sftp").lower()
    try:
        t0 = time.monotonic()
        if protocolo == "sftp":
            # paramiko é bloqueante → roda em thread pra não travar o event loop
            import asyncio
            await asyncio.to_thread(
                _upload_sftp, arquivo,
                config.db_export_remote_host, config.db_export_remote_porta,
                config.db_export_remote_user, senha, config.db_export_remote_path or "/",
            )
        elif protocolo == "ftp":
            import asyncio
            await asyncio.to_thread(
                _upload_ftp, arquivo,
                config.db_export_remote_host, config.db_export_remote_porta,
                config.db_export_remote_user, senha, config.db_export_remote_path or "/",
            )
        else:
            return False, f"Protocolo desconhecido: {protocolo}"
        dur = time.monotonic() - t0
        msg = (
            f"Enviado {arquivo.name} ({arquivo.stat().st_size} bytes) via "
            f"{protocolo.upper()} pra {config.db_export_remote_host} em {dur:.1f}s"
        )
        log.warning("DB export remoto OK: %s", msg)
        return True, msg
    except Exception as e:
        msg = f"Falha {protocolo.upper()} pra {config.db_export_remote_host}: {e}"
        log.error("DB export remoto falhou: %s", msg)
        return False, msg
