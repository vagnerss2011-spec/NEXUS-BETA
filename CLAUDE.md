# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projeto

**NEXUS BETA — Backup Manager**: painel web para gerenciar backups de equipamentos de rede (Huawei, MikroTik, Ubiquiti, Intelbras) via SSH, com retenção de 7 dias por dispositivo.

## Como rodar

### Backend
```bash
cd backend
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm run dev
```

### Acessar
- Painel: http://localhost:5173 (ou http://192.168.1.254:5173 na rede local)
- API Docs: http://localhost:8000/docs

## Credenciais de desenvolvimento

- **Usuário admin:** vagnerss2011@gmail.com / Admin@2025
- **PostgreSQL:** localhost:5432 / banco: `dbnexus` / user: `postgres`
- **Senha do banco:** no arquivo `backend/.env` (não commitado)

## Configuração obrigatória

Copiar `backend/.env.example` para `backend/.env` e preencher:

```
DATABASE_URL=postgresql+asyncpg://postgres:SENHA@localhost:5432/dbnexus
SECRET_KEY=chave-longa-aleatoria
ENCRYPTION_KEY=chave-fernet-base64  # gerar com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
BACKUP_RETENTION_DAYS=7
```

## Arquitetura

```
NEXUS-BETA/
├── backend/                  # FastAPI + PostgreSQL
│   ├── main.py               # app, CORS RFC1918, lifespan (cria tabelas + scheduler)
│   ├── models.py             # User, Device, Backup (SQLAlchemy)
│   ├── auth.py               # JWT + bcrypt, require_role()
│   ├── config.py             # Settings via pydantic-settings (.env)
│   ├── database.py           # engine async + get_db()
│   ├── routers/              # auth, users, devices, backups
│   └── services/
│       ├── ssh_service.py    # Paramiko — executa export por fabricante
│       ├── scheduler.py      # APScheduler — backup diário às 02:00
│       └── crypto.py         # Fernet — criptografa senhas SSH no banco
└── frontend/                 # React + Tailwind + Vite
    └── src/
        ├── pages/            # Login, Dashboard, Devices, Backups, Users
        ├── components/       # Layout, Sidebar, StatusBadge
        └── services/api.js   # axios com interceptor JWT
```

## Comandos SSH por fabricante

| Fabricante | Comando |
|---|---|
| MikroTik | `/export` |
| Huawei | `display current-configuration` |
| Ubiquiti | `cat /tmp/system.cfg` |
| Intelbras / Outro | `show running-config` |

## Roles e permissões

| Role | Pode |
|---|---|
| admin | tudo — usuários, dispositivos, backups |
| operador | adicionar dispositivos, rodar backups manuais |
| viewer | somente visualizar |

## Dependências principais

- **Backend:** FastAPI, SQLAlchemy async, asyncpg, Paramiko, APScheduler, python-jose, passlib+bcrypt==4.0.1, cryptography
- **Frontend:** React 18, React Router 6, Axios, Tailwind CSS 3, Lucide React, Vite 5

## Firewall (rede local)

Portas 5173 e 8000 abertas no Windows Firewall para faixas RFC1918:
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`

CORS do backend aceita qualquer origin RFC1918 via regex em `main.py`.

## Estado atual do projeto

- [x] Backend completo e funcionando
- [x] Frontend completo com todas as páginas
- [x] PostgreSQL instalado e banco `dbnexus` criado
- [x] Usuário admin criado no banco
- [x] Acessível na rede local (192.168.1.254)
- [x] Código salvo no GitHub (branch: `backup`)
- [ ] Configurar HTTPS / proxy reverso (nginx) para produção
- [ ] Adicionar notificações de falha por e-mail/webhook
