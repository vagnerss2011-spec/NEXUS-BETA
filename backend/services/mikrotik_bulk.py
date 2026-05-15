"""Ações em massa para devices Mikrotik (módulo Operações).

Cada função aqui executa UMA ação contra UM device via API binária
(librouteros). O paralelismo é orquestrado pelo router — esse módulo só
expõe primitivas síncronas (executadas via `asyncio.to_thread`).

Why catálogo curado e não comando livre genérico:
- Comando livre via API é janela pra acidente (digitar /system reset-configuration
  num device de produção). O catálogo cobre 95% dos casos de manutenção em massa
  com confirmações específicas no frontend.
- Comando livre EXISTE como ação separada (`comando_livre`) mas é restrita a
  admin master e bloqueia regex de comandos perigosos antes de mandar.

Why síncrono + asyncio.to_thread:
- librouteros usa sockets bloqueantes. Wrap em thread evita travar o loop
  do FastAPI e permite paralelismo real (não cooperativo).

Toda função retorna (status, output_texto) onde status ∈ {"sucesso", "falha"}.
Caller (router) decide o que faz com o output (joga em resultados[device_id]).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Tuple

from models import Device, DeviceVendor
from services.mikrotik_api import _connect

log = logging.getLogger(__name__)

# Comandos bloqueados no comando livre — protege contra acidente catastrófico.
# Match case-insensitive em qualquer parte do path (não exige começar com /).
# Lista pequena de propósito: é checagem de "última linha de defesa", não
# whitelist. Quem realmente quer rodar isso usa Winbox.
_COMANDO_LIVRE_BLOQUEADOS = re.compile(
    r"(reset-configuration|factory-reset|system[/ ]+shutdown|system[/ ]+reboot|"
    r"file[/ ]+remove|user[/ ]+remove|interface[/ ]+remove|ip[/ ]+address[/ ]+remove)",
    re.IGNORECASE,
)


def _formatar_uptime(uptime_str: str) -> str:
    """Mikrotik retorna uptime tipo '1w2d3h4m5s'. Devolve igual — formato já
    é legível. Mantemos a função pra futuras conversões."""
    return uptime_str or "n/d"


# ============================================================
# CHECAGENS (read-only)
# ============================================================

def checar_versao(device: Device, params: dict | None = None) -> Tuple[str, str]:
    """Lê /system/resource — retorna versão RouterOS, board e arquitetura."""
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        rows = list(api("/system/resource/print"))
        if not rows:
            return "falha", "Comando /system/resource não retornou dados."
        r = rows[0]
        versao = r.get("version", "n/d")
        board = r.get("board-name", "n/d")
        arch = r.get("architecture-name", "n/d")
        return "sucesso", f"version={versao} · board={board} · arch={arch}"
    except Exception as e:
        return "falha", f"Erro lendo /system/resource: {e}"
    finally:
        try: api.close()
        except Exception: pass


def listar_usuarios(device: Device, params: dict | None = None) -> Tuple[str, str]:
    """Lista usuários ativos do device (nome + group + last-logged-in)."""
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        rows = list(api("/user/print"))
        if not rows:
            return "sucesso", "(nenhum usuário cadastrado)"
        linhas = []
        for r in rows:
            nome = r.get("name", "?")
            grupo = r.get("group", "?")
            last = r.get("last-logged-in", "nunca")
            disabled = r.get("disabled", "false") == "true"
            flag = " [DISABLED]" if disabled else ""
            linhas.append(f"  - {nome} ({grupo}) · last={last}{flag}")
        return "sucesso", f"{len(rows)} usuários:\n" + "\n".join(linhas)
    except Exception as e:
        return "falha", f"Erro lendo /user: {e}"
    finally:
        try: api.close()
        except Exception: pass


def inventario_rapido(device: Device, params: dict | None = None) -> Tuple[str, str]:
    """Snapshot de saúde: modelo + uptime + espaço livre no /file + RouterOS."""
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        rows = list(api("/system/resource/print"))
        if not rows:
            return "falha", "Comando /system/resource não retornou dados."
        r = rows[0]
        versao = r.get("version", "n/d")
        board = r.get("board-name", "n/d")
        uptime = _formatar_uptime(r.get("uptime", ""))
        # free-hdd-space vem em bytes
        try:
            free_b = int(r.get("free-hdd-space", "0"))
            total_b = int(r.get("total-hdd-space", "0"))
            free_mb = free_b / (1024 * 1024)
            pct = (free_b / total_b * 100) if total_b else 0
            disco = f"{free_mb:.1f} MB livres ({pct:.0f}% de {total_b/(1024*1024):.0f} MB)"
        except Exception:
            disco = "n/d"
        return "sucesso", (
            f"board={board} · version={versao} · uptime={uptime} · /file: {disco}"
        )
    except Exception as e:
        return "falha", f"Erro lendo inventário: {e}"
    finally:
        try: api.close()
        except Exception: pass


# ============================================================
# CONFIGURAÇÕES (write)
# ============================================================

def adicionar_usuario(device: Device, params: dict) -> Tuple[str, str]:
    """Cria usuário novo no Mikrotik. Exige params: username, password, group.

    Idempotente: se usuário já existe com mesmo nome, retorna sucesso sem erro
    (RouterOS dispara TrapError 'already have user', interpretamos como OK).
    """
    username = (params or {}).get("username", "").strip()
    password = (params or {}).get("password", "")
    group = (params or {}).get("group", "read").strip()
    if not username or not password:
        return "falha", "params.username e params.password são obrigatórios."
    if len(password) < 8:
        return "falha", "Senha precisa ter no mínimo 8 caracteres."
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        try:
            list(api("/user/add", name=username, password=password, group=group))
            return "sucesso", f"usuário '{username}' criado no grupo '{group}'"
        except Exception as e:
            msg = str(e).lower()
            if "already have user" in msg or "duplicate" in msg:
                return "sucesso", f"usuário '{username}' já existe (sem mudança)"
            return "falha", f"Erro criando usuário: {e}"
    finally:
        try: api.close()
        except Exception: pass


def remover_usuario(device: Device, params: dict) -> Tuple[str, str]:
    """Remove usuário do Mikrotik pelo nome. Exige params.username.

    Proteção: se o usuário a remover é o MESMO usado pra conectar via API
    (device.usuario_ssh), aborta — senão a próxima execução perde acesso.
    """
    username = (params or {}).get("username", "").strip()
    if not username:
        return "falha", "params.username é obrigatório."
    if device.usuario_ssh and username.lower() == device.usuario_ssh.lower():
        return "falha", (
            f"Bloqueado: '{username}' é o usuário usado pelo NEXUS pra "
            "conectar via API. Remover deixaria o device órfão."
        )
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        rows = list(api("/user/print"))
        target = next((r for r in rows if r.get("name") == username), None)
        if not target:
            return "sucesso", f"usuário '{username}' já não existia (nada a remover)"
        file_id = target.get(".id")
        if not file_id:
            return "falha", f"linha do usuário '{username}' sem .id (firmware estranho)"
        list(api("/user/remove", **{".id": file_id}))
        return "sucesso", f"usuário '{username}' removido"
    except Exception as e:
        return "falha", f"Erro removendo usuário: {e}"
    finally:
        try: api.close()
        except Exception: pass


def configurar_snmp(device: Device, params: dict) -> Tuple[str, str]:
    """Configura SNMP. Exige params: community, contact (opcional),
    location (opcional), trap_target (opcional)."""
    community = (params or {}).get("community", "").strip()
    if not community:
        return "falha", "params.community é obrigatório."
    contact = (params or {}).get("contact", "").strip()
    location = (params or {}).get("location", "").strip()
    trap_target = (params or {}).get("trap_target", "").strip()
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        # 1) Habilita SNMP geral + define contact/location se vieram
        snmp_args: dict = {"enabled": "yes"}
        if contact:
            snmp_args["contact"] = contact
        if location:
            snmp_args["location"] = location
        if trap_target:
            # set traps no SNMP geral. RouterOS aceita trap-target / trap-community.
            snmp_args["trap-target"] = trap_target
            snmp_args["trap-community"] = community
            snmp_args["trap-version"] = "2"
        list(api("/snmp/set", **snmp_args))

        # 2) Configura community default (idempotente — RouterOS sempre tem
        # community 'public' por default; renomeamos pra a configurada).
        com_rows = list(api("/snmp/community/print"))
        default = next((c for c in com_rows if c.get("default", "false") == "true"), None)
        if default and ".id" in default:
            list(api(
                "/snmp/community/set",
                **{".id": default[".id"], "name": community, "read-access": "yes"}
            ))
        else:
            # Sem community default — cria uma nova.
            list(api("/snmp/community/add", name=community, **{"read-access": "yes"}))
        return "sucesso", (
            f"SNMP habilitado · community='{community}'"
            + (f" · trap={trap_target}" if trap_target else "")
            + (f" · contact='{contact}'" if contact else "")
        )
    except Exception as e:
        return "falha", f"Erro configurando SNMP: {e}"
    finally:
        try: api.close()
        except Exception: pass


def cleanup_orfaos(device: Device, params: dict | None = None) -> Tuple[str, str]:
    """Remove todos os arquivos `nexus-api-*.rsc` do /file do Mikrotik.

    Reusa a função `_cleanup_orfaos_nexus` do mikrotik_api.py — fonte única
    da verdade do que conta como "órfão NEXUS".
    """
    from services.mikrotik_api import _cleanup_orfaos_nexus
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        removidos = _cleanup_orfaos_nexus(api)
        if removidos == 0:
            return "sucesso", "nenhum arquivo nexus-api-*.rsc encontrado"
        return "sucesso", f"{removidos} arquivo(s) nexus-api-*.rsc removido(s)"
    except Exception as e:
        return "falha", f"Erro no cleanup: {e}"
    finally:
        try: api.close()
        except Exception: pass


# ============================================================
# COMANDO LIVRE (admin only)
# ============================================================

def comando_livre(device: Device, params: dict) -> Tuple[str, str]:
    """Executa comando arbitrário via API. Exige params.comando.

    O comando deve vir no formato librouteros: caminho começando com '/'
    seguido de args nomeados via params.args (dict). Ex.:
        params = {"comando": "/system/identity/print"}
        params = {"comando": "/ip/address/print", "args": {"detail": ""}}

    Bloqueia regex de comandos destrutivos (reset, reboot, remove). Pra
    rodar algo bloqueado, admin precisa abrir Winbox/SSH.
    """
    comando = (params or {}).get("comando", "").strip()
    args = (params or {}).get("args", {}) or {}
    if not comando:
        return "falha", "params.comando é obrigatório."
    if not comando.startswith("/"):
        return "falha", "Comando precisa começar com '/' (ex.: /system/identity/print)."
    if _COMANDO_LIVRE_BLOQUEADOS.search(comando):
        return "falha", (
            f"Comando bloqueado: '{comando}' contém padrão destrutivo. "
            "Use Winbox/SSH direto se realmente precisar."
        )
    try:
        api = _connect(device)
    except Exception as e:
        return "falha", f"Conexão falhou: {e}"
    try:
        rows = list(api(comando, **{str(k): str(v) for k, v in args.items()}))
        if not rows:
            return "sucesso", "(comando executado, sem retorno)"
        # Formata cada reply como key=val separados por espaço.
        linhas = []
        for r in rows[:50]:  # cap pra evitar output gigante
            linhas.append(" ".join(f"{k}={v}" for k, v in r.items() if not k.startswith(".")))
        suffix = f"\n... (+{len(rows)-50} linhas truncadas)" if len(rows) > 50 else ""
        return "sucesso", "\n".join(linhas) + suffix
    except Exception as e:
        return "falha", f"Erro executando comando: {e}"
    finally:
        try: api.close()
        except Exception: pass


# ============================================================
# Registry
# ============================================================

# Mapa: slug da ação → (função, exige_admin_master, sanitizar_params_func)
# - exige_admin_master: comando livre só pra master; resto admin+operador.
# - sanitizar_params: pra não gravar senha em texto no OperacaoMassaLog.params.
def _sanitizar_password(p: dict) -> dict:
    """Substitui campo 'password' por '***' antes de gravar em log."""
    if not p:
        return p
    out = dict(p)
    if "password" in out and out["password"]:
        out["password"] = "***"
    return out


ACOES = {
    # read-only
    "checar_versao":    (checar_versao,    False, None),
    "listar_usuarios":  (listar_usuarios,  False, None),
    "inventario":       (inventario_rapido,False, None),
    # write
    "adicionar_user":   (adicionar_usuario, False, _sanitizar_password),
    "remover_user":     (remover_usuario,   False, None),
    "configurar_snmp":  (configurar_snmp,   False, None),
    "cleanup_orfaos":   (cleanup_orfaos,    False, None),
    # comando livre (master only)
    "comando_livre":    (comando_livre,     True,  None),
}


def is_mikrotik(device: Device) -> bool:
    """Filtro pra aceitar só devices Mikrotik (v6 ou v7) com protocolo API.
    Comando livre/etc. via API exige protocolo='api' configurado no device."""
    return device.fabricante in (DeviceVendor.mikrotik, DeviceVendor.mikrotik_v7)
