"""Mirror FTP de firmwares (v2.2.0).

Permite upload de firmwares pelo painel + cadastro de "origens" (credenciais
FTP) que devices usam pra baixar via /tool fetch (Mikrotik) ou comando
equivalente em outros fabricantes.

Arquivos vivem em FIRMWARE_DIR (/var/firmware/). Catálogo (metadados) na
tabela `firmwares`. Credenciais na tabela `firmware_origens` com senha
cifrada via Fernet — mostrada em texto puro só uma vez na criação/regeneração.

Uploads via FTP por origens (perm 'w') aterrissam direto no diretório como
"órfãos" — admin promove ou deleta pelo painel.

Permissões:
- Upload/edit/delete de firmware: admin + admin_empresa + operador
- CRUD de origens: admin + admin_empresa (operador NÃO cria origem — é credencial)
- Listagem: todos os roles autenticados
- Download via painel: todos os roles autenticados
"""
import hashlib
import logging
import os
import re
import secrets
import string
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, is_master, require_role
from database import get_db
from models import (
    Atividade, Empresa, Firmware, FirmwareOrigem, TipoAtividade, User, UserRole,
)
from schemas import (
    FirmwareOrfaoOut, FirmwareOrigemCreate, FirmwareOrigemCredencial,
    FirmwareOrigemOut, FirmwareOrigemUpdate, FirmwareOut, FirmwareUpdate,
)
from services.crypto import encrypt
from services import audit

log = logging.getLogger(__name__)

# Diretório compartilhado pelas origens FTP (chroot read+write).
# Igual ao FTP_FIRMWARE_DIR de ftp_server.py — duplicado aqui pra evitar
# import circular (router importa de services; service importa models).
FIRMWARE_DIR = "/var/firmware"

# Tamanho do chunk pra streaming de upload e cálculo de SHA256.
# 4 MB equilibra IO/CPU — chunks menores aumentam syscalls, maiores estouram
# memória de processos parallel uploads.
CHUNK_SIZE = 4 * 1024 * 1024

# Validação de nome de arquivo — rejeita path traversal e chars problemáticos
# em FTP de devices legados. Permitido: letras, dígitos, ponto, hífen, underscore.
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

router = APIRouter(prefix="/api", tags=["firmwares"])


# ─── Helpers ───

def _sanitizar_nome(nome_original: str) -> str:
    """Devolve nome de arquivo seguro pra disco/FTP.

    - Remove path traversal (basename only)
    - Substitui chars não-[A-Za-z0-9._-] por '_'
    - Garante extensão preservada
    - Limite de 200 chars (FTP de alguns devices trunca em 255)
    """
    base = os.path.basename(nome_original or "").strip()
    if not base:
        return f"firmware_{secrets.token_hex(8)}.bin"
    # Substitui chars problemáticos
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if len(base) > 200:
        # Preserva extensão ao truncar
        nome, _, ext = base.rpartition(".")
        if ext and len(ext) <= 10:
            base = nome[:200 - len(ext) - 1] + "." + ext
        else:
            base = base[:200]
    return base


def _nome_unico_no_disco(nome_sanitizado: str) -> str:
    """Se já existe arquivo com esse nome em FIRMWARE_DIR, adiciona sufixo
    numérico até achar livre. Preserva extensão pra device reconhecer."""
    destino = os.path.join(FIRMWARE_DIR, nome_sanitizado)
    if not os.path.exists(destino):
        return nome_sanitizado
    nome, _, ext = nome_sanitizado.rpartition(".")
    if not nome:
        nome, ext = nome_sanitizado, ""
    for i in range(1, 1000):
        candidato = f"{nome}_{i}.{ext}" if ext else f"{nome}_{i}"
        if not os.path.exists(os.path.join(FIRMWARE_DIR, candidato)):
            return candidato
    raise HTTPException(status_code=409, detail="Não foi possível gerar nome único")


def _gerar_user_origem(origem_id: int) -> str:
    """Username FTP da origem firmware. Formato 'fwm_NNNNN' (fwm = firmware mirror).
    Prefixo distinto de 'ftp_NNNNN' do device push pra evitar colisão no auth."""
    return f"fwm_{origem_id:05d}"


def _gerar_senha_origem(comprimento: int = 24) -> str:
    """Alfanumérica pura — clientes FTP de devices têm comportamento errático
    com chars de escape. 24 chars dá ~143 bits de entropia (suficiente sem CIDR)."""
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(comprimento))


def _calcular_sha256(filepath: str) -> str:
    """SHA256 do arquivo em hex lowercase. Útil pra device validar integridade."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _scope_firmwares(user: User, q):
    """Filtra firmwares por empresa do user (não-master vê só sua empresa).
    Master vê todos. Firmwares não têm empresa_id próprio — controle é por origem.
    Por enquanto: todos os roles autenticados veem todos os firmwares (catalogo
    compartilhado é a ideia do feature). Função reservada pra futuro split."""
    return q


def _to_origem_out(o: FirmwareOrigem) -> FirmwareOrigemOut:
    return FirmwareOrigemOut.model_validate(o)


# ─── Endpoints: Firmwares ───

@router.get("/firmwares", response_model=List[FirmwareOut])
async def listar_firmwares(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Firmware).order_by(Firmware.criado_em.desc())
    q = _scope_firmwares(user, q)
    rows = (await db.execute(q)).scalars().all()
    return [FirmwareOut.model_validate(r) for r in rows]


@router.post(
    "/firmwares/upload", response_model=FirmwareOut,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def upload_firmware(
    request: Request,
    arquivo: UploadFile = File(...),
    nome: str = Form(...),
    descricao: Optional[str] = Form(None),
    fabricante: Optional[str] = Form(None),
    modelo_alvo: Optional[str] = Form(None),
    versao: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload via multipart form. Stream pro disco (sem buffer em memória).

    Sem limite explícito de tamanho — uvicorn + nginx ficam responsáveis por
    timeout/buffer máximo do request body. Recomenda-se ajustar
    `client_max_body_size` no nginx pra cobrir o maior firmware esperado.
    """
    if not arquivo.filename:
        raise HTTPException(status_code=400, detail="Nome do arquivo ausente")

    os.makedirs(FIRMWARE_DIR, exist_ok=True)
    nome_sanitizado = _sanitizar_nome(arquivo.filename)
    nome_final = _nome_unico_no_disco(nome_sanitizado)
    destino = os.path.join(FIRMWARE_DIR, nome_final)

    # Stream chunk-a-chunk + calcula SHA256 ao mesmo tempo.
    h = hashlib.sha256()
    tamanho = 0
    try:
        with open(destino, "wb") as out:
            while True:
                chunk = await arquivo.read(CHUNK_SIZE)
                if not chunk:
                    break
                tamanho += len(chunk)
                h.update(chunk)
                out.write(chunk)
    except Exception as e:
        # Limpeza em caso de falha no meio do upload
        try:
            os.unlink(destino)
        except OSError:
            pass
        log.exception("Falha no upload de firmware %s", nome_sanitizado)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo: {e}")

    if tamanho == 0:
        try:
            os.unlink(destino)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    fw = Firmware(
        nome=nome.strip(),
        descricao=(descricao or "").strip() or None,
        arquivo_nome=nome_final,
        tamanho_bytes=tamanho,
        sha256=h.hexdigest(),
        fabricante=(fabricante or "").strip() or None,
        modelo_alvo=(modelo_alvo or "").strip() or None,
        versao=(versao or "").strip() or None,
        criado_por_id=user.id,
        criado_por_nome=user.nome,
    )
    db.add(fw)
    await db.flush()
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_upload_painel, user=user,
        request=request, alvo_tipo="firmware", alvo_nome=nome_final,
        detalhe=f"{tamanho} bytes · sha256={h.hexdigest()[:12]}…",
        commit=False,
    )
    await db.commit()
    await db.refresh(fw)
    return FirmwareOut.model_validate(fw)


@router.get("/firmwares/{firmware_id}/download")
async def download_firmware(
    firmware_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download direto via painel — mesma fonte que o FTP serve pras origens."""
    fw = (await db.execute(
        select(Firmware).where(Firmware.id == firmware_id)
    )).scalar_one_or_none()
    if not fw:
        raise HTTPException(status_code=404, detail="Firmware não encontrado")
    filepath = os.path.join(FIRMWARE_DIR, fw.arquivo_nome)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=410, detail="Arquivo perdido no disco — re-upload")
    return FileResponse(
        filepath,
        filename=fw.arquivo_nome,
        media_type="application/octet-stream",
    )


@router.patch(
    "/firmwares/{firmware_id}", response_model=FirmwareOut,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def atualizar_firmware(
    firmware_id: int,
    data: FirmwareUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fw = (await db.execute(
        select(Firmware).where(Firmware.id == firmware_id)
    )).scalar_one_or_none()
    if not fw:
        raise HTTPException(status_code=404, detail="Firmware não encontrado")
    if data.nome is not None:
        fw.nome = data.nome.strip()
    if data.descricao is not None:
        fw.descricao = data.descricao.strip() or None
    if data.fabricante is not None:
        fw.fabricante = data.fabricante.strip() or None
    if data.modelo_alvo is not None:
        fw.modelo_alvo = data.modelo_alvo.strip() or None
    if data.versao is not None:
        fw.versao = data.versao.strip() or None
    await db.commit()
    await db.refresh(fw)
    return FirmwareOut.model_validate(fw)


@router.delete(
    "/firmwares/{firmware_id}",
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def deletar_firmware(
    firmware_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fw = (await db.execute(
        select(Firmware).where(Firmware.id == firmware_id)
    )).scalar_one_or_none()
    if not fw:
        raise HTTPException(status_code=404, detail="Firmware não encontrado")
    nome_arquivo = fw.arquivo_nome
    filepath = os.path.join(FIRMWARE_DIR, nome_arquivo)
    # Apaga arquivo do disco primeiro — se falhar, mantém row pra retry.
    if os.path.exists(filepath):
        try:
            os.unlink(filepath)
        except OSError as e:
            log.exception("Falha ao apagar arquivo %s", filepath)
            raise HTTPException(status_code=500, detail=f"Erro ao apagar arquivo: {e}")
    await db.delete(fw)
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_removido, user=user,
        request=request, alvo_tipo="firmware", alvo_nome=nome_arquivo,
        commit=False,
    )
    await db.commit()
    return {"ok": True}


# ─── Endpoints: Órfãos (arquivos no disco sem row em firmwares) ───

@router.get("/firmwares/orfaos", response_model=List[FirmwareOrfaoOut])
async def listar_orfaos(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Arquivos físicos em /var/firmware/ que não têm row correspondente.

    Tipicamente: uploads via FTP por origens (perm 'w'). Painel oferece
    'promover a firmware' (com metadados) ou 'apagar'.
    """
    os.makedirs(FIRMWARE_DIR, exist_ok=True)
    catalogados = set(
        (await db.execute(select(Firmware.arquivo_nome))).scalars().all()
    )
    orfaos = []
    for entry in os.scandir(FIRMWARE_DIR):
        if not entry.is_file():
            continue
        if entry.name in catalogados:
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        orfaos.append(FirmwareOrfaoOut(
            arquivo_nome=entry.name,
            tamanho_bytes=st.st_size,
            modificado_em=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
        ))
    orfaos.sort(key=lambda o: o.modificado_em, reverse=True)
    return orfaos


@router.post(
    "/firmwares/orfaos/{arquivo_nome}/promover", response_model=FirmwareOut,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def promover_orfao(
    arquivo_nome: str,
    request: Request,
    nome: str = Form(...),
    descricao: Optional[str] = Form(None),
    fabricante: Optional[str] = Form(None),
    modelo_alvo: Optional[str] = Form(None),
    versao: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Adopta um órfão como firmware oficial — cria row sem mover o arquivo."""
    if not SAFE_FILENAME_RE.match(arquivo_nome):
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")
    filepath = os.path.join(FIRMWARE_DIR, arquivo_nome)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Órfão não encontrado")
    # Verifica que não está já catalogado (race contra outro admin promovendo)
    ja = (await db.execute(
        select(Firmware.id).where(Firmware.arquivo_nome == arquivo_nome)
    )).scalar_one_or_none()
    if ja:
        raise HTTPException(status_code=409, detail="Arquivo já catalogado")
    tamanho = os.path.getsize(filepath)
    sha = _calcular_sha256(filepath)
    fw = Firmware(
        nome=nome.strip(),
        descricao=(descricao or "").strip() or None,
        arquivo_nome=arquivo_nome,
        tamanho_bytes=tamanho,
        sha256=sha,
        fabricante=(fabricante or "").strip() or None,
        modelo_alvo=(modelo_alvo or "").strip() or None,
        versao=(versao or "").strip() or None,
        criado_por_id=user.id,
        criado_por_nome=user.nome,
    )
    db.add(fw)
    await db.flush()
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_upload_painel, user=user,
        request=request, alvo_tipo="firmware", alvo_nome=arquivo_nome,
        detalhe=f"promovido de órfão · {tamanho} bytes",
        commit=False,
    )
    await db.commit()
    await db.refresh(fw)
    return FirmwareOut.model_validate(fw)


@router.delete(
    "/firmwares/orfaos/{arquivo_nome}",
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))],
)
async def deletar_orfao(
    arquivo_nome: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not SAFE_FILENAME_RE.match(arquivo_nome):
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")
    filepath = os.path.join(FIRMWARE_DIR, arquivo_nome)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Órfão não encontrado")
    # Garante que não é um arquivo já catalogado (DELETE deveria ir pelo
    # endpoint /firmwares/{id} pra apagar row+arquivo juntos)
    ja = (await db.execute(
        select(Firmware.id).where(Firmware.arquivo_nome == arquivo_nome)
    )).scalar_one_or_none()
    if ja:
        raise HTTPException(
            status_code=409,
            detail="Arquivo está catalogado — use DELETE /firmwares/{id}",
        )
    try:
        os.unlink(filepath)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Erro ao apagar: {e}")
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_removido, user=user,
        request=request, alvo_tipo="firmware", alvo_nome=arquivo_nome,
        detalhe="órfão apagado",
    )
    return {"ok": True}


# ─── Endpoints: FirmwareOrigem (credenciais FTP) ───

@router.get("/firmware-origens", response_model=List[FirmwareOrigemOut])
async def listar_origens(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(FirmwareOrigem).order_by(FirmwareOrigem.criado_em.desc())
    if not is_master(user):
        # Não-master vê só origens da própria empresa (ou globais sem empresa)
        if user.empresa_id is None:
            return []
        q = q.where(
            (FirmwareOrigem.empresa_id == user.empresa_id) |
            (FirmwareOrigem.empresa_id.is_(None))
        )
    rows = (await db.execute(q)).scalars().all()
    return [_to_origem_out(o) for o in rows]


@router.post(
    "/firmware-origens", response_model=FirmwareOrigemCredencial,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))],
)
async def criar_origem(
    data: FirmwareOrigemCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cria origem + gera credencial FTP. Senha mostrada UMA vez no retorno."""
    # Valida empresa_id conforme role do user
    if is_master(user):
        empresa_id = data.empresa_id  # pode ser None (global) ou específica
        if empresa_id is not None:
            ok = (await db.execute(
                select(Empresa.id).where(Empresa.id == empresa_id)
            )).scalar_one_or_none()
            if not ok:
                raise HTTPException(status_code=404, detail="Empresa não encontrada")
    else:
        # admin_empresa: força empresa do próprio user (ignora data.empresa_id)
        if user.empresa_id is None:
            raise HTTPException(status_code=403, detail="Sem empresa vinculada")
        empresa_id = user.empresa_id

    senha = _gerar_senha_origem()
    origem = FirmwareOrigem(
        nome=data.nome.strip(),
        descricao=(data.descricao or "").strip() or None,
        # Placeholder ÚNICO (token aleatório) só até o flush popular o ID — evita
        # colisão na constraint unique se 2 origens forem criadas em paralelo.
        # Logo abaixo vira o valor final fwm_NNNNN baseado no id.
        usuario_ftp=f"__pend_{secrets.token_hex(8)}__",
        senha_ftp_enc=encrypt(senha),
        ativo=True,
        empresa_id=empresa_id,
        criado_por_id=user.id,
        criado_por_nome=user.nome,
    )
    db.add(origem)
    await db.flush()  # popula origem.id
    origem.usuario_ftp = _gerar_user_origem(origem.id)
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_origem_criada, user=user,
        request=request, empresa_id=empresa_id,
        alvo_tipo="firmware_origem", alvo_nome=origem.nome,
        detalhe=f"usuario={origem.usuario_ftp}",
        commit=False,
    )
    await db.commit()
    await db.refresh(origem)
    out = FirmwareOrigemCredencial.model_validate({
        **FirmwareOrigemOut.model_validate(origem).model_dump(),
        "senha_ftp": senha,
    })
    return out


@router.patch(
    "/firmware-origens/{origem_id}", response_model=FirmwareOrigemOut,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))],
)
async def atualizar_origem(
    origem_id: int,
    data: FirmwareOrigemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    origem = (await db.execute(
        select(FirmwareOrigem).where(FirmwareOrigem.id == origem_id)
    )).scalar_one_or_none()
    if not origem:
        raise HTTPException(status_code=404, detail="Origem não encontrada")
    # admin_empresa só pode tocar origens da própria empresa
    if not is_master(user) and origem.empresa_id != user.empresa_id:
        raise HTTPException(status_code=403, detail="Sem acesso a esta origem")
    if data.nome is not None:
        origem.nome = data.nome.strip()
    if data.descricao is not None:
        origem.descricao = data.descricao.strip() or None
    if data.ativo is not None:
        origem.ativo = data.ativo
    await db.commit()
    await db.refresh(origem)
    return _to_origem_out(origem)


@router.post(
    "/firmware-origens/{origem_id}/regen-senha", response_model=FirmwareOrigemCredencial,
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))],
)
async def regenerar_senha(
    origem_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    origem = (await db.execute(
        select(FirmwareOrigem).where(FirmwareOrigem.id == origem_id)
    )).scalar_one_or_none()
    if not origem:
        raise HTTPException(status_code=404, detail="Origem não encontrada")
    if not is_master(user) and origem.empresa_id != user.empresa_id:
        raise HTTPException(status_code=403, detail="Sem acesso a esta origem")
    senha = _gerar_senha_origem()
    origem.senha_ftp_enc = encrypt(senha)
    await db.commit()
    await db.refresh(origem)
    out = FirmwareOrigemCredencial.model_validate({
        **FirmwareOrigemOut.model_validate(origem).model_dump(),
        "senha_ftp": senha,
    })
    return out


@router.delete(
    "/firmware-origens/{origem_id}",
    dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))],
)
async def deletar_origem(
    origem_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    origem = (await db.execute(
        select(FirmwareOrigem).where(FirmwareOrigem.id == origem_id)
    )).scalar_one_or_none()
    if not origem:
        raise HTTPException(status_code=404, detail="Origem não encontrada")
    if not is_master(user) and origem.empresa_id != user.empresa_id:
        raise HTTPException(status_code=403, detail="Sem acesso a esta origem")
    # Captura tudo ANTES do delete — após db.delete() o objeto entra em estado
    # transiente e acessar atributos fica imprevisível.
    nome = origem.nome
    user_ftp = origem.usuario_ftp
    empresa_id = origem.empresa_id
    await db.delete(origem)
    await audit.registrar(
        db, tipo=TipoAtividade.firmware_origem_removida, user=user,
        request=request, empresa_id=empresa_id,
        alvo_tipo="firmware_origem", alvo_nome=nome,
        detalhe=f"usuario={user_ftp}",
        commit=False,
    )
    await db.commit()
    return {"ok": True}
