from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import text
from database import engine, Base
from routers import auth, users, devices, backups, settings, logs, empresas, atividades
from services.scheduler import iniciar_scheduler, scheduler

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
    yield
    scheduler.shutdown()

app = FastAPI(title="NEXUS BETA - Backup Manager", version="1.0.0", lifespan=lifespan)

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

@app.get("/")
async def root():
    return {"status": "NEXUS BETA online"}
