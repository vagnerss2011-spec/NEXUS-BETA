from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from database import get_db
from models import Device, Empresa, User, UserRole, Backup, TipoAtividade, AuthMethod, Protocolo, DeviceTipo
from auth import require_role, get_current_user, is_master, ensure_empresa_access
from schemas import DeviceCreate, DeviceUpdate, DeviceOut, FTPCredentialOut
from services.crypto import encrypt
from services.ssh_service import load_pkey
from services import audit
from paramiko.ssh_exception import SSHException
from ipaddress import ip_network
from typing import List, Optional
import secrets
import string

router = APIRouter(prefix="/api/devices", tags=["devices"])

async def _empresa_exists(db: AsyncSession, empresa_id: int) -> bool:
    r = await db.execute(select(Empresa.id).where(Empresa.id == empresa_id))
    return r.scalar_one_or_none() is not None

async def _ultimos_backups_map(db: AsyncSession, device_ids: list[int]) -> dict[int, tuple[str, object]]:
    """Retorna {device_id: (status, criado_em)} do backup mais recente de cada device.
    Usa DISTINCT ON do Postgres (1 só query)."""
    if not device_ids:
        return {}
    rows = (await db.execute(
        select(Backup.device_id, Backup.status, Backup.criado_em)
        .where(Backup.device_id.in_(device_ids))
        .order_by(Backup.device_id, Backup.criado_em.desc())
        .distinct(Backup.device_id)
    )).all()
    return {r.device_id: (r.status, r.criado_em) for r in rows}

def _to_out(d: Device, ultimos: dict[int, tuple[str, object]]) -> DeviceOut:
    out = DeviceOut.model_validate(d)
    s, dt = ultimos.get(d.id, (None, None))
    out.ultimo_backup_status = s
    out.ultimo_backup_em = dt
    return out

@router.get("/", response_model=List[DeviceOut])
async def listar_devices(
    empresa_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Device).order_by(Device.nome)
    if is_master(user):
        if empresa_id is not None:
            q = q.where(Device.empresa_id == empresa_id)
    else:
        if user.empresa_id is None:
            return []
        q = q.where(Device.empresa_id == user.empresa_id)
    devices = (await db.execute(q)).scalars().all()
    ultimos = await _ultimos_backups_map(db, [d.id for d in devices])
    return [_to_out(d, ultimos) for d in devices]

def _validar_chave_ou_400(pem: str, passphrase: Optional[str]) -> None:
    """Tenta carregar a chave; rejeita o request com mensagem clara se não bater."""
    try:
        load_pkey(pem, passphrase)
    except SSHException as e:
        raise HTTPException(status_code=400, detail=f"Chave SSH inválida: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Chave SSH inválida: {e}")


def _validar_cidr_ou_400(cidr: str, exigir_host: bool = False) -> None:
    """Valida que o CIDR é IPv4 válido. Se exigir_host=True, força /32
    (necessário pra TFTP, que sem auth identifica device pelo IP origem
    — /24 daria ambiguidade entre vários devices)."""
    try:
        net = ip_network(cidr.strip(), strict=False)
        if net.version != 4:
            raise HTTPException(status_code=400, detail="ftp_origem_cidr precisa ser IPv4")
        if exigir_host and net.prefixlen != 32:
            raise HTTPException(
                status_code=400,
                detail="Para TFTP o CIDR precisa ser /32 (1 IP exato). TFTP não tem autenticação — o IP é a única identificação do device.",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"ftp_origem_cidr inválido: {e}")


def _gerar_ftp_user(device_id: int, tipo: DeviceTipo | None = None) -> str:
    """Username FTP/SFTP do device.

    UNM2000 (Fiberhome NMS): SEM caracteres especiais — o EMS Set Backup
    Server e o XFTP Server Setting rejeitam '_', '-' e similares no campo
    Username. Usamos só letras+dígitos: 'ftp00012'.

    Demais devices (OLT/switch/roteador): formato histórico 'ftp_00012'
    com underscore — clientes FTP genéricos aceitam normalmente.
    """
    if tipo == DeviceTipo.unm2000:
        return f"ftp{device_id:05d}"
    return f"ftp_{device_id:05d}"


def _ftp_senha_len_para_device(tipo: DeviceTipo | None, fabricante=None) -> int:
    """Tamanho da senha FTP gerada conforme tipo + fabricante.

    Fiberhome tem campos de senha curtos em vários pontos:
      - UNM2000 EMS Set Backup Server     -> ~20 chars
      - UNM2000 XFTP Server Setting       -> ~20 chars
      - OLT AN5516/AN6000 CLI ftp passwd  -> ~16 chars (limite firmware)

    Quando o UNM2000 dispara Configuration Export Task, ele transmite a senha
    para a OLT executar o upload. Senha maior que o limite da OLT é truncada
    silenciosamente -> auth fail no nosso servidor com 'senha_invalida'.

    Lógica:
      - tipo=unm2000              -> 20 chars (limite EMS)
      - fabricante=fiberhome      -> 16 chars (limite OLT AN5516)
      - default                   -> 32 chars
    """
    if tipo == DeviceTipo.unm2000:
        return 20
    # fabricante pode chegar como enum DeviceVendor ou string ('fiberhome')
    fab_val = getattr(fabricante, "value", fabricante)
    if fab_val == "fiberhome":
        return 16
    return 32


# Wrapper de compat — código existente que ainda chama _ftp_senha_len_para_tipo
# continua funcionando (retorna o tamanho considerando só o tipo, sem fabricante).
def _ftp_senha_len_para_tipo(tipo: DeviceTipo | None) -> int:
    return _ftp_senha_len_para_device(tipo, fabricante=None)


def _gerar_ftp_senha(comprimento: int = 32) -> str:
    """N chars de [a-zA-Z0-9] (default 32). Sem caracteres especiais — o EMS
    do UNM2000 rejeita '_', '-', '/' e outros símbolos no campo Password,
    e clientes FTP de equipamentos diversos têm comportamentos imprevisíveis
    com chars de escape. Alfanumérico puro funciona em 100% dos cenários.
    Use _ftp_senha_len_para_tipo() pra escolher o comprimento (UNM2000=20)."""
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(comprimento))


@router.post("/", response_model=DeviceOut,
             dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))])
async def criar_device(
    data: DeviceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    empresa_id = ensure_empresa_access(user, data.empresa_id)
    if not await _empresa_exists(db, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")

    senha_enc = None
    chave_enc = None
    passphrase_enc = None
    ftp_origem_cidr = None
    PUSH_PROTOCOLS = (Protocolo.ftp_push, Protocolo.sftp_push, Protocolo.tftp_push)
    is_push = data.protocolo in PUSH_PROTOCOLS
    # FTP/SFTP geram credencial (user+senha); TFTP não tem auth.
    gera_credencial = data.protocolo in (Protocolo.ftp_push, Protocolo.sftp_push)
    is_tftp = data.protocolo == Protocolo.tftp_push

    # UNM2000 é receptor passivo (EMS Set Backup Server) — só FTP/SFTP push.
    # Bloqueia SSH/Telnet (não faz sentido polar um NMS) e TFTP (EMS não usa).
    if data.tipo == DeviceTipo.unm2000 and data.protocolo not in (Protocolo.ftp_push, Protocolo.sftp_push):
        raise HTTPException(
            status_code=400,
            detail="UNM2000 só aceita protocolo FTP push ou SFTP push (configurado no EMS Set Backup Server)",
        )

    if is_push:
        if not data.ftp_origem_cidr:
            raise HTTPException(status_code=400, detail="ftp_origem_cidr é obrigatório para push (FTP/SFTP/TFTP)")
        _validar_cidr_ou_400(data.ftp_origem_cidr, exigir_host=is_tftp)
        ftp_origem_cidr = data.ftp_origem_cidr.strip()
    else:
        # SSH/Telnet: valida combinação de método de autenticação x credenciais
        if not data.usuario_ssh:
            raise HTTPException(status_code=400, detail="usuario_ssh é obrigatório para SSH/Telnet")
        if data.auth_method == AuthMethod.ssh_key:
            if not data.chave_privada or not data.chave_privada.strip():
                raise HTTPException(status_code=400, detail="Chave privada é obrigatória quando o método é 'ssh_key'")
            _validar_chave_ou_400(data.chave_privada, data.chave_passphrase)
            chave_enc = encrypt(data.chave_privada)
            passphrase_enc = encrypt(data.chave_passphrase) if data.chave_passphrase else None
        else:
            if not data.senha_ssh:
                raise HTTPException(status_code=400, detail="Senha é obrigatória quando o método é 'password'")
            senha_enc = encrypt(data.senha_ssh)

    device = Device(
        nome=data.nome, ip=data.ip, porta=data.porta,
        fabricante=data.fabricante, tipo=data.tipo, protocolo=data.protocolo,
        usuario_ssh=data.usuario_ssh,
        auth_method=data.auth_method,
        senha_ssh_enc=senha_enc,
        chave_privada_enc=chave_enc,
        chave_passphrase_enc=passphrase_enc,
        ftp_origem_cidr=ftp_origem_cidr,
        empresa_id=empresa_id,
    )
    db.add(device)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Mensagem amigável quando uma constraint do banco é violada (ex.:
        # ftp_user UNIQUE, ou alguma column NOT NULL ainda não migrada).
        raise HTTPException(status_code=400, detail=f"Conflito ao salvar dispositivo: {e.orig}")
    await db.refresh(device)

    # FTP/SFTP: geram credencial após ter o ID. Senha em texto puro vai no
    # DeviceOut da resposta APENAS desta criação. TFTP não gera credencial
    # (protocolo é anonymous — id do device é o IP de origem).
    ftp_senha_plain: Optional[str] = None
    if gera_credencial:
        device.ftp_user = _gerar_ftp_user(device.id, device.tipo)
        ftp_senha_plain = _gerar_ftp_senha(_ftp_senha_len_para_device(device.tipo, device.fabricante))
        device.ftp_senha_enc = encrypt(ftp_senha_plain)
        await db.commit()
        await db.refresh(device)
    await audit.registrar(
        db, tipo=TipoAtividade.device_criado, user=user, request=request,
        empresa_id=empresa_id,
        alvo_tipo="device", alvo_nome=device.nome,
        detalhe=f"{device.tipo.value} · {device.fabricante.value} · {device.ip}",
    )
    out = DeviceOut.model_validate(device)
    if ftp_senha_plain:
        # Devolve a senha em texto puro UMA ÚNICA VEZ — frontend mostra com aviso
        # de "anote agora, não dá pra recuperar".
        out.ftp_senha = ftp_senha_plain
    return out

@router.put("/{device_id}", response_model=DeviceOut,
            dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))])
async def atualizar_device(
    device_id: int, data: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, device.empresa_id)

    update = data.model_dump(exclude_none=True)
    # Mudar de empresa: só admin master
    if "empresa_id" in update and update["empresa_id"] != device.empresa_id:
        if not is_master(user):
            raise HTTPException(status_code=403, detail="Apenas admin master pode mover dispositivos entre empresas")
        if not await _empresa_exists(db, update["empresa_id"]):
            raise HTTPException(status_code=404, detail="Empresa de destino não encontrada")
    else:
        update.pop("empresa_id", None)

    # Determina o método final (após esse update) e garante credencial coerente.
    novo_method = update.get("auth_method", device.auth_method)
    nova_chave = update.get("chave_privada")
    nova_chave_tem_valor = nova_chave is not None and nova_chave.strip() != ""

    if novo_method == AuthMethod.ssh_key:
        if nova_chave_tem_valor:
            _validar_chave_ou_400(nova_chave, update.get("chave_passphrase"))
            device.chave_privada_enc = encrypt(nova_chave)
        # Se está mudando pra ssh_key e não tem nem chave nova nem chave já salva, exige chave.
        if device.auth_method != AuthMethod.ssh_key and not device.chave_privada_enc:
            raise HTTPException(status_code=400, detail="Para mudar para chave SSH, envie a chave privada")
        # Atualiza/limpa passphrase se foi enviada no payload
        if "chave_passphrase" in update:
            pp = update.get("chave_passphrase")
            device.chave_passphrase_enc = encrypt(pp) if pp else None
    elif novo_method == AuthMethod.password:
        if "senha_ssh" in update and update.get("senha_ssh"):
            device.senha_ssh_enc = encrypt(update.get("senha_ssh"))
        if device.auth_method != AuthMethod.password and not device.senha_ssh_enc:
            raise HTTPException(status_code=400, detail="Para mudar para senha, envie a senha")

    # Push (FTP/SFTP/TFTP): valida CIDR se foi enviado
    if "ftp_origem_cidr" in update and update.get("ftp_origem_cidr"):
        protocolo_efetivo = update.get("protocolo") or device.protocolo
        _validar_cidr_ou_400(
            update["ftp_origem_cidr"],
            exigir_host=protocolo_efetivo == Protocolo.tftp_push,
        )

    # Os campos sensíveis já foram processados acima; remove pra não atribuir via setattr.
    for k in ("senha_ssh", "chave_privada", "chave_passphrase"):
        update.pop(k, None)
    for field, value in update.items():
        setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    ultimos = await _ultimos_backups_map(db, [device.id])
    return _to_out(device, ultimos)

@router.post("/{device_id}/ftp-credentials/regenerate", response_model=FTPCredentialOut,
             dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa, UserRole.operador))])
async def regenerar_credencial_ftp(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Regenera a senha FTP do device. user permanece o mesmo (ftp_<id>) para
    evitar que admins esqueçam de atualizar config no equipamento. Senha em
    texto puro retornada UMA ÚNICA VEZ."""
    device = (await db.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, device.empresa_id)
    if device.protocolo not in (Protocolo.ftp_push, Protocolo.sftp_push):
        raise HTTPException(status_code=400, detail="Esse dispositivo não usa FTP/SFTP push (TFTP não tem credencial)")

    # Caso 1 (sem ftp_user): cadastra agora.
    # Caso 2 (UNM2000 com user antigo 'ftp_xxxxx' c/ underscore): atualiza pro
    # formato sem underscore que o EMS aceita. Permite consertar devices
    # cadastrados antes do fix sem precisar deletar/recriar.
    user_correto = _gerar_ftp_user(device.id, device.tipo)
    if not device.ftp_user or device.ftp_user != user_correto:
        device.ftp_user = user_correto
    nova_senha = _gerar_ftp_senha(_ftp_senha_len_para_device(device.tipo, device.fabricante))
    device.ftp_senha_enc = encrypt(nova_senha)
    await db.commit()
    return FTPCredentialOut(
        ftp_user=device.ftp_user,
        ftp_senha=nova_senha,
        ftp_origem_cidr=device.ftp_origem_cidr,
    )


@router.delete("/{device_id}",
               dependencies=[Depends(require_role(UserRole.admin, UserRole.admin_empresa))])
async def deletar_device(
    device_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")
    ensure_empresa_access(user, device.empresa_id)
    nome = device.nome
    empresa_id = device.empresa_id
    tipo_dev = device.tipo.value
    await db.delete(device)
    await db.commit()
    await audit.registrar(
        db, tipo=TipoAtividade.device_removido, user=user, request=request,
        empresa_id=empresa_id,
        alvo_tipo="device", alvo_nome=nome,
        detalhe=tipo_dev,
    )
    return {"ok": True}
