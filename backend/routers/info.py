"""Informação pública da instância (servidor) — exposta pra UI dinamizar
exemplos de comandos por fabricante (NTP client, FTP push, etc.).

Multi-instância: cada nexus-backup tem seu próprio IP/FQDN. Antes (até v1.2.0)
o frontend tinha o IP do servidor hardcoded, então uma instância nova
mostrava o IP da instância antiga nos exemplos. Endpoint dinamiza isso.
"""
from fastapi import APIRouter, Depends
from auth import get_current_user
from models import User
from config import settings

router = APIRouter(prefix="/api/info", tags=["info"])


@router.get("/server")
async def get_server_info(_user: User = Depends(get_current_user)):
    """Retorna os endpoints públicos desta instância — usado pra UI substituir
    o placeholder `<SERVIDOR>` nos exemplos de comando por fabricante.

    `ftp_endpoint`: IP que clientes usam pra alcançar os servers de push
    (FTP/SFTP/TFTP). Vem do .env (`FTP_MASQUERADE_ADDRESS`). Vazio = .env
    não foi configurado — UI cai num placeholder visível.
    """
    return {
        "ftp_endpoint": settings.FTP_MASQUERADE_ADDRESS or "",
    }
