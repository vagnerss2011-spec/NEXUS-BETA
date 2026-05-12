import asyncio
import logging
import time
import traceback
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from models import Device, Backup, Configuracao, LogScheduler, Protocolo
from services.ssh_service import run_backup
from services.telegram import enviar_alerta_async
from services.system_health import HealthMonitor
from config import settings

log = logging.getLogger(__name__)
# uvicorn default suprime INFO de loggers não-uvicorn — usar WARNING quando
# o evento for crítico (pico/alerta) garante que aparece em `docker logs`.

scheduler = AsyncIOScheduler()

# Bundling: se falhas em uma run >= esse limite, manda 1 mensagem agregada
# em vez de N individuais — evita spammar o grupo do Telegram quando uma
# queda de internet derruba 50 devices ao mesmo tempo.
MAX_ALERTAS_INDIVIDUAIS = 5

# Tamanho da janela histórica usada pra calcular a média de duração por device
# (e detectar picos). 10 backups cobre ~1 semana e meia em retenção default 7.
JANELA_MEDIA_DURACAO = 10

# Limite mínimo de coletas históricas pra considerar a média "confiável". Se o
# device tem só 1-2 backups, qualquer variação parece pico — só checamos depois
# que houver pelo menos esse número de durações registradas.
MIN_HISTORICO_PARA_PICO = 3

# Ratio (tamanho_novo / tamanho_anterior) abaixo do qual disparamos "alerta de
# redução suspeita" — mesma regra que o frontend já aplica visualmente em
# Backups.jsx (ALERTA_RATIO=0.5). Mantido sincronizado.
ALERTA_RATIO_TAMANHO = 0.5

# ===== Paralelismo adaptativo (AIMD-like) =====
# Hard cap absoluto — admin não consegue passar disso via Settings. Protege
# contra config errada que estouraria conexões SSH (Paramiko abre 1 thread
# + socket por sessão; 10+ em paralelo é receita pra OOM/exhaustion).
WORKERS_HARD_CAP = 8
# Sample da telemetria a cada N segundos pelo monitor em background.
HEALTH_SAMPLE_INTERVAL_SEG = 5
# Quantos sucessos consecutivos sem stress até o monitor sugerir +1 worker
# (additive increase). Inspirado em TCP slow-start.
SUCESSOS_PARA_SUBIR = 3

PURGA_LOGS_JOB_ID = "purga_logs_diaria"


def _ext_arquivo(nome: str | None) -> str:
    """Extensão lowercase de nome_arquivo. Replica _extOf() do frontend pra
    que UNM2000 (envia .txt + .zip) não compare tamanho cross-extension."""
    if not nome:
        return ""
    i = nome.rfind(".")
    return nome[i:].lower() if i >= 0 else ""


async def executar_backups():
    """Job diário que pola SSH/Telnet/API com paralelismo adaptativo.

    Comportamento v2.x:
    - DOIS POOLS independentes: API (mais agressivo, default cap 4) e
      SSH/Telnet (conservador, default cap 2). Cada pool tem seu target
      dinâmico que sobe gradualmente.
    - Telemetria contínua (psutil) sample a cada 5s — média móvel 60s
      de CPU e RAM. Decisões só consideram a média, não pico isolado.
    - AIMD: a cada SUCESSOS_PARA_SUBIR sucessos de um pool sem stress,
      target += 1 (até o cap). Stress detectado → target //= 2.
    - Auto OFF (backup_workers_auto=False) trava em target=1 (modo legado).
    - Delay adaptativo aplicado POR WORKER (cada slot espera antes do próximo
      device da sua fila).
    - Picos/reduções de tamanho detectados igual antes; alertas Telegram
      idem.
    """
    inicio = datetime.now(timezone.utc)
    log_run = LogScheduler(inicio=inicio)

    async with AsyncSessionLocal() as db:
        db.add(log_run)
        # COMMIT (não só flush!) — workers paralelos abrem sessions próprias e
        # precisam enxergar log_run.id no banco pra inserir backups referenciando
        # essa FK (backups.log_scheduler_id). Sem commit aqui, sessions paralelas
        # caem em ForeignKeyViolationError (regressão da v2.0.0 paralelismo).
        # expire_on_commit=False (database.py) garante que log_run continua
        # utilizável pra updates posteriores na mesma session.
        await db.commit()
        await db.refresh(log_run)
        log_id = log_run.id

        # Lê config 1x no início — mudanças durante a run só valem na próxima.
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if config is None:
            config = Configuracao()
            db.add(config)
            await db.commit()
            await db.refresh(config)
        delay_min = max(0, int(config.backup_delay_min_seg or 0))
        delay_fator = max(0.0, float(config.backup_delay_fator or 0.0))
        pico_fator = max(1.0, float(config.backup_pico_fator_critico or 3.0))
        workers_max_api = min(WORKERS_HARD_CAP, max(1, int(config.backup_workers_max_api or 4)))
        workers_max_ssh = min(WORKERS_HARD_CAP, max(1, int(config.backup_workers_max_ssh or 2)))
        cpu_limite = max(30, min(95, int(config.backup_cpu_limite_pct or 80)))
        mem_limite = max(30, min(95, int(config.backup_mem_limite_pct or 80)))
        auto_tuning = bool(config.backup_workers_auto if config.backup_workers_auto is not None else True)

        # Estado compartilhado entre coleta + monitor + workers.
        # Mutável: vivem em dict pra captura por referência sem `nonlocal`.
        state: dict = {
            "target_api": 1,        # workers atuais permitidos no pool API
            "target_ssh": 1,        # workers atuais permitidos no pool SSH/Telnet
            "max_api": 1,           # pico de target_api durante a run
            "max_ssh": 1,           # pico de target_ssh
            "sucessos_api": 0,      # contador AIMD do pool API (zera ao subir)
            "sucessos_ssh": 0,      # idem SSH/Telnet
            "tempo_stress_seg": 0,  # acumula segundos com CPU/mem > limite
            "parar": False,         # flag pra encerrar monitor ao fim
        }

        # Quando auto OFF, trava em 1 — caps "max_*" também = 1.
        if not auto_tuning:
            workers_max_api = 1
            workers_max_ssh = 1

        # Inicializa telemetria (psutil warm-up).
        health = HealthMonitor()
        health.start()

        total = sucessos = falhas = 0
        picos_detectados = 0
        alertas_tamanho_run = 0
        duracoes: list[int] = []
        falhas_da_run: list[tuple[Device, str, int | None]] = []
        alertas_da_run: list[tuple[Device, str, int | None]] = []
        # Lock pra proteger atualizações nos contadores acima — múltiplos
        # workers chamam _registrar_resultado concorrente.
        contadores_lock = asyncio.Lock()

        async def _registrar_resultado(
            device: Device, ok: bool, erro_msg, duracao_seg, alerta_tipo, alerta_msg, pool_nome: str,
        ):
            """Centraliza incremento de contadores e listas. Chamado por todos
            os workers — protegido por lock pra não ter race em duracoes/falhas."""
            nonlocal total, sucessos, falhas, picos_detectados, alertas_tamanho_run
            async with contadores_lock:
                if duracao_seg is not None:
                    duracoes.append(duracao_seg)
                if ok:
                    sucessos += 1
                    # AIMD additive: sucesso conta pra subir o target do pool.
                    state[f"sucessos_{pool_nome}"] += 1
                else:
                    falhas += 1
                    falhas_da_run.append((device, erro_msg, device.empresa_id))
                    # Falha NÃO conta como sucesso pro AIMD, mas também não força
                    # corte automático — só se telemetria mostrar stress real.
                if alerta_tipo == "pico":
                    picos_detectados += 1
                    alertas_da_run.append((device, alerta_msg, device.empresa_id))
                elif alerta_tipo == "reducao":
                    alertas_tamanho_run += 1
                    alertas_da_run.append((device, alerta_msg, device.empresa_id))

        try:
            # Carrega devices pull (não-push, não-manual).
            result = await db.execute(
                select(Device).where(
                    Device.ativo == True,
                    Device.backup_manual_apenas == False,
                    Device.protocolo.notin_((
                        Protocolo.ftp_push, Protocolo.sftp_push, Protocolo.tftp_push,
                    )),
                ).order_by(Device.id)
            )
            devices_all = result.scalars().all()
            total = len(devices_all)

            # Separa por pool. Pula API pra pool dedicado; SSH+Telnet compartilham
            # o pool "ssh" (ambos via Paramiko/Netmiko — mesma classe de carga).
            devices_api = [d for d in devices_all if d.protocolo == Protocolo.api]
            devices_ssh = [d for d in devices_all if d.protocolo in (Protocolo.ssh, Protocolo.telnet)]

            # Dispara monitor de telemetria em background — ajusta target_api/ssh
            # baseado em CPU/RAM. Cancelado no `finally`.
            monitor_task = asyncio.create_task(
                _monitor_telemetria(
                    health, state, cpu_limite, mem_limite,
                    workers_max_api, workers_max_ssh, auto_tuning,
                )
            )

            try:
                # Roda os dois pools em paralelo. Cada pool é uma fila + N workers
                # dinâmicos que leem state["target_<pool>"] antes de cada device.
                await asyncio.gather(
                    _executar_pool(
                        "api", devices_api, state, log_id, pico_fator,
                        delay_min, delay_fator, _registrar_resultado,
                    ),
                    _executar_pool(
                        "ssh", devices_ssh, state, log_id, pico_fator,
                        delay_min, delay_fator, _registrar_resultado,
                    ),
                )
            finally:
                state["parar"] = True
                monitor_task.cancel()
                try:
                    await monitor_task
                except (asyncio.CancelledError, Exception):
                    pass

            fim = datetime.now(timezone.utc)
            log_run.fim = fim
            log_run.total = total
            log_run.sucessos = sucessos
            log_run.falhas = falhas
            log_run.duracao_total_segundos = int((fim - inicio).total_seconds())
            log_run.duracao_media_segundos = (sum(duracoes) / len(duracoes)) if duracoes else None
            log_run.picos_detectados = picos_detectados
            log_run.alertas_tamanho = alertas_tamanho_run
            log_run.workers_max_atingido_api = state["max_api"]
            log_run.workers_max_atingido_ssh = state["max_ssh"]
            log_run.tempo_sob_stress_seg = state["tempo_stress_seg"]
            await db.commit()

            await _disparar_alertas_telegram(db, falhas_da_run)
            if alertas_da_run:
                await _disparar_alertas_telegram_v2(db, alertas_da_run)

        except Exception:
            log_run.fim = datetime.now(timezone.utc)
            log_run.total = total
            log_run.sucessos = sucessos
            log_run.falhas = falhas
            log_run.duracao_total_segundos = int((datetime.now(timezone.utc) - inicio).total_seconds())
            log_run.duracao_media_segundos = (sum(duracoes) / len(duracoes)) if duracoes else None
            log_run.picos_detectados = picos_detectados
            log_run.alertas_tamanho = alertas_tamanho_run
            log_run.workers_max_atingido_api = state["max_api"]
            log_run.workers_max_atingido_ssh = state["max_ssh"]
            log_run.tempo_sob_stress_seg = state["tempo_stress_seg"]
            log_run.erro_geral = traceback.format_exc()
            await db.commit()


async def _monitor_telemetria(
    health: HealthMonitor, state: dict,
    cpu_limite: int, mem_limite: int,
    workers_max_api: int, workers_max_ssh: int,
    auto_tuning: bool,
) -> None:
    """Loop de fundo que sample CPU/RAM a cada HEALTH_SAMPLE_INTERVAL_SEG
    e ajusta state['target_api']/['target_ssh'] (AIMD).

    Quando auto_tuning=False, ainda roda mas não muda targets — só acumula
    tempo_stress_seg pra log (útil pra admin saber se a infra tava saturada
    mesmo em modo single-worker)."""
    try:
        while not state["parar"]:
            await asyncio.sleep(HEALTH_SAMPLE_INTERVAL_SEG)
            amostra = health.sample()
            stress = health.sob_stress(cpu_limite, mem_limite)
            if stress:
                state["tempo_stress_seg"] += HEALTH_SAMPLE_INTERVAL_SEG
                log.warning(
                    "STRESS scheduler: cpu_media=%.0f%% mem_media=%.0f%% (limites %d/%d)",
                    health.cpu_media(), health.mem_media(), cpu_limite, mem_limite,
                )

            if not auto_tuning:
                continue

            if stress:
                # Multiplicative decrease — corta workers pela metade (mínimo 1).
                novo_api = max(1, state["target_api"] // 2)
                novo_ssh = max(1, state["target_ssh"] // 2)
                if novo_api != state["target_api"] or novo_ssh != state["target_ssh"]:
                    log.warning(
                        "SCHEDULER ↓ target: api=%d→%d ssh=%d→%d (stress detectado)",
                        state["target_api"], novo_api, state["target_ssh"], novo_ssh,
                    )
                state["target_api"] = novo_api
                state["target_ssh"] = novo_ssh
                # Reseta contadores de sucesso — não queremos subir logo após cair.
                state["sucessos_api"] = 0
                state["sucessos_ssh"] = 0
            else:
                # Additive increase — sobe 1 worker do pool que acumulou sucessos.
                if state["sucessos_api"] >= SUCESSOS_PARA_SUBIR and state["target_api"] < workers_max_api:
                    state["target_api"] += 1
                    state["sucessos_api"] = 0
                    state["max_api"] = max(state["max_api"], state["target_api"])
                    log.info("SCHEDULER ↑ target_api=%d", state["target_api"])
                if state["sucessos_ssh"] >= SUCESSOS_PARA_SUBIR and state["target_ssh"] < workers_max_ssh:
                    state["target_ssh"] += 1
                    state["sucessos_ssh"] = 0
                    state["max_ssh"] = max(state["max_ssh"], state["target_ssh"])
                    log.info("SCHEDULER ↑ target_ssh=%d", state["target_ssh"])
    except asyncio.CancelledError:
        # Esperado quando executar_backups termina e cancela o monitor.
        return


async def _executar_pool(
    pool_nome: str, devices: list, state: dict, log_id: int, pico_fator: float,
    delay_min: int, delay_fator: float, registrar_callback,
) -> None:
    """Executa um pool de coletas em paralelo. Número de workers cresce/encolhe
    conforme state['target_<pool>'].

    Cada worker é uma task que pega da queue, processa o device em uma session
    SQLAlchemy própria (importante: sessions não são thread-safe) e libera o slot.
    """
    if not devices:
        return

    queue: asyncio.Queue = asyncio.Queue()
    for d in devices:
        queue.put_nowait(d)

    target_key = f"target_{pool_nome}"
    max_key = f"max_{pool_nome}"
    workers_ativos: set[asyncio.Task] = set()

    async def _worker():
        """Loop do worker: pega da fila, processa, aplica delay, repete."""
        while True:
            try:
                device = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                # Cada worker abre sua própria SQLAlchemy session — sessions
                # não toleram uso compartilhado entre coroutines concorrentes
                # (cursor compartilhado dá race fácil).
                async with AsyncSessionLocal() as db_worker:
                    ok, erro_msg, duracao_seg, alerta_tipo, alerta_msg = await _backup_device(
                        db_worker, device, log_id, pico_fator,
                    )
                    await db_worker.commit()
                await registrar_callback(
                    device, ok, erro_msg, duracao_seg, alerta_tipo, alerta_msg, pool_nome,
                )
            except Exception:
                # Não deixa o worker morrer silencioso — loga e segue.
                log.exception("Worker pool=%s falhou em device id=%s", pool_nome, getattr(device, "id", "?"))
            # Delay adaptativo antes do próximo device DESTE worker. Outros
            # workers do mesmo pool podem estar pegando devices ao mesmo tempo;
            # não há sincronização global de delay (intencional — pool maior =
            # janela total menor; o delay é só fôlego entre coletas do MESMO worker).
            if not queue.empty() and (delay_min > 0 or delay_fator > 0):
                base = duracao_seg if "duracao_seg" in locals() and duracao_seg else 0
                delay = max(delay_min, int(round(delay_fator * base)))
                if delay > 0:
                    await asyncio.sleep(delay)

    # Loop de orquestração: cresce/encolhe o número de workers ativos pra
    # bater state[target_key]. Lê target_key a cada iteração — assim o
    # monitor de telemetria pode mexer em tempo real.
    while not queue.empty() or workers_ativos:
        # Limpa tasks já completas.
        completas = [t for t in workers_ativos if t.done()]
        for t in completas:
            workers_ativos.discard(t)
            # `await t` pra propagar exceção; já loggamos no worker, então só
            # garante que não fica como pending exception.
            try:
                await t
            except Exception:
                pass

        target = state[target_key]
        # Spawna workers até atingir target (ou esvaziar a fila).
        while len(workers_ativos) < target and not queue.empty():
            t = asyncio.create_task(_worker())
            workers_ativos.add(t)
            state[max_key] = max(state[max_key], len(workers_ativos))

        # Quando target encolhe (monitor cortou), não matamos workers no meio
        # de uma coleta — apenas paramos de spawnar novos. Workers ativos
        # terminam o device atual e morrem naturalmente quando a fila esvazia.

        if workers_ativos:
            # Espera pelo menos UM worker terminar antes de re-avaliar target.
            # Timeout curto pra reagir rápido a mudanças no target.
            await asyncio.wait(
                workers_ativos, return_when=asyncio.FIRST_COMPLETED, timeout=1.0,
            )
        elif not queue.empty():
            # Sem workers ativos mas com fila pendente — pode acontecer se
            # target=0 (não devia, mas garantia extra). Espera um pouco e
            # re-avalia.
            await asyncio.sleep(0.5)


async def _disparar_alertas_telegram(
    db: AsyncSession, falhas: list[tuple[Device, str, int | None]],
) -> None:
    """Envia alertas Telegram para a run inteira.

    Lógica de bundling:
    - 0 falhas: silencioso (run perfeita não merece notificação)
    - 1 a MAX_ALERTAS_INDIVIDUAIS: 1 mensagem por device falhado, agrupado por empresa
      (mensagem vai pro chat da empresa quando ela tem chat próprio)
    - acima: 1 mensagem agregada por empresa, listando os devices

    Falhas com empresa_id=None (devices órfãos) viram alerta no chat default.
    """
    if not falhas:
        return

    # Agrupa por empresa pra respeitar override de chat_id por empresa
    por_empresa: dict[int | None, list[tuple[Device, str]]] = {}
    for dev, msg, emp_id in falhas:
        por_empresa.setdefault(emp_id, []).append((dev, msg))

    for emp_id, items in por_empresa.items():
        if len(items) <= MAX_ALERTAS_INDIVIDUAIS:
            for dev, msg in items:
                titulo = f"❌ Falha de backup — {dev.nome}"
                detalhes = (
                    f"<b>IP:</b> <code>{dev.ip}</code>\n"
                    f"<b>Fabricante:</b> {dev.fabricante.value if dev.fabricante else '—'}\n"
                    f"<b>Erro:</b> <i>{(msg or '')[:300]}</i>"
                )
                await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="falha_backup")
        else:
            # Bundle agregado — lista nomes dos primeiros 10 e mostra contagem total.
            nomes = ", ".join(d.nome for d, _ in items[:10])
            mais = f" e mais {len(items) - 10}" if len(items) > 10 else ""
            titulo = f"⚠️ {len(items)} backups falharam"
            detalhes = (
                f"<b>Devices:</b> {nomes}{mais}\n"
                f"<i>Provável causa comum (rede, scheduler). Veja o painel para detalhes.</i>"
            )
            await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="falha_backup")


async def _backup_device(
    db: AsyncSession, device: Device, log_id: int, pico_fator: float,
) -> tuple[bool, str | None, int | None, str | None, str | None]:
    """Roda backup do device. Retorna tupla:

    (sucesso, erro_resumido, duracao_segundos, alerta_tipo, alerta_msg)

    - sucesso: bool — backup criado com status='sucesso'
    - erro_resumido: primeira linha do erro (pra Telegram) ou None se ok
    - duracao_segundos: tempo de coleta (sempre preenchido, mesmo em falha)
    - alerta_tipo: None | 'pico' | 'reducao' — só pra runs bem-sucedidas
    - alerta_msg: descrição curta do alerta (pra Telegram + log)
    """
    t_inicio = time.monotonic()
    duracao = 0
    status: str = "falha"
    conteudo: str | None = None
    erro_resumido: str | None = None
    erro_tb: str | None = None

    try:
        # run_backup é síncrono (Paramiko/Netmiko/librouteros). Em paralelismo
        # com asyncio.gather + múltiplos workers, chamada direta bloquearia o
        # event loop e serializaria tudo de novo. asyncio.to_thread joga pra
        # uma worker thread → paralelismo real.
        status, conteudo = await asyncio.to_thread(run_backup, device)
        duracao = int(round(time.monotonic() - t_inicio))
        if status != "sucesso":
            erro_resumido = (conteudo or "Falha sem detalhe").splitlines()[0][:300]
    except Exception:
        duracao = int(round(time.monotonic() - t_inicio))
        erro_tb = traceback.format_exc()
        status = "falha"
        erro_resumido = erro_tb.splitlines()[-1][:300]

    backup = Backup(
        device_id=device.id,
        log_scheduler_id=log_id,
        status=status,
        conteudo=conteudo if status == "sucesso" else None,
        erro=(conteudo if status == "falha" and conteudo else erro_tb),
        origem="scheduler",
        duracao_segundos=duracao,
    )
    db.add(backup)
    await db.flush()
    await _limpar_backups_antigos(db, device.id)

    if status != "sucesso":
        return False, erro_resumido, duracao, None, None

    # ===== Validações pós-coleta (só pra sucesso) =====
    alerta_tipo: str | None = None
    alerta_msg: str | None = None

    # 1) Detecção de pico — duração > pico_fator × média histórica.
    media_duracao = await _media_duracao_historica(db, device.id, exclude_backup_id=backup.id)
    if media_duracao is not None and duracao > pico_fator * media_duracao:
        alerta_tipo = "pico"
        alerta_msg = (
            f"Backup levou {duracao}s — {duracao / media_duracao:.1f}× "
            f"a média histórica ({media_duracao:.0f}s)"
        )
        log.warning(
            "PICO scheduler: device=%s id=%s duracao=%ss media=%.0fs ratio=%.2f",
            device.nome, device.id, duracao, media_duracao, duracao / media_duracao,
        )

    # 2) Redução suspeita de tamanho — < 50% do último sucesso (mesma extensão).
    # Só checa se ainda não há alerta de pico (não polui com 2 alertas pro mesmo backup).
    if alerta_tipo is None and conteudo:
        anterior_tamanho = await _ultimo_sucesso_tamanho(
            db, device.id, _ext_arquivo(backup.nome_arquivo), exclude_backup_id=backup.id,
        )
        novo_tamanho = len(conteudo)
        if anterior_tamanho is not None and anterior_tamanho > 0 \
           and novo_tamanho < ALERTA_RATIO_TAMANHO * anterior_tamanho:
            alerta_tipo = "reducao"
            alerta_msg = (
                f"Backup com {novo_tamanho} bytes — só "
                f"{(novo_tamanho / anterior_tamanho * 100):.0f}% do anterior "
                f"({anterior_tamanho} bytes)"
            )
            log.warning(
                "REDUÇÃO scheduler: device=%s id=%s novo=%s anterior=%s ratio=%.2f",
                device.nome, device.id, novo_tamanho, anterior_tamanho,
                novo_tamanho / anterior_tamanho,
            )

    return True, None, duracao, alerta_tipo, alerta_msg


async def _media_duracao_historica(
    db: AsyncSession, device_id: int, *, exclude_backup_id: int | None = None,
) -> float | None:
    """Média (segundos) das últimas JANELA_MEDIA_DURACAO durações registradas
    do device. Ignora backups sem duracao_segundos (push, pré-feature) e o
    backup recém-inserido (passado em exclude_backup_id). Retorna None quando
    histórico < MIN_HISTORICO_PARA_PICO."""
    q = (
        select(Backup.duracao_segundos)
        .where(Backup.device_id == device_id, Backup.duracao_segundos.is_not(None))
        .order_by(Backup.criado_em.desc())
        .limit(JANELA_MEDIA_DURACAO)
    )
    if exclude_backup_id is not None:
        q = q.where(Backup.id != exclude_backup_id)
    rows = (await db.execute(q)).fetchall()
    duracoes = [r[0] for r in rows if r[0] is not None]
    if len(duracoes) < MIN_HISTORICO_PARA_PICO:
        return None
    return sum(duracoes) / len(duracoes)


async def _ultimo_sucesso_tamanho(
    db: AsyncSession, device_id: int, extensao: str, *, exclude_backup_id: int,
) -> int | None:
    """Retorna tamanho (len do conteudo) do ÚLTIMO backup bem-sucedido anterior
    do device com a MESMA extensão de nome_arquivo. UNM2000 envia .txt+.zip
    e cada extensão tem ordem de magnitude diferente — comparar cross-extension
    daria sempre falso-positivo de "redução"."""
    q = (
        select(Backup.conteudo, Backup.nome_arquivo)
        .where(
            Backup.device_id == device_id,
            Backup.status == "sucesso",
            Backup.id != exclude_backup_id,
        )
        .order_by(Backup.criado_em.desc())
        .limit(5)  # checa só os últimos 5 — se não bate extensão neles, desiste
    )
    rows = (await db.execute(q)).fetchall()
    for conteudo, nome_arq in rows:
        if _ext_arquivo(nome_arq) != extensao:
            continue
        return len(conteudo) if conteudo else 0
    return None


async def _disparar_alertas_telegram_v2(
    db: AsyncSession, alertas: list[tuple[Device, str, int | None]],
) -> None:
    """Alertas de pico de duração / redução de tamanho.

    Usa categoria 'volume_alto' (reaproveita o flag já existente do Telegram —
    o admin não precisa habilitar nada novo se já tinha esse alerta ligado).
    Aplica o mesmo bundling de _disparar_alertas_telegram pra não spammar.
    """
    if not alertas:
        return
    por_empresa: dict[int | None, list[tuple[Device, str]]] = {}
    for dev, msg, emp_id in alertas:
        por_empresa.setdefault(emp_id, []).append((dev, msg))

    for emp_id, items in por_empresa.items():
        if len(items) <= MAX_ALERTAS_INDIVIDUAIS:
            for dev, msg in items:
                titulo = f"⚠️ Atenção no backup — {dev.nome}"
                detalhes = (
                    f"<b>IP:</b> <code>{dev.ip}</code>\n"
                    f"<b>Fabricante:</b> {dev.fabricante.value if dev.fabricante else '—'}\n"
                    f"<b>Alerta:</b> <i>{msg}</i>"
                )
                await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="volume_alto")
        else:
            nomes = ", ".join(d.nome for d, _ in items[:10])
            mais = f" e mais {len(items) - 10}" if len(items) > 10 else ""
            titulo = f"⚠️ {len(items)} alertas no scheduler"
            detalhes = (
                f"<b>Devices:</b> {nomes}{mais}\n"
                f"<i>Picos de duração ou redução de tamanho — ver Logs do painel.</i>"
            )
            await enviar_alerta_async(db, titulo, detalhes, empresa_id=emp_id, categoria="volume_alto")


async def _limpar_backups_antigos(db: AsyncSession, device_id: int):
    result = await db.execute(
        select(Backup.id)
        .where(Backup.device_id == device_id)
        .order_by(Backup.criado_em.desc())
        .offset(settings.BACKUP_RETENTION_DAYS)
    )
    ids_antigos = [row[0] for row in result.fetchall()]
    if ids_antigos:
        await db.execute(delete(Backup).where(Backup.id.in_(ids_antigos)))


async def purgar_logs_antigos():
    """Remove logs do scheduler mais velhos que Configuracao.log_retention_days.
    Se log_retention_days == 0, a purga fica desativada."""
    async with AsyncSessionLocal() as db:
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if not config or config.log_retention_days <= 0:
            return
        corte = datetime.now(timezone.utc) - timedelta(days=config.log_retention_days)
        await db.execute(delete(LogScheduler).where(LogScheduler.criado_em < corte))
        await db.commit()


# ===== Export do banco em .nxbak (v2.0.0) =====

DB_EXPORT_JOB_ID = "db_export_diario"
DB_EXPORT_REMOTE_JOB_ID = "db_export_remoto_semanal"


async def executar_db_export_local():
    """Job diário: gera o .nxbak com snapshot do banco e aplica retenção."""
    from services import db_export as _dbex
    async with AsyncSessionLocal() as db:
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if config is None or not config.db_export_enabled:
            log.info("DB export pulado (db_export_enabled=false)")
            return
        try:
            arquivo = await _dbex.exportar_para_arquivo(db)
            if arquivo is None:
                # DB_EXPORT_KEY ausente — já loggou warning
                await _enviar_alerta_falha_export(
                    db, "DB_EXPORT_KEY não configurada no .env",
                )
                return
            _dbex.aplicar_retencao()
        except Exception as e:
            log.exception("DB export falhou")
            await _enviar_alerta_falha_export(db, str(e))


async def executar_db_export_remoto():
    """Job semanal: envia o .nxbak mais recente pra nuvem de segurança."""
    from services import db_export as _dbex
    async with AsyncSessionLocal() as db:
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
        if config is None or not config.db_export_remote_enabled:
            log.info("DB export remoto pulado (db_export_remote_enabled=false)")
            return
        ok, msg = await _dbex.enviar_mais_recente_pra_nuvem(db)
        if not ok:
            await _enviar_alerta_falha_export(db, f"Upload remoto falhou: {msg}")


async def _enviar_alerta_falha_export(db: AsyncSession, motivo: str) -> None:
    """Alerta Telegram (categoria falha_backup) quando export ou upload falham.
    Diferente das falhas de coleta normais — esta é falha do mecanismo de
    segurança em si, merece atenção do admin master."""
    try:
        titulo = "❌ Export do banco (.nxbak) falhou"
        detalhes = f"<b>Motivo:</b> <i>{motivo[:400]}</i>"
        # empresa_id=None → vai pro chat default global (admin master)
        await enviar_alerta_async(db, titulo, detalhes, empresa_id=None, categoria="falha_backup")
    except Exception:
        log.exception("Falha ao enviar alerta Telegram do db-export")


async def _carregar_horario() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Configuracao))
        config = result.scalar_one_or_none()
        if config:
            return config.backup_hour, config.backup_minute
        config = Configuracao(backup_hour=2, backup_minute=0)
        db.add(config)
        await db.commit()
        return 2, 0


def atualizar_scheduler(hour: int, minute: int):
    scheduler.reschedule_job("backup_diario", trigger="cron", hour=hour, minute=minute)


def atualizar_db_export_local(hour: int, minute: int):
    """Reagenda o job de export diário do banco. Chamado pelo router quando
    admin muda backup_export_hour/minute."""
    if scheduler.get_job(DB_EXPORT_JOB_ID):
        scheduler.reschedule_job(DB_EXPORT_JOB_ID, trigger="cron", hour=hour, minute=minute)


def atualizar_db_export_remoto(dia_semana: int, hour: int, minute: int):
    """Reagenda o job semanal de upload. dia_semana cron-style: 0=segunda,
    6=domingo (mesma convenção que APScheduler usa em day_of_week)."""
    if scheduler.get_job(DB_EXPORT_REMOTE_JOB_ID):
        scheduler.reschedule_job(
            DB_EXPORT_REMOTE_JOB_ID, trigger="cron",
            day_of_week=dia_semana, hour=hour, minute=minute,
        )


async def iniciar_scheduler():
    hour, minute = await _carregar_horario()
    scheduler.add_job(executar_backups, "cron", hour=hour, minute=minute, id="backup_diario")
    # Purga de logs: todo dia às 03:00 (TZ do container — definido em docker-compose)
    scheduler.add_job(purgar_logs_antigos, "cron", hour=3, minute=0, id=PURGA_LOGS_JOB_ID)

    # ===== Jobs do .nxbak (v2.0.0) =====
    # Lê hora configurada (ou usa defaults se não existir Configuracao ainda).
    async with AsyncSessionLocal() as db:
        config = (await db.execute(select(Configuracao))).scalar_one_or_none()
    h_export = config.db_export_hour if config else 3
    m_export = config.db_export_minute if config else 30
    dow_remoto = config.db_export_remote_dia_semana if config else 0
    h_remoto = config.db_export_remote_hora if config else 4
    m_remoto = config.db_export_remote_minute if config else 0
    # Diário: sempre registrado. O job em si checa db_export_enabled e pula
    # se desligado — assim admin pode ligar/desligar via UI sem precisar
    # reagendar o job.
    scheduler.add_job(
        executar_db_export_local, "cron",
        hour=h_export, minute=m_export, id=DB_EXPORT_JOB_ID,
    )
    # Semanal: idem — job checa db_export_remote_enabled e pula.
    scheduler.add_job(
        executar_db_export_remoto, "cron",
        day_of_week=dow_remoto, hour=h_remoto, minute=m_remoto,
        id=DB_EXPORT_REMOTE_JOB_ID,
    )
    scheduler.start()
