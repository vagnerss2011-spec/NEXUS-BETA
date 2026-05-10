"""Checagem de versão — compara APP_VERSION com a tag mais recente no GitHub.

Cache em memória com TTL ~1h. Sem cache, cada page load = 1 req à API GitHub
(rate limit 60/h sem auth, 5000/h com PAT).

Falha-tolerante: API GitHub indisponível, token errado, repo errado, etc. =
'sem dados' (latest=None, update_available=False), nunca propaga exceção.

Repo privado: precisa GITHUB_TOKEN no .env (PAT com scope 'repo'). Sem token,
endpoint retorna current=APP_VERSION, latest=None — frontend não mostra banner.
"""
from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional
import httpx
from config import settings
from version import APP_VERSION

log = logging.getLogger("nexus.version")

CACHE_TTL_SECONDS = 3600  # 1h


@dataclass
class VersionCheckResult:
    current: str
    latest: Optional[str]
    update_available: bool
    changelog_summary: Optional[str]
    checked_at: float


# Cache global em memória. Reseta no restart do backend; recarrega na 1ª req.
_cache: Optional[VersionCheckResult] = None


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


def _slice_changelog(content: str, current: str, latest: str) -> Optional[str]:
    """Extrai do CHANGELOG.md os blocos de release entre `latest` (inclusive)
    e `current` (exclusivo) — ou seja, tudo o que o usuário ainda não tem.

    Formato esperado: '## [X.Y.Z] - YYYY-MM-DD' como heading de cada release.
    Se o regex não casar, retorna None (UI mostra só "vX.Y.Z disponível").
    """
    cur = current.lstrip("v")
    lat = latest.lstrip("v")
    # Pattern: a partir do bloco [latest] até (mas excluindo) [current].
    # Se [current] não estiver no CHANGELOG (instância em uma versão muito
    # antiga, pré-CHANGELOG), pega tudo até o fim do "[X.Y.Z]" mais antigo.
    pat = (
        rf"## \[{re.escape(lat)}\][^\n]*\n.*?"
        rf"(?=## \[{re.escape(cur)}\]|\[Não lançado\]|\Z)"
    )
    m = re.search(pat, content, re.DOTALL)
    if not m:
        return None
    text = m.group(0).strip()
    # Trunca defensivamente — banner no painel não precisa de KB de changelog.
    MAX_CHARS = 4000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n*(...truncado, ver CHANGELOG.md completo no GitHub)*"
    return text


async def _fetch_latest_tag() -> Optional[str]:
    """Retorna a tag estável mais recente (formato 'vX.Y.Z' — sem pré-release),
    ou None se falhar. Idiomatic: paginação 1ª página com 30 tags chega bem
    pro caso comum (instância raramente fica >30 versões atrás)."""
    if not settings.GITHUB_TOKEN:
        log.debug("GITHUB_TOKEN vazio — checagem desabilitada")
        return None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"nexus-backup/{APP_VERSION}",
    }
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/tags?per_page=30"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            tags = r.json()
            for t in tags:
                name = t.get("name", "")
                if re.match(r"^v\d+\.\d+\.\d+$", name):
                    return name
            log.warning("nenhuma tag estável vX.Y.Z encontrada nas %d tags", len(tags))
            return None
    except httpx.HTTPError as e:
        log.warning("GitHub /tags falhou: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("erro inesperado em _fetch_latest_tag: %s", e)
        return None


async def _fetch_changelog(ref: str) -> Optional[str]:
    """Baixa CHANGELOG.md raw do repo na ref dada (tag/branch/commit).
    Endpoint /contents com 'Accept: vnd.github.raw' devolve o file inteiro
    como text/plain — não precisa decodificar base64."""
    if not settings.GITHUB_TOKEN:
        return None
    headers = {
        "Accept": "application/vnd.github.raw",
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"nexus-backup/{APP_VERSION}",
    }
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/contents/CHANGELOG.md?ref={ref}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.text
    except httpx.HTTPError as e:
        log.warning("CHANGELOG fetch (ref=%s) falhou: %s", ref, e)
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("erro inesperado em _fetch_changelog: %s", e)
        return None


async def check_version(force: bool = False) -> VersionCheckResult:
    """Resultado da última checagem (cache 1h) ou refresh se forçado.

    Sempre retorna um VersionCheckResult — em caso de falha de rede/auth,
    `latest=None` e `update_available=False`. UI lida com isso ocultando
    o banner (sem dados = sem mensagem).
    """
    global _cache
    now = time.time()
    if _cache and not force and (now - _cache.checked_at) < CACHE_TTL_SECONDS:
        return _cache

    latest = await _fetch_latest_tag()
    if not latest:
        result = VersionCheckResult(
            current=APP_VERSION,
            latest=None,
            update_available=False,
            changelog_summary=None,
            checked_at=now,
        )
        _cache = result
        return result

    update_available = _is_newer(latest, APP_VERSION)
    changelog = None
    if update_available:
        # Baixa CHANGELOG na tag latest — assim contém todas as entradas
        # entre current e latest (inclusive).
        raw = await _fetch_changelog(latest)
        if raw:
            changelog = _slice_changelog(raw, APP_VERSION, latest)

    result = VersionCheckResult(
        current=APP_VERSION,
        latest=latest,
        update_available=update_available,
        changelog_summary=changelog,
        checked_at=now,
    )
    _cache = result
    return result


def to_dict(r: VersionCheckResult) -> dict:
    """Serializer pra JSON response (dataclass não é nativamente serializável
    pelo FastAPI sem `response_model`)."""
    return asdict(r)
