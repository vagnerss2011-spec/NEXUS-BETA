# Releasing — NEXUS BACKUP

Como cortar uma nova versão e como aplicar uma versão num servidor.

## Conceito

- **Branch mainline:** `backup` (mantido por motivo histórico — não é `main`).
- **Releases:** tags semver `vMAJOR.MINOR.PATCH` (ex.: `v1.0.0`, `v1.1.0`, `v1.0.1`).
- **Servidor "canário":** novo servidor (a provisionar). Pode rodar HEAD do branch ou tag pré-release (`v1.2.0-rc1`). Valida com tráfego real antes de promover pra outras instâncias.
- **Servidor estável:** `backup.bandaa.net.br`. Só recebe tag depois de validada no canário.

## Política de bump

| Tipo | Quando |
|---|---|
| **MAJOR** (`v2.0.0`) | Quebra compatibilidade: rename/drop de coluna do banco, mudança de API que exige rebuild do frontend, mudança de variável obrigatória do `.env`. |
| **MINOR** (`v1.1.0`) | Novo fabricante, novo tipo de device, nova feature visível no painel. |
| **PATCH** (`v1.0.1`) | Bugfix, ajuste de UI, hotfix de segurança que não muda contrato. |

Em dúvida, prefira MAJOR — bump excessivo não machuca, MAJOR escondido como MINOR machuca.

---

## Cortar uma nova versão

### 1. Atualizar `frontend/package.json`

Bump do campo `"version"` segundo a política acima.

```bash
# Exemplo: vai sair v1.1.0
sed -i 's/"version": "1.0.0"/"version": "1.1.0"/' frontend/package.json
```

### 2. Atualizar `CHANGELOG.md`

Mover as linhas de `[Não lançado]` para uma nova seção `[1.1.0] - YYYY-MM-DD`. Garantir que cada item está na seção certa (Adicionado / Mudado / Corrigido / Segurança / Removido).

Atualizar os links no rodapé:
```markdown
[Não lançado]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/vagnerss2011-spec/NEXUS-BETA/compare/v1.0.0...v1.1.0
```

### 3. Commitar a release

```bash
git add frontend/package.json CHANGELOG.md
git commit -m "release: v1.1.0"
```

### 4. Criar a tag anotada e empurrar

```bash
git tag -a v1.1.0 -m "v1.1.0 — <resumo de 1 linha do que mudou>"
git push origin backup
git push origin v1.1.0
```

> **Por que tag anotada (`-a`)** e não lightweight: anotada carrega autor, data e mensagem — vira um objeto rastreável no repo. Lightweight é só um ponteiro.

### 5. (Opcional) Criar a release no GitHub

```bash
gh release create v1.1.0 --notes-from-tag
```

---

## Aplicar uma versão num servidor (deploy)

> **Pré-requisito:** o servidor já tem `git`, `docker`, `docker-compose-v2`, e o repo está clonado em `/root/NEXUS-BETA` (ou onde for) com `.env` preenchido.

### Promoção normal

```bash
cd /root/NEXUS-BETA

# Backup do banco antes de promover (sempre)
docker compose exec db pg_dump -U $POSTGRES_USER -d $POSTGRES_DB \
  | gzip > /root/backups/db-pre-v1.1.0-$(date +%Y%m%d-%H%M).sql.gz

# Puxar tag
git fetch --tags
git checkout v1.1.0

# Rebuild + restart
docker compose up -d --build

# Se mudou faixa Docker ou daemon.json, lembrar:
systemctl restart fail2ban
```

### Validar pós-deploy

```bash
# Containers up
docker compose ps

# Versão exibida na UI bate com a tag
curl -s https://backup.bandaa.net.br/ | grep -oP 'v\d+\.\d+\.\d+' | head -1

# Backend respondendo
curl -sf https://backup.bandaa.net.br/api/health || echo "FAIL"

# Logs sem erro de migration
docker compose logs backend --tail=50 | grep -iE 'error|traceback'
```

### Rollback rápido

```bash
cd /root/NEXUS-BETA
git checkout v1.0.0           # tag anterior
docker compose up -d --build

# Se a tag nova rodou migration de schema (rename/drop), rollback do código
# NÃO desfaz schema. Restaurar o banco do dump pré-deploy:
gunzip < /root/backups/db-pre-v1.1.0-YYYYMMDD-HHMM.sql.gz \
  | docker compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB
```

---

## Pré-releases (canário)

Quando uma feature precisa de validação em prod-real antes de virar tag estável:

```bash
git tag -a v1.2.0-rc1 -m "v1.2.0-rc1 — testando suporte a Datacom"
git push origin v1.2.0-rc1
```

Deploy só no servidor canário. Após N dias estável:

```bash
# rc vira release final — tag nova apontando pro mesmo commit
git tag -a v1.2.0 v1.2.0-rc1 -m "v1.2.0 — Datacom validado em prod"
git push origin v1.2.0
```

---

## Checklist de release

- [ ] `frontend/package.json` versão batida com a tag
- [ ] `CHANGELOG.md` atualizado, seção `[Não lançado]` esvaziada
- [ ] Migrations testadas em DB com dados (não só DB zerado)
- [ ] Tag anotada (`-a`) e pushed
- [ ] Servidor canário deployado e validado por ≥ 24h antes de promover prod estável
- [ ] Dump do `pg_dump` arquivado antes de cada promoção
