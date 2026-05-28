"""Checagem de versão — compara APP_VERSION com releases no GitHub.

Cache em memória com TTL ~1h. Sem cache, cada page load = 1 req à API GitHub
(rate limit 60/h sem auth, 5000/h com PAT).

Falha-tolerante: API GitHub indisponível, token errado, repo errado, etc. =
'sem dados' (latest=None, update_available=False), nunca propaga exceção.

Canais (v2.3.0+):
- **LTS**  = última release no GitHub marcada como "Latest" (campo prerelease=false).
            Você promove uma versão a LTS marcando-a como "Latest" no GitHub Releases.
- **Edge** = última release overall, incluindo as marcadas como Pre-release.
            Cada instância escolhe seu canal (Configuracao.update_channel).

Fallback: se o repo ainda não tem GitHub Releases criados (só tags), cai pro
endpoint /tags como nas versões antigas — nesse caso ambos canais apontam
pra mesma tag mais nova (sem distinção até o admin começar a criar releases).

Auth GitHub (v2.5.1+): o token é **opcional**. Se o repo for público (caso atual),
funciona anônimo com rate limit 60 req/h por IP — folga gigante porque o cache
é 1h por canal (~24 req/dia por instância). Se um dia o repo voltar a ser
privado, adiciona `GITHUB_TOKEN` no `.env` que o backend volta a usar.
"""
from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import httpx
from config import settings
from version import APP_VERSION

log = logging.getLogger("nexus.version")

CACHE_TTL_SECONDS = 3600  # 1h


@dataclass
class ReleaseInfo:
    """Representa um GitHub Release (ou uma tag, no fallback)."""
    tag: str                                  # 'v2.2.4'
    version: str                              # '2.2.4' (sem o 'v')
    name: Optional[str] = None                # title do release
    published_at: Optional[str] = None        # ISO string
    body: Optional[str] = None                # markdown do release notes
    prerelease: bool = False
    is_lts: bool = False                      # alias semântico: not prerelease
    url: Optional[str] = None                 # link humano pro release


@dataclass
class VersionCheckResult:
    current: str
    channel: str                              # 'lts' | 'edge'
    update_available: bool
    checked_at: float
    current_release: Optional[ReleaseInfo] = None
    current_dias_em_producao: Optional[int] = None
    latest_lts: Optional[ReleaseInfo] = None
    latest_edge: Optional[ReleaseInfo] = None
    target: Optional[ReleaseInfo] = None      # latest_lts ou latest_edge conforme canal
    # Campos legacy mantidos pro UpdateBanner antigo não quebrar enquanto não
    # for atualizado. Espelham target.tag e target.body.
    latest: Optional[str] = None
    changelog_summary: Optional[str] = None


# Cache global em memória, indexado por canal — instâncias diferentes do mesmo
# servidor (improvável mas teoricamente possível) não vazam entre canais.
_cache: dict[str, VersionCheckResult] = {}


# ───────────────────────── Helpers de versão ─────────────────────────

def _semver_tuple(v: str) -> tuple[int, int, int]:
    """Converte 'v1.2.3' ou '1.2.3' em (1, 2, 3) pra comparação numérica.
    Strings inválidas viram (0,0,0) — qualquer release válida é "maior"."""
    s = v.lstrip("v").split("-")[0]  # ignora pré-release '-rc1' etc.
    parts = s.split(".")
    try:
        a = int(parts[0]) if len(parts) > 0 else 0
        b = int(parts[1]) if len(parts) > 1 else 0
        c = int(parts[2]) if len(parts) > 2 else 0
        return (a, b, c)
    except ValueError:
        return (0, 0, 0)


def _is_newer(latest: str, current: str) -> bool:
    return _semver_tuple(latest) > _semver_tuple(current)


def _strip_v(tag: str) -> str:
    return tag.lstrip("v") if tag else tag


# ───────────────────────── HTTP wrappers GitHub ─────────────────────────

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    """Auth opcional — repo público funciona sem Bearer (rate limit 60/h por IP,
    suficiente porque o cache TTL é 1h). Token apenas eleva o limite pra 5000/h."""
    h = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"nexus-backup/{APP_VERSION}",
    }
    if settings.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    return h


async def _fetch_releases() -> Optional[list[dict]]:
    """Retorna até 30 releases mais recentes (ordem: criação desc), ou None
    se falhar. Lista pode estar vazia (repo sem releases — comum pra projetos
    que só usaram tags até agora). Token opcional (vide _gh_headers)."""
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/releases?per_page=30"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_gh_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("GitHub /releases falhou: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("erro inesperado em _fetch_releases: %s", e)
        return None


async def _fetch_tags() -> Optional[list[dict]]:
    """Fallback: lista de tags (sem metadado de release). Usado quando o repo
    ainda não tem GitHub Releases criados — instância ainda funciona, mas
    sem distinção de canal. Token opcional."""
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/tags?per_page=30"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_gh_headers())
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("GitHub /tags falhou: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("erro inesperado em _fetch_tags: %s", e)
        return None


# ───────────────────────── Conversão ─────────────────────────

def _release_to_info(rel: dict) -> Optional[ReleaseInfo]:
    """Converte 1 release do GitHub em ReleaseInfo. Filtra releases sem tag
    válida (formato vX.Y.Z) — releases manuais 'rascunho' sem tag são ignoradas."""
    tag = rel.get("tag_name") or ""
    if not re.match(r"^v?\d+\.\d+\.\d+$", tag):
        return None
    prerelease = bool(rel.get("prerelease"))
    return ReleaseInfo(
        tag=tag if tag.startswith("v") else f"v{tag}",
        version=_strip_v(tag),
        name=rel.get("name") or None,
        published_at=rel.get("published_at") or None,
        body=rel.get("body") or None,
        prerelease=prerelease,
        is_lts=not prerelease,
        url=rel.get("html_url") or None,
    )


def _tag_to_info(t: dict) -> Optional[ReleaseInfo]:
    """Fallback quando só há tags (sem releases): nome da tag, sem body/data."""
    name = t.get("name") or ""
    if not re.match(r"^v?\d+\.\d+\.\d+$", name):
        return None
    return ReleaseInfo(
        tag=name if name.startswith("v") else f"v{name}",
        version=_strip_v(name),
        is_lts=True,    # sem distinção possível — assume LTS pra não esconder em modo conservador
    )


# ───────────────────────── Lógica principal ─────────────────────────

def _dias_desde(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except Exception:
        return None


def _achar_release(infos: list[ReleaseInfo], version_str: str) -> Optional[ReleaseInfo]:
    """Procura na lista a release que casa com a versão (compara sem 'v')."""
    target = _strip_v(version_str)
    for info in infos:
        if info.version == target:
            return info
    return None


async def _build_result_from_releases(channel: str, releases: list[dict]) -> VersionCheckResult:
    """Cenário ideal: repo tem GitHub Releases. Distingue LTS/Edge."""
    infos = [info for r in releases if (info := _release_to_info(r))]

    latest_lts = next((i for i in infos if i.is_lts), None)
    latest_edge = infos[0] if infos else None

    current_release = _achar_release(infos, APP_VERSION)
    dias = _dias_desde(current_release.published_at) if current_release else None

    target = latest_lts if channel == "lts" else latest_edge
    update_available = bool(target and _is_newer(target.version, APP_VERSION))

    return VersionCheckResult(
        current=APP_VERSION,
        channel=channel,
        update_available=update_available,
        checked_at=time.time(),
        current_release=current_release,
        current_dias_em_producao=dias,
        latest_lts=latest_lts,
        latest_edge=latest_edge,
        target=target,
        latest=target.tag if target else None,
        changelog_summary=target.body if target else None,
    )


async def _build_result_from_tags(channel: str, tags: list[dict]) -> VersionCheckResult:
    """Fallback quando o repo ainda não tem Releases — só tags. Ambos canais
    apontam pra mesma tag mais recente (sem distinção). Frontend deve mostrar
    aviso 'crie GitHub Releases pra ativar canais'."""
    infos = [info for t in tags if (info := _tag_to_info(t))]
    target = infos[0] if infos else None
    update_available = bool(target and _is_newer(target.version, APP_VERSION))
    return VersionCheckResult(
        current=APP_VERSION,
        channel=channel,
        update_available=update_available,
        checked_at=time.time(),
        latest_lts=target,
        latest_edge=target,
        target=target,
        latest=target.tag if target else None,
        changelog_summary=None,
    )


def _empty(channel: str) -> VersionCheckResult:
    """Resultado vazio (sem token ou API fora). Frontend trata como 'sem banner'."""
    return VersionCheckResult(
        current=APP_VERSION,
        channel=channel,
        update_available=False,
        checked_at=time.time(),
    )


async def check_version(channel: str = "lts", force: bool = False) -> VersionCheckResult:
    """Resultado da última checagem (cache 1h por canal) ou refresh se forçado."""
    if channel not in ("lts", "edge"):
        channel = "lts"
    cached = _cache.get(channel)
    now = time.time()
    if cached and not force and (now - cached.checked_at) < CACHE_TTL_SECONDS:
        return cached

    releases = await _fetch_releases()
    if releases is None:                       # falha de auth/rede
        result = _empty(channel)
    elif releases:                             # repo tem releases — caminho normal
        result = await _build_result_from_releases(channel, releases)
    else:                                      # repo SEM releases — fallback tags
        tags = await _fetch_tags() or []
        result = await _build_result_from_tags(channel, tags)

    _cache[channel] = result
    return result


def to_dict(r: VersionCheckResult) -> dict:
    """Serializer pra JSON response."""
    return asdict(r)
