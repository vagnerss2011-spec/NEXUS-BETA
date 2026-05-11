"""Telemetria de carga do container backend.

Usado pelo scheduler diário (services/scheduler.py) pra decidir dinamicamente
quantos workers paralelos pode usar sem estourar CPU/RAM. Funciona como um
"Zabbix agent" embutido — sample contínuo de CPU/memória do processo,
exposto via média móvel curta (60s).

Por que média móvel e não valor instantâneo:
- CPU varia muito a cada amostra (10% → 70% → 20% em 3s é normal sob carga
  de Paramiko/Netmiko). Decisão de cortar workers baseada em pico
  instantâneo causaria oscilação descontrolada.
- 60s cobre bem o tempo típico de coleta (5-180s) sem ficar lerdo demais
  pra reagir a um pico real sustentado.

API mínima:
    health = HealthMonitor()
    health.start()           # warm-up do psutil.cpu_percent (1ª chamada = 0)
    health.sample()          # adiciona amostra ao histórico (chamar a cada 5s)
    health.cpu_media()       # média 60s
    health.mem_media()       # média 60s
    health.sob_stress(80, 80)  # True se cpu>80 OU mem>80
"""

from collections import deque
from typing import Optional

try:
    import psutil  # opcional — se não tiver, scheduler degrada graciosamente pra modo single-worker
    _PSUTIL_DISPONIVEL = True
except ImportError:
    psutil = None  # type: ignore
    _PSUTIL_DISPONIVEL = False


# 12 amostras × 5s = 60s de janela. Capacity fixa: deque rola sozinha.
_JANELA_AMOSTRAS = 12


class HealthMonitor:
    """Mantém histórico curto de CPU/RAM. Não roda thread própria — quem usar
    deve chamar .sample() periodicamente (ex.: tarefa asyncio a cada 5s)."""

    def __init__(self):
        self._cpu: deque[float] = deque(maxlen=_JANELA_AMOSTRAS)
        self._mem: deque[float] = deque(maxlen=_JANELA_AMOSTRAS)
        self._started = False

    @property
    def disponivel(self) -> bool:
        """False quando psutil não tá instalado — chamador deve cair em modo
        single-worker conservador."""
        return _PSUTIL_DISPONIVEL

    def start(self) -> None:
        """Warm-up. psutil.cpu_percent(interval=None) na 1ª chamada retorna 0.0
        porque ainda não tem janela de comparação. Chamamos uma vez vazio
        pra "armar" — próximas chamadas retornam valor real."""
        if not _PSUTIL_DISPONIVEL:
            self._started = True
            return
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            # psutil pode falhar em containers sem acesso a /proc cgroup
            pass
        self._started = True

    def sample(self) -> dict:
        """Lê CPU/RAM atual e adiciona ao histórico. Retorna {cpu, mem}
        pra logs. Custo: ~1ms (lê /proc), não bloqueia."""
        if not _PSUTIL_DISPONIVEL or not self._started:
            return {"cpu": 0.0, "mem": 0.0}
        try:
            cpu = float(psutil.cpu_percent(interval=None))
            mem = float(psutil.virtual_memory().percent)
        except Exception:
            return {"cpu": 0.0, "mem": 0.0}
        self._cpu.append(cpu)
        self._mem.append(mem)
        return {"cpu": cpu, "mem": mem}

    def cpu_media(self) -> float:
        if not self._cpu:
            return 0.0
        return sum(self._cpu) / len(self._cpu)

    def mem_media(self) -> float:
        if not self._mem:
            return 0.0
        return sum(self._mem) / len(self._mem)

    def sob_stress(self, cpu_limite: float, mem_limite: float) -> bool:
        """True se CPU média > cpu_limite OU memória média > mem_limite."""
        if not _PSUTIL_DISPONIVEL:
            return False  # sem telemetria, presume sem stress (modo legado)
        return self.cpu_media() > cpu_limite or self.mem_media() > mem_limite

    def amostras_coletadas(self) -> int:
        """Quantas amostras já temos no histórico. Útil pra saber quando a
        média já é confiável (<3 amostras = pouco dado)."""
        return len(self._cpu)
