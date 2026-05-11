# Formato `.nxbak` — NEXUS BACKUP Export v2.0.0

Especificação completa do arquivo proprietário gerado diariamente pelo
NEXUS BACKUP. Serve de referência pra implementar a ferramenta externa de
leitura (que abre o `.nxbak` fora da instância NEXUS).

## Por que existe

O `.nxbak` é um **backup do banco de backups**. Cada arquivo é um snapshot
completo de todas as configurações de equipamentos coletadas até aquele
momento. Se o servidor NEXUS queimar/corromper, o admin pode:

1. Levar a chave Fernet (`DB_EXPORT_KEY` do `.env`) e o `.nxbak` mais recente
   pra outra máquina.
2. Abrir com a ferramenta externa de leitura.
3. Ver/exportar/imprimir cada backup individual de cada equipamento.

Não restaura a instância NEXUS — restaura o **conteúdo** dos backups.

## Como o arquivo é gerado

1. Job APScheduler diário (default 03:30 local).
2. Lê do banco: todas as `empresas`, `devices` (sem senhas cifradas) e
   `backups` (com conteúdo completo).
3. Serializa em JSON UTF-8.
4. Comprime com gzip nível 9.
5. Criptografa com Fernet usando `DB_EXPORT_KEY`.
6. Escreve no disco com magic header binário.
7. Aplica retenção: mantém só os últimos 7 arquivos.

1× por semana (default domingo 04:00) o mais recente é enviado pra um
servidor remoto via SFTP ou FTP (configurável).

## Layout binário

```
offset 0    : 16 bytes  magic ASCII "NEXUSBACKUPv200\n"
offset 16   :  1 byte   format_version (0x01)
offset 17   :  8 bytes  payload_length (uint64 big-endian)
offset 25   :  N bytes  Fernet token (string base64, tratada como bytes)
```

**Total**: 25 bytes de header + tamanho do Fernet token.

### Magic header

Bytes literais `4E 45 58 55 53 42 41 43 4B 55 50 76 32 30 30 0A`.
String: `NEXUSBACKUPv200\n` (15 chars + newline = 16 bytes).

Tudo o que não começa com esses 16 bytes não é `.nxbak` — abortar leitura.

### Format version

Apenas `0x01` no momento. Reservado pra futuras evoluções do formato. Ferramenta
de leitura deve recusar versões desconhecidas pra evitar interpretar errado.

### Payload length

8 bytes big-endian — tamanho exato do token Fernet em bytes. Fernet emite
saída textual (base64 URL-safe), então `payload_length` é o `len()` da string
quando lida como bytes (UTF-8).

### Fernet token

Estrutura interna do token Fernet (padrão da lib Python `cryptography`):

```
b'gAAAAA' + <86 chars> = base64 de:
  version (1 byte = 0x80) + timestamp (8 bytes) + IV (16 bytes)
  + ciphertext (variável, AES-128-CBC)
  + HMAC SHA256 (32 bytes)
```

Não precisa parsear manualmente — usar `Fernet.decrypt(token)` da lib.

## Pseudo-código pra ler

```python
import struct, gzip, json
from cryptography.fernet import Fernet

def ler_nxbak(caminho: str, chave_fernet: str) -> dict:
    with open(caminho, "rb") as f:
        magic = f.read(16)
        if magic != b"NEXUSBACKUPv200\n":
            raise ValueError("Arquivo não é .nxbak válido")
        version = f.read(1)[0]
        if version != 0x01:
            raise ValueError(f"format_version desconhecida: {version}")
        (payload_length,) = struct.unpack(">Q", f.read(8))
        token = f.read(payload_length)

    fernet = Fernet(chave_fernet.encode())
    compressed = fernet.decrypt(token)
    payload = gzip.decompress(compressed)
    return json.loads(payload.decode("utf-8"))
```

## Payload JSON (após decifrar e descomprimir)

```json
{
  "metadata": {
    "format_version": 1,
    "exported_at": "2026-05-11T03:30:00.123456+00:00",
    "instance_id": "nexuscamon-1a2b3c4d",
    "total_empresas": 3,
    "total_devices": 47,
    "total_backups": 312
  },
  "empresas": [
    {
      "id": 1,
      "nome": "Provedor X",
      "cnpj": "00.000.000/0001-00",
      "criado_em": "2026-04-01T12:00:00+00:00"
    }
  ],
  "devices": [
    {
      "id": 12,
      "nome": "CRS328-Asa_Norte",
      "ip": "172.25.0.190",
      "porta": 22,
      "fabricante": "mikrotik_v7",
      "tipo": "switch",
      "protocolo": "api",
      "empresa_id": 1,
      "criado_em": "2026-04-15T10:30:00+00:00"
    }
  ],
  "backups": [
    {
      "id": 4521,
      "device_id": 12,
      "status": "sucesso",
      "origem": "scheduler",
      "nome_arquivo": null,
      "log_scheduler_id": 89,
      "duracao_segundos": 8,
      "criado_em": "2026-05-10T02:03:15+00:00",
      "conteudo": "# 2026-05-10 02:03:15...\n/interface bridge\nadd name=...\n",
      "erro": null
    }
  ]
}
```

### Notas sobre os campos

- **`empresas`**: contexto pra agrupar devices. Sem `telegram_chat_id` (config
  interna da instância, não-restaurável fora dela).
- **`devices`**: identidade e configuração de coleta. **SEM senhas SSH/FTP
  cifradas** — sem a `ENCRYPTION_KEY` local elas seriam inúteis na ferramenta
  externa, então não vão no payload.
- **`backups.conteudo`**:
  - Strings normais (CLI textual): conteúdo direto, UTF-8.
  - **Conteúdo binário** (UNM2000 .zip, etc.): prefixado com `BASE64:` —
    decodificar `conteudo[7:]` com `base64.b64decode()` pra obter o blob original.
- **`backups.erro`**: traceback ou mensagem curta quando `status="falha"`.
  Quando sucesso, é `null`.
- **`backups.duracao_segundos`**: tempo de coleta (null em backups pré-v2 ou
  em backups recebidos via push).

## Tamanho típico

| Cenário | Backups armazenados | .nxbak (depois de gzip+Fernet) |
|---|---|---|
| 10 devices × 7 backups, configs ~50KB texto | 70 | ~2-5 MB |
| 50 devices × 7 backups | 350 | ~15-25 MB |
| 200 devices × 7 backups + alguns .zip UNM2000 | 1400+ | ~80-150 MB |

Gzip nível 9 + texto repetitivo de configs dá ratio de compressão de ~10:1
na prática.

## Segurança

- **Chave**: `DB_EXPORT_KEY` é Fernet, 32 bytes URL-safe base64. Gerar com:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- **Separada da ENCRYPTION_KEY** propositalmente. Se vazar uma, a outra
  mantém a defesa em profundidade.
- **Local de guarda**: NÃO deixar a chave junto do `.nxbak` no mesmo lugar —
  isso anula a criptografia. Guardar em password manager, cofre digital,
  ou impressa no cofre físico.
- **Recuperação**: se perder a chave, o `.nxbak` é **irrecuperável**. Sem
  backdoor, sem master key, sem reset.

## Versionamento do formato

Mudanças de schema (campos novos no JSON) NÃO mudam o `format_version` —
ferramenta de leitura deve usar `.get()` com defaults pra ser resiliente.

Mudanças no **layout binário** (magic, header, criptografia) BUMPAM o
`format_version`. Versão atual: `0x01`.

Toda nova versão do NEXUS BACKUP que mude o formato bumpará a major
version do produto também (ex.: NEXUS BACKUP v3.0.0 = `.nxbak`
format_version 0x02).
