from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text
from database import engine, Base
from routers import auth, users, devices, backups, settings, logs, empresas, atividades, version, info
from version import APP_VERSION
from services.scheduler import iniciar_scheduler, scheduler
from services.ftp_server import iniciar_ftp_server, parar_ftp_server
from services.sftp_server import iniciar_sftp_server, parar_sftp_server
from services.tftp_server import iniciar_tftp_server, parar_tftp_server

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enum ADD VALUE precisa rodar fora de transação em algumas versões do Postgres.
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await conn.execute(text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'admin_empresa'"))
        except Exception:
            pass  # tipo pode ainda não existir no primeiro boot; create_all cria logo abaixo
        try:
            await conn.execute(text("ALTER TYPE tipoatividade ADD VALUE IF NOT EXISTS 'device_removido'"))
        except Exception:
            pass
        try:
            await conn.execute(text("ALTER TYPE tipoatividade ADD VALUE IF NOT EXISTS 'backup_removido'"))
        except Exception:
            pass
        # FTP/SFTP/TFTP push: novos valores no enum protocolo + atividades relacionadas
        # 'api' adicionado em v1.4.0 (RouterOS API binária — Mikrotik v6+v7)
        for proto in ("ftp_push", "sftp_push", "tftp_push", "api"):
            try:
                await conn.execute(text(f"ALTER TYPE protocolo ADD VALUE IF NOT EXISTS '{proto}'"))
            except Exception:
                pass
        for ev in ("ftp_backup_recebido", "ftp_volume_alto", "ftp_acesso_negado",
                   "ftp_backup_falha"):
            try:
                await conn.execute(text(f"ALTER TYPE tipoatividade ADD VALUE IF NOT EXISTS '{ev}'"))
            except Exception:
                pass
        # Novos fabricantes (ZTE, Nokia, Fiberhome, VSolutions, Mikrotik v7)
        for vendor in ("zte", "nokia", "fiberhome", "vsolutions", "mikrotik_v7"):
            try:
                await conn.execute(text(f"ALTER TYPE devicevendor ADD VALUE IF NOT EXISTS '{vendor}'"))
            except Exception:
                pass
        # Novo tipo: UNM2000 (NMS Fiberhome — recebe push backup do próprio EMS)
        try:
            await conn.execute(text("ALTER TYPE devicetipo ADD VALUE IF NOT EXISTS 'unm2000'"))
        except Exception:
            pass
        # Cria o enum authmethod (idempotente). NOT EXISTS no CREATE TYPE
        # ainda não existe no Postgres, então usamos DO $$ BEGIN ... END$$.
        try:
            await conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'authmethod') THEN
                        CREATE TYPE authmethod AS ENUM ('password', 'ssh_key');
                    END IF;
                END$$;
            """))
        except Exception:
            pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # --- Migrações incrementais (idempotentes) ---

        # 1) backups.log_scheduler_id (legado)
        await conn.execute(text("""
            ALTER TABLE backups
            ADD COLUMN IF NOT EXISTS log_scheduler_id INTEGER
            REFERENCES log_scheduler(id) ON DELETE SET NULL
        """))
        # 1.2) backups.origem — diferencia manual / scheduler / push.
        # Default 'manual' cobre rows pré-migração que não tinham origem registrada
        # (a UI antiga já mostrava elas como "Manual" baseada em log_scheduler_id IS NULL,
        # então o default mantém compatibilidade visual pra histórico antigo).
        await conn.execute(text("""
            ALTER TABLE backups
            ADD COLUMN IF NOT EXISTS origem VARCHAR(16) NOT NULL DEFAULT 'manual'
        """))
        # 1.3) backups.nome_arquivo — preserva nome do arquivo original recebido
        # via push. Crítico para UNM2000 que envia 1 zip do banco próprio + N
        # arquivos .cfg de OLTs distintas usando a mesma credencial FTP. NULL
        # nas rows pré-migração e em backups SSH (que não vêm de arquivo).
        await conn.execute(text("""
            ALTER TABLE backups
            ADD COLUMN IF NOT EXISTS nome_arquivo VARCHAR(255)
        """))
        # 1.1) Garante que a constraint tenha ON DELETE SET NULL.
        # Em ambientes antigos a tabela foi criada via create_all SEM ondelete
        # no model, e o ADD COLUMN IF NOT EXISTS acima virou no-op porque a
        # coluna já existia. Sem ON DELETE SET NULL, deletar uma linha de
        # log_scheduler quebra com FK violation. Esta migração detecta o
        # estado atual e recria a constraint só se necessário.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'backups_log_scheduler_id_fkey'
                      AND confdeltype <> 'n'  -- 'n' = SET NULL; outros valores indicam comportamento errado
                ) THEN
                    ALTER TABLE backups DROP CONSTRAINT backups_log_scheduler_id_fkey;
                    ALTER TABLE backups
                        ADD CONSTRAINT backups_log_scheduler_id_fkey
                        FOREIGN KEY (log_scheduler_id) REFERENCES log_scheduler(id)
                        ON DELETE SET NULL;
                END IF;
            END$$;
        """))

        # 3) users.empresa_id
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS empresa_id INTEGER
            REFERENCES empresas(id) ON DELETE SET NULL
        """))

        # 3.1) users: anti-bruteforce (lockout de conta após N senhas erradas)
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS tentativas_falhas INTEGER NOT NULL DEFAULT 0
        """))
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS bloqueado_ate TIMESTAMP WITH TIME ZONE
        """))
        # 3.2) users.senha_temporaria — usuários existentes mantêm acesso
        # (DEFAULT FALSE no ADD COLUMN), só novos cadastros são marcados
        # como senha temporária pelo router.
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS senha_temporaria BOOLEAN NOT NULL DEFAULT FALSE
        """))

        # 4) devices.empresa_id — adicionado nullable; depois migra órfãos e vira NOT NULL
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS empresa_id INTEGER
            REFERENCES empresas(id) ON DELETE CASCADE
        """))

        # 5) Bootstrap: cria empresa default APENAS se a tabela estiver vazia.
        # Evita ressuscitar a "Empresa de Testes" depois que o admin já apagou em produção.
        await conn.execute(text("""
            INSERT INTO empresas (nome, cnpj, ativo)
            SELECT 'Empresa de Testes', NULL, TRUE
            WHERE NOT EXISTS (SELECT 1 FROM empresas)
        """))

        # 6) Migra dispositivos órfãos para a empresa de testes
        await conn.execute(text("""
            UPDATE devices
            SET empresa_id = (SELECT id FROM empresas WHERE nome = 'Empresa de Testes' LIMIT 1)
            WHERE empresa_id IS NULL
        """))

        # 7) Agora pode virar NOT NULL com segurança
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='devices' AND column_name='empresa_id' AND is_nullable='YES'
                ) THEN
                    ALTER TABLE devices ALTER COLUMN empresa_id SET NOT NULL;
                END IF;
            END$$;
        """))

        # 8) configuracoes.log_retention_days (default 30)
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS log_retention_days INTEGER NOT NULL DEFAULT 30
        """))

        # 8.0.1) configuracoes: campos de Telegram (alertas de falha/corrupção).
        # Token do bot encriptado com Fernet. Chat IDs em texto (não secret).
        # Toggles per-categoria default ON; só dispara se token estiver setado.
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS telegram_bot_token_enc VARCHAR(500)
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS telegram_chat_id_default VARCHAR(40)
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS telegram_alerta_falha_backup BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS telegram_alerta_push_negado BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS telegram_alerta_volume_alto BOOLEAN NOT NULL DEFAULT TRUE
        """))

        # Tuning do scheduler diário v2 — delay adaptativo entre devices SSH/Telnet/API.
        # Defaults conservadores que preservam o ritmo legado em prod pequena
        # (10s de pausa só faz diferença quando há muitos devices na fila).
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_delay_min_seg INTEGER NOT NULL DEFAULT 10
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_delay_fator DOUBLE PRECISION NOT NULL DEFAULT 0.2
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_pico_fator_critico DOUBLE PRECISION NOT NULL DEFAULT 3.0
        """))

        # backups.duracao_segundos — tempo de coleta por device (NULL = pré-feature ou push)
        await conn.execute(text("""
            ALTER TABLE backups
            ADD COLUMN IF NOT EXISTS duracao_segundos INTEGER
        """))

        # log_scheduler — métricas agregadas da janela diária (NULL = log pré-feature)
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS duracao_total_segundos INTEGER
        """))
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS duracao_media_segundos DOUBLE PRECISION
        """))
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS picos_detectados INTEGER
        """))
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS alertas_tamanho INTEGER
        """))

        # Paralelismo adaptativo (Zabbix-like) — caps + limites + auto on/off.
        # Defaults: API até 4, SSH até 2, CPU/RAM limite 80%, auto ON.
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_workers_max_api INTEGER NOT NULL DEFAULT 4
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_workers_max_ssh INTEGER NOT NULL DEFAULT 2
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_cpu_limite_pct INTEGER NOT NULL DEFAULT 80
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_mem_limite_pct INTEGER NOT NULL DEFAULT 80
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS backup_workers_auto BOOLEAN NOT NULL DEFAULT TRUE
        """))

        # Métricas de paralelismo gravadas a cada run do scheduler
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS workers_max_atingido_api INTEGER
        """))
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS workers_max_atingido_ssh INTEGER
        """))
        await conn.execute(text("""
            ALTER TABLE log_scheduler
            ADD COLUMN IF NOT EXISTS tempo_sob_stress_seg INTEGER
        """))

        # Export diário do banco em .nxbak (criptografado) — config em Configuracao.
        # Defaults: enabled true, 03:30 local, remoto disabled (admin configura).
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_hour INTEGER NOT NULL DEFAULT 3
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_minute INTEGER NOT NULL DEFAULT 30
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_protocolo VARCHAR(8) NOT NULL DEFAULT 'sftp'
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_host VARCHAR(120)
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_porta INTEGER NOT NULL DEFAULT 22
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_user VARCHAR(120)
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_senha_enc TEXT
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_path VARCHAR(255) NOT NULL DEFAULT '/'
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_dia_semana INTEGER NOT NULL DEFAULT 0
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_hora INTEGER NOT NULL DEFAULT 4
        """))
        await conn.execute(text("""
            ALTER TABLE configuracoes
            ADD COLUMN IF NOT EXISTS db_export_remote_minute INTEGER NOT NULL DEFAULT 0
        """))

        # 8.0.2) empresas.telegram_chat_id (override do default global por empresa)
        await conn.execute(text("""
            ALTER TABLE empresas
            ADD COLUMN IF NOT EXISTS telegram_chat_id VARCHAR(40)
        """))

        # 8.1.x) devices: campos de FTP push (idempotente)
        # usuario_ssh precisa virar nullable — devices via ftp_push não têm SSH user.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='devices' AND column_name='usuario_ssh' AND is_nullable='NO'
                ) THEN
                    ALTER TABLE devices ALTER COLUMN usuario_ssh DROP NOT NULL;
                END IF;
            END$$;
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS ftp_user VARCHAR(64) UNIQUE
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS ftp_senha_enc TEXT
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS ftp_origem_cidr VARCHAR(64)
        """))

        # 8.1.api) devices.api_tls — usado quando protocolo='api' (RouterOS API).
        # false = porta 8728 plain; true = porta 8729 TLS. Adicionado em v1.4.0.
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS api_tls BOOLEAN NOT NULL DEFAULT FALSE
        """))

        # 8.1.manual) devices.backup_manual_apenas — quando TRUE, o scheduler
        # diário pula o device (só roda no clique manual). Default FALSE
        # preserva comportamento histórico de "todo device cadastrado roda às 02:00".
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS backup_manual_apenas BOOLEAN NOT NULL DEFAULT FALSE
        """))

        # 8.1) devices: campos de autenticação por chave SSH
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS auth_method authmethod NOT NULL DEFAULT 'password'
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS chave_privada_enc TEXT
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS chave_passphrase_enc TEXT
        """))
        # senha_ssh_enc precisa virar nullable (devices que usam chave não têm senha)
        await conn.execute(text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='devices' AND column_name='senha_ssh_enc' AND is_nullable='NO'
                ) THEN
                    ALTER TABLE devices ALTER COLUMN senha_ssh_enc DROP NOT NULL;
                END IF;
            END$$;
        """))

        # 9) devices.tipo (enum devicetipo, default 'roteador')
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'devicetipo') THEN
                    CREATE TYPE devicetipo AS ENUM ('roteador', 'olt', 'switch', 'wireless');
                END IF;
            END$$;
        """))
        await conn.execute(text("""
            ALTER TABLE devices
            ADD COLUMN IF NOT EXISTS tipo devicetipo NOT NULL DEFAULT 'roteador'
        """))

    await iniciar_scheduler()
    iniciar_ftp_server()
    iniciar_sftp_server()
    iniciar_tftp_server()
    yield
    scheduler.shutdown()
    parar_ftp_server()
    parar_sftp_server()
    parar_tftp_server()

app = FastAPI(title="NEXUS BACKUP", version=APP_VERSION, lifespan=lifespan)

RFC1918_REGEX = (
    r"http://(localhost|127\.0\.0\.1"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})"
    r"(:\d+)?"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=RFC1918_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(empresas.router)
app.include_router(devices.router)
app.include_router(backups.router)
app.include_router(settings.router)
app.include_router(logs.router)
app.include_router(atividades.router)
app.include_router(version.router)
app.include_router(info.router)

@app.get("/")
async def root():
    return {"status": "NEXUS BACKUP online", "version": APP_VERSION}
