# Publicar a ferramenta WhatsApp

Guia para colocar o WhatsApp em produção no Droplet e liberar para os clientes. Esta publicação **tem migração de banco** (`0013_whatsapp_service`). Por isso ela usa só alguns passos de [ATUALIZAR_API.md](ATUALIZAR_API.md), que cobre atualizações sem migração, e inclui aqui o backup e a migração. Detalhes técnicos, mapa dos dados e rollback completo estão em [WHATSAPP_POSTGRES.md](WHATSAPP_POSTGRES.md).

## Como funciona

```
Cliente (portal)                  Bot do cliente
      │  aceite, QR, webhook, chaves     │  envia mensagens (chave wp_...)
      ▼                                  ▼
 api.futuranext.com.br ── Caddy ─┬─ /api/...                   → API do portal (futuranext-api-1)
                                 └─ /whatsapp/api/v1/messages  → serviço WhatsApp (whatsapp-api)
 API do portal ── token de serviço ──► whatsapp-api:8000 (rede interna futuranext_default)
 serviço WhatsApp ── Evolution/Baileys ──► WhatsApp
 serviço WhatsApp e worker ── papéis wa_api / wa_worker ──► Supabase (schema privado whatsapp)
 worker ── webhook assinado (HMAC) ──► URL cadastrada pelo cliente
```

- **Código e containers:** o código fica na pasta `whatsapp/` deste repositório. Ele roda num projeto Compose separado, `futuranext-whatsapp`, em `/opt/futuranext/whatsapp`, com 6 containers: Redis, Postgres e Redis da Evolution, Evolution, backend e worker.
- **Onde ficam os dados:** os dados do serviço (instâncias, chaves, mensagens, webhooks) ficam no **Supabase do portal**, no schema privado `whatsapp`. A Evolution continua, **temporariamente**, com o PostgreSQL próprio no Droplet.
- **Rede e rotas públicas:** nenhuma porta nova é aberta. O backend entra na rede `futuranext_default` com o nome `whatsapp-api`. O Caddy publica **somente** `/whatsapp/api/v1/messages` e `/whatsapp/api/v1/instances`, que são as rotas usadas pelo bot do cliente.
- **Aceite e opt-out:** o **aceite** é do administrador da empresa que conecta o número. Ele recebe um e-mail com o link "Li e aceito", que só vale na sessão dele e expira em 24 horas. O serviço também confere o aceite no banco antes de mostrar o QR Code. Não há consentimento por destinatário. Quem responder **SAIR, PARAR, STOP ou CANCELAR** é bloqueado automaticamente.
- **Licença:** se a licença da ferramenta acabar no portal, as chaves param de funcionar e a fila da empresa é cancelada.
- **Limites iniciais:** 1 envio a cada 5 segundos por número e 100 envios por dia por empresa.

## Antes de começar

1. **Aumente o Droplet para pelo menos 4 GB de RAM e 2 vCPU.** No painel da DigitalOcean: Droplet › Resize › CPU and RAM only › escolha o plano › Resize. O Droplet desliga por alguns minutos, então faça fora do horário de uso. Depois, no console:

   ```bash
   free -h; nproc; df -h /
   ```

   ✅ `Mem: total` de 3,8 Gi ou mais, `nproc` igual a 2 ou mais e pelo menos 10 GB livres em `/`.

2. **O e-mail do portal (Zoho) precisa estar configurado**, porque o aceite chega por e-mail:

   ```bash
   cd /opt/futuranext/futuranext_portal
   grep -cE '^FN_ZOHO_MAIL_(CLIENT_ID|CLIENT_SECRET|REFRESH_TOKEN|ACCOUNT_ID|SENDER)=.+' .env.runtime
   ```

   ✅ `5`. Se der menos, configure o Zoho antes de continuar.

3. **Valide o banco de produção sem alterar nada.** No Supabase, abra **SQL Editor** e rode os `SELECT`s da [seção 4 de WHATSAPP_POSTGRES.md](WHATSAPP_POSTGRES.md#4-validação-somente-leitura-antes-de-migrar-sql-editor-de-produção).

   ✅ Revisão `0012_whatsapp_consent`, nenhum schema `whatsapp`/`evolution`, nenhum papel `wa_api`/`wa_worker` e o produto `whatsapp` ativo. Se algo for diferente, **pare** e envie o resultado.

4. **Confira o limite de conexões.** Em **Database › Settings › Connection pooling**, anote o **Pool Size**. O WhatsApp abre até 5 conexões (3 do backend e 2 do worker), além das da API do portal. Se o Pool Size for 15 ou menos, mantenha esses valores.

5. **Envie o código para o GitHub** pelo PowerShell do seu computador:

   ```powershell
   git add -A
   git commit -m "Serviço WhatsApp no PostgreSQL do portal"
   git push origin main
   ```

## Publicação (console do Droplet)

As regras do console são as mesmas de [ATUALIZAR_API.md](ATUALIZAR_API.md): é Linux, cole um bloco por vez e confira o ✅ antes de seguir. Se a conexão cair, recrie `release`, `short` e `image` como no fim daquele guia.

### W1. Passos 1, 2 e 3 do ATUALIZAR_API.md

Faça os **Passos 1, 2 e 3** de [ATUALIZAR_API.md](ATUALIZAR_API.md). Ao final você tem `$release`, `$short` e `$image`. **Não faça o Passo 4 daquele guia**: esta release tem migração, e ela é feita aqui.

### W2. Conferir a migração e fazer o backup do banco

```bash
migrate() {
  docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
    -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
    "$image" python -m alembic "$@"
}
migrate current
migrate heads
```

✅ A primeira saída é `0012_whatsapp_consent` e a segunda é `0013_whatsapp_service (head)`. Se a primeira não for `0012_whatsapp_consent`, **pare**.

Backup (obrigatório). Cole o bloco inteiro:

```bash
python3 - "$release" <<'PY'
from pathlib import Path
from sys import argv
from urllib.parse import unquote, urlsplit

entry = next(line.split("=", 1)[1] for line in Path(".env.migration").read_text().splitlines()
             if line.startswith("FN_MIGRATION_DATABASE_URL="))
url = urlsplit(entry.strip().strip(chr(34)).strip(chr(39)))
fields = {
    "PGHOST": url.hostname or "", "PGPORT": str(url.port or 5432),
    "PGDATABASE": unquote(url.path.lstrip("/")), "PGUSER": unquote(url.username or ""),
    "PGPASSWORD": unquote(url.password or ""), "PGSSLMODE": "require", "PGCONNECT_TIMEOUT": "10",
}
if not (fields["PGHOST"].endswith(".pooler.supabase.com") and fields["PGPORT"] == "5432"
        and fields["PGUSER"].startswith("postgres.") and fields["PGDATABASE"] == "postgres"
        and fields["PGPASSWORD"]):
    raise SystemExit("Confira o destino de produção da migração.")
target = Path(argv[1]) / "backup.env"
target.write_text("".join(f"{key}={value}\n" for key, value in fields.items()))
target.chmod(0o600)
PY

docker run --rm --network host --env-file "$release/backup.env" postgres:17-alpine \
  pg_dump --no-password --format=custom --lock-wait-timeout=10s --strict-names \
  --schema=auth --schema=futuranext --schema=futuranext_meta > "$release/pre-deploy.dump"
rm -f -- "$release/backup.env"
docker run --rm -i postgres:17-alpine pg_restore --list < "$release/pre-deploy.dump" | grep -c "TABLE DATA"
ls -lh "$release/pre-deploy.dump"
```

✅ Um número maior que zero e o arquivo `pre-deploy.dump` com tamanho maior que zero. Copie o arquivo para fora do Droplet.

❌ "Confira o destino de produção" ou qualquer erro: **pare**. O backup é a condição para migrar.

### W3. Criar a pasta do WhatsApp e localizar a CA do Supabase

```bash
install -d -m 700 /opt/futuranext/whatsapp
install -m 644 "$release/source/whatsapp/compose.yaml" /opt/futuranext/whatsapp/compose.yaml
ca=$(docker inspect futuranext-api-1 --format '{{range .Mounts}}{{if eq .Destination "/run/futuranext/supabase-ca.crt"}}{{.Source}}{{end}}{{end}}')
[ -z "$ca" ] && ca=$(ls /opt/futuranext/futuranext_portal/certs/supabase-ca.crt 2>/dev/null)
echo "CA: ${ca:-NAO_ENCONTRADA}"
[ -n "$ca" ] && openssl x509 -in "$ca" -noout -subject
```

✅ `CA: /caminho/supabase-ca.crt` e uma linha `subject=` do certificado.

❌ `CA: NAO_ENCONTRADA`: baixe a CA em **Supabase › Database Settings › SSL Configuration**, envie para `/opt/futuranext/futuranext_portal/certs/supabase-ca.crt` (como em [PRIMEIRA_PUBLICACAO.md](PRIMEIRA_PUBLICACAO.md)) e repita o bloco.

### W4. Gerar o `.env` do WhatsApp (somente na primeira vez)

O bloco cria `/opt/futuranext/whatsapp/.env` com senhas novas e não mostra nenhuma delas. As URLs dos papéis `wa_api` e `wa_worker` usam o mesmo host do Supabase que a API do portal (`FN_DATABASE_URL`). Se o arquivo já existir, ele não mexe em nada.

```bash
python3 - "$ca" <<'PY'
import base64, os, secrets
from pathlib import Path
from sys import argv
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

target = Path("/opt/futuranext/whatsapp/.env")
if target.exists():
    raise SystemExit("JA_EXISTE: mantendo os segredos atuais.")
runtime = Path("/opt/futuranext/futuranext_portal/.env.runtime").read_text().splitlines()
raw = next(line.split("=", 1)[1] for line in runtime if line.startswith("FN_DATABASE_URL="))
url = urlsplit(raw.strip().strip(chr(34)).strip(chr(39)).replace("postgresql+psycopg://", "postgresql://", 1))
user = unquote(url.username or "")
if not user.startswith("fn_app") or not url.hostname:
    raise SystemExit("ERRO: FN_DATABASE_URL inesperada. Envie a mensagem para análise.")
suffix = user[len("fn_app"):]  # ".REF" no Session pooler
query = urlencode({"sslmode": "verify-full", "sslrootcert": "/run/futuranext/supabase-ca.crt"})

def dsn(role, password):
    netloc = f"{role}{suffix}:{password}@{url.hostname}:{url.port or 5432}"
    return urlunsplit(("postgresql", netloc, url.path, query, ""))

hexa = lambda: secrets.token_hex(32)
api_password, worker_password = secrets.token_hex(24), secrets.token_hex(24)
values = {
    "WA_IMAGE": "futuranext-whatsapp:pendente",
    "WA_API_DATABASE_URL": f"'{dsn('wa_api', api_password)}'",
    "WA_WORKER_DATABASE_URL": f"'{dsn('wa_worker', worker_password)}'",
    "WA_API_DB_PASSWORD": api_password, "WA_WORKER_DB_PASSWORD": worker_password,
    "SUPABASE_CA_FILE": argv[1], "WA_API_POOL_SIZE": "3", "WA_WORKER_POOL_SIZE": "2",
    "POSTGRES_PASSWORD": hexa(), "REDIS_PASSWORD": hexa(), "EVOLUTION_REDIS_PASSWORD": hexa(),
    "EVOLUTION_API_KEY": hexa(), "AUTH_PEPPER": hexa(),
    "DATA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "PORTAL_SERVICE_TOKEN": hexa(),
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("".join(f"{key}={value}\n" for key, value in values.items()))
print("SEGREDOS_CRIADOS")
PY
sed -E "s/^([A-Z_]+=.{0,22}).*/\1.../" /opt/futuranext/whatsapp/.env | grep -E '^(WA_|SUPABASE)'
```

✅ `SEGREDOS_CRIADOS` (ou `JA_EXISTE` nas próximas vezes) e as linhas `WA_API_DATABASE_URL='postgresql://wa_api…` e `WA_WORKER_DATABASE_URL='postgresql://wa_worker…`.

> ⚠️ **Não apague nem regenere este arquivo depois que o WhatsApp subir.** Com `DATA_ENCRYPTION_KEY` ou `AUTH_PEPPER` diferentes, as mensagens, os segredos de webhook e as chaves gravados no banco deixam de ser lidos. Guarde uma cópia do arquivo no seu cofre de senhas.

### W5. Gerar a imagem do WhatsApp

```bash
wa_image="futuranext-whatsapp:$short"
docker build --platform=linux/amd64 --tag "$wa_image" "$release/source/whatsapp/backend"
sed -i "s|^WA_IMAGE=.*|WA_IMAGE=$wa_image|" /opt/futuranext/whatsapp/.env
grep '^WA_IMAGE=' /opt/futuranext/whatsapp/.env
```

✅ O build termina sem `ERROR` e a última linha mostra `WA_IMAGE=futuranext-whatsapp:<sha>`, a mesma versão da imagem da API.

---

> ⚠️ **Daqui em diante a produção é alterada.** Faça W6, W7 e W8 em sequência, na mesma sessão. Depois da migração (W6), a API do portal em execução aparece como `unhealthy` para o Docker até receber a imagem nova (W8), porque a checagem de prontidão exige a revisão exata. Ela continua atendendo durante esse intervalo.

### W6. Migrar o banco e ativar os papéis do WhatsApp

```bash
migrate upgrade head
migrate current
docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 --env-file .env.migration \
  --env-file <(grep -E '^WA_(API|WORKER)_DB_PASSWORD=' /opt/futuranext/whatsapp/.env) \
  "$image" python -m app.db_admin setup-whatsapp-roles && echo "PAPEIS_OK"
```

✅ `0013_whatsapp_service (head)` e `PAPEIS_OK`. A migração só **cria** o schema `whatsapp`, as tabelas, as funções e os papéis `wa_api`/`wa_worker`. Nenhuma tabela existente é alterada. Confira também os `SELECT`s "depois da migração" da [seção 4 de WHATSAPP_POSTGRES.md](WHATSAPP_POSTGRES.md#4-validação-somente-leitura-antes-de-migrar-sql-editor-de-produção).

❌ Erro na migração: nada foi aplicado, porque a migração roda numa transação. Envie a mensagem para análise antes de tentar de novo.

### W7. Subir o WhatsApp

```bash
docker network inspect futuranext_default --format 'REDE_OK {{.Name}}'
cd /opt/futuranext/whatsapp
docker compose --env-file .env -f compose.yaml config --quiet && echo "CONFIG_OK"
docker compose --env-file .env -f compose.yaml up -d --no-build --wait --wait-timeout 300
docker compose --env-file .env -f compose.yaml ps --format '{{.Name}} {{.Status}}'
cd /opt/futuranext/futuranext_portal
docker run --rm --network futuranext_default curlimages/curl:8.11.1 -fsS http://whatsapp-api:8000/health/ready; echo
```

✅ `REDE_OK futuranext_default`, `CONFIG_OK` sem `WARN`, os 6 containers `futuranext-whatsapp-*` com `(healthy)` e `{"status":"ready"}`. Na primeira vez as imagens (cerca de 1,5 GB) são baixadas e o comando leva alguns minutos.

❌ `backend` não fica saudável: `docker logs --tail 80 futuranext-whatsapp-backend-1`. `password authentication failed for user "wa_api"` indica senha diferente da ativada em W6: repita o último comando de W6. `MaxClientsInSessionMode` ou `too many clients` indica Pool Size do Supabase insuficiente: reduza `WA_API_POOL_SIZE`/`WA_WORKER_POOL_SIZE` ou aumente o Pool Size.

### W8. Publicar a API do portal com as variáveis do WhatsApp

1. Faça o **Passo 5** de [ATUALIZAR_API.md](ATUALIZAR_API.md) (preparar a configuração).
2. Acrescente as variáveis do WhatsApp:

   ```bash
   sed -i '/^FN_WHATSAPP_/d' "$release/env.runtime.next"
   wa_token=$(grep '^PORTAL_SERVICE_TOKEN=' /opt/futuranext/whatsapp/.env | cut -d= -f2)
   printf '%s\n' \
     "FN_WHATSAPP_SERVICE_URL=http://whatsapp-api:8000" \
     "FN_WHATSAPP_SERVICE_TOKEN=$wa_token" \
     "FN_WHATSAPP_PUBLIC_API_URL=https://api.futuranext.com.br/whatsapp" >> "$release/env.runtime.next"
   unset wa_token
   sed -E "s/^([A-Z_]+=.{0,12}).*/\1.../" "$release/env.runtime.next" | grep '^FN_WHATSAPP_'
   ```

   ✅ Três linhas `FN_WHATSAPP_`. O token é hexadecimal, sem `$`, e por isso não precisa de aspas.

3. Faça os **Passos 6, 7 e 8** de [ATUALIZAR_API.md](ATUALIZAR_API.md). No Passo 6, a linha `CONFIGURACAO_ORIGINAL_PRESERVADA` continua aparecendo, porque ela compara os arquivos antigos.

✅ `futuranext-api-1` `healthy` com a imagem `futuranext-api:<sha>` e as duas respostas de saúde com sucesso.

### W9. Atualizar o Caddy

```bash
caddyfile=$(docker inspect futuranext-proxy-1 --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
echo "CADDYFILE: $caddyfile"
cp "$caddyfile" "$release/Caddyfile.before"
docker cp "$release/source/infra/Caddyfile" futuranext-proxy-1:/tmp/Caddyfile.next
docker exec futuranext-proxy-1 caddy validate --config /tmp/Caddyfile.next --adapter caddyfile
```

✅ Mostra o caminho do arquivo e termina com `Valid configuration`.

Troque o arquivo e reinicie o proxy. O site fica 2 ou 3 segundos fora do ar:

```bash
install -m 644 "$release/source/infra/Caddyfile" "$caddyfile"
docker restart futuranext-proxy-1
sleep 3
curl -fsS https://api.futuranext.com.br/health/ready; echo
curl -s -o /dev/null -w 'WHATSAPP_PUBLICO %{http_code}\n' https://api.futuranext.com.br/whatsapp/api/v1/messages
curl -s -o /dev/null -w 'SERVICO_INTERNO %{http_code}\n' https://api.futuranext.com.br/whatsapp/service/v1/tenants/x/whatsapp
```

✅ `{"status":"ok"}`, `WHATSAPP_PUBLICO 401` (a rota existe e pede a chave do bot) e `SERVICO_INTERNO 404` (a rota interna não é publicada).

❌ Se `health/ready` falhar, volte o arquivo anterior com `install -m 644 "$release/Caddyfile.before" "$caddyfile" && docker restart futuranext-proxy-1`.

## Liberar para um cliente

1. **Licença:** no portal, em **Administração › Licenças**, ative **Conexão WhatsApp** para a empresa, por exemplo a Barbearia Stylus.
2. **Aceite:** o administrador da empresa abre **Ferramentas › WhatsApp** e clica em **Enviar e-mail de aceite**. No e-mail, ele clica em **Li e aceito** no mesmo navegador em que está conectado ao portal.
3. **Conexão:** clica em **Conectar WhatsApp**. No celular, abre o WhatsApp › Configurações › Aparelhos conectados › Conectar um aparelho e lê o QR Code. A tela mostra **Conectado** e o final do número.
4. **Webhook:** informa a URL HTTPS do bot e guarda o **segredo do webhook**, que aparece uma única vez.
5. **Chave de API:** cria uma chave e guarda o valor `wp_...`, que também aparece uma única vez.

Somente o administrador da empresa (`cliente_admin`) conecta, cadastra o webhook e cria chaves. Os outros usuários veem só a situação da conexão.

## Para o desenvolvedor do bot do cliente

**Enviar mensagem:**

```python
import os, uuid, httpx

resposta = httpx.post(
    "https://api.futuranext.com.br/whatsapp/api/v1/messages/send",
    headers={"Authorization": f"Bearer {os.environ['WHATSAPP_API_KEY']}"},
    json={"instance_id": os.environ["WHATSAPP_INSTANCE_ID"], "phone": "5511999999999",
          "message": "Olá!", "idempotency_key": str(uuid.uuid4())},
)
print(resposta.status_code, resposta.json())  # 202 = na fila
```

- Repita um envio com a **mesma** `idempotency_key` para não duplicar a mensagem.
- `GET /whatsapp/api/v1/messages/{id}` mostra o status: `sent`, `delivered`, `read`, `failed` ou `unknown`.
- `403 OPTED_OUT` significa que o destinatário pediu para sair. `409 NOT_CONNECTED` significa que o número está desconectado.

**Receber eventos no webhook.** Valide a assinatura antes de usar o corpo:

```python
import hashlib, hmac, os, time

def assinatura_valida(corpo: bytes, timestamp: str, assinatura: str) -> bool:
    segredo = os.environ["WHATSAPP_WEBHOOK_SECRET"].encode()
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
        return False
    esperado = "sha256=" + hmac.new(segredo, timestamp.encode() + b"." + corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura)

# Cabeçalhos: X-Timestamp, X-Signature, X-Event-Id. Responda 2xx depois de guardar o evento.
# Corpo: {"id", "type", "instance_id", "occurred_at", "data"}
#   message.received → data: phone, message, message_id
#   message.status   → data: status, message_id
#   instance.connection → data: state
```

Guarde o `id` de cada evento e ignore repetições, porque o worker pode reenviar o mesmo evento. Mais detalhes em [whatsapp/docs/OUTBOUND_WEBHOOKS.md](../whatsapp/docs/OUTBOUND_WEBHOOKS.md).

## Atualizar só o WhatsApp

Quando uma release muda apenas a pasta `whatsapp/` e **não traz migração nova**, faça os Passos 1 e 2 de [ATUALIZAR_API.md](ATUALIZAR_API.md) e depois o bloco abaixo. Se `migrate current` e `migrate heads` (W2) forem diferentes, siga a publicação completa, com backup e migração.

```bash
install -m 644 "$release/source/whatsapp/compose.yaml" /opt/futuranext/whatsapp/compose.yaml
wa_image="futuranext-whatsapp:$short"
docker build --platform=linux/amd64 --tag "$wa_image" "$release/source/whatsapp/backend"
sed -i "s|^WA_IMAGE=.*|WA_IMAGE=$wa_image|" /opt/futuranext/whatsapp/.env
cd /opt/futuranext/whatsapp
docker compose --env-file .env -f compose.yaml up -d --no-build --wait --wait-timeout 300 backend worker
cd /opt/futuranext/futuranext_portal
```

✅ `backend` e `worker` aparecem `Healthy`. Os números conectados continuam pareados, porque a sessão fica no volume da Evolution.

## Backup do WhatsApp

```bash
cd /opt/futuranext/whatsapp
backup="/opt/futuranext/backups/whatsapp-$(date +%Y%m%d-%H%M)"
install -d -m 700 "$backup"
docker compose --env-file .env -f compose.yaml exec -T evolution-postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$backup/evolution.dump"
install -m 600 .env "$backup/env"
ls -lh "$backup"
cd /opt/futuranext/futuranext_portal
```

✅ Dois arquivos com tamanho maior que zero. Copie a pasta para fora do Droplet.

Os dados do serviço (schema `whatsapp`) ficam no Supabase e entram nos backups do projeto. Para um dump avulso, use o bloco de backup do W2 com `--schema=whatsapp`. Guarde junto o arquivo `env`: sem `DATA_ENCRYPTION_KEY` e `AUTH_PEPPER`, as mensagens e os segredos do backup não podem ser lidos.

## Voltar atrás

Do mais simples para o mais drástico. Detalhes em [WHATSAPP_POSTGRES.md](WHATSAPP_POSTGRES.md#5-plano-de-implantação).

1. **Caddy:** `install -m 644 "$release/Caddyfile.before" "$caddyfile" && docker restart futuranext-proxy-1`.
2. **Serviço WhatsApp:** `cd /opt/futuranext/whatsapp && docker compose --env-file .env -f compose.yaml down`. Sem `-v`, os volumes ficam.
3. **API do portal e banco:** a imagem anterior exige a revisão `0012`. Para voltar a ela:
   1. Rode `migrate downgrade 0012_whatsapp_consent`. Isso remove o schema `whatsapp` e **apaga os dados do serviço**.
   2. Execute `ALTER ROLE wa_api NOLOGIN; ALTER ROLE wa_worker NOLOGIN;` no SQL Editor.
   3. Use "Se precisar voltar à imagem anterior" de [ATUALIZAR_API.md](ATUALIZAR_API.md).
   4. Em último caso, restaure `"$release/pre-deploy.dump"`.

## Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| Tela "sendo preparada pela FuturaNext" | Faltam as variáveis `FN_WHATSAPP_*` na API do portal. Refaça W8 |
| `futuranext-api-1` `unhealthy` logo após W6 | Esperado até a API nova subir (W8). Se continuar depois do W8, confira `migrate current` (`0013_whatsapp_service`) |
| `whatsapp-api` não fica pronto | `docker logs --tail 80 futuranext-whatsapp-backend-1`. Senha do `wa_api`: repita o último comando do W6. `MaxClientsInSessionMode`: reduza `WA_API_POOL_SIZE`/`WA_WORKER_POOL_SIZE` ou aumente o Pool Size no Supabase |
| "A ferramenta WhatsApp não está liberada" | Licença inativa ou vencida. Ative **Conexão WhatsApp** em **Administração › Licenças** |
| "Aceite os termos de uso pelo link enviado por e-mail" | Quem clicou em conectar ainda não aceitou os termos. O aceite vale por usuário |
| "Serviço de WhatsApp indisponível" | `docker compose --env-file .env -f compose.yaml ps` em `/opt/futuranext/whatsapp`. Veja `docker logs --tail 80 futuranext-whatsapp-backend-1` |
| E-mail de aceite não chega | Confira a caixa de spam e as variáveis `FN_ZOHO_MAIL_*`. Depois de 2 minutos é possível reenviar |
| "Este link de aceite não vale para a sua conta" | O link foi aberto com outra conta ou outro e-mail foi pedido depois. Envie um novo e-mail e abra o mais recente |
| QR Code não aparece | Veja `docker logs --tail 80 futuranext-whatsapp-evolution-1`. Clique em **Gerar QR Code** de novo (até 3 vezes por minuto) |
| Bot recebe `401` | Chave revogada, expirada ou copiada errada. Crie outra no portal |
| Webhook não recebe eventos | A URL precisa ser HTTPS pública, com certificado válido, porta 443 e sem `?`. Veja `docker logs --tail 80 futuranext-whatsapp-worker-1` |
| Droplet lento ou containers reiniciando | `free -h` e `docker stats --no-stream`. Aumente a memória do Droplet |

## Segurança

- `/opt/futuranext/whatsapp/.env` contém todas as senhas do serviço, inclusive as dos papéis `wa_api` e `wa_worker` no Supabase. Mantenha-o com permissão `600` e guarde uma cópia no cofre.
- `wa_api` e `wa_worker` não têm `BYPASSRLS` nem acesso ao schema `futuranext`. O schema `whatsapp` não deve ser adicionado aos **Exposed schemas** da Data API do Supabase.
- Para trocar a senha de um papel, altere `WA_*_DB_PASSWORD` e a URL correspondente no `.env`. Depois repita o último comando do W6 e recrie `backend` e `worker`.
- O `PORTAL_SERVICE_TOKEN` só existe nesse arquivo e em `FN_WHATSAPP_SERVICE_TOKEN`. Para trocá-lo, altere os dois e recrie `backend`, `worker` e a API do portal.
- Os segredos de webhook e as chaves `wp_...` aparecem uma única vez no portal. A FuturaNext guarda só o hash da chave e o segredo criptografado.
- O WhatsApp Web não é oficial. Use somente números autorizados pela empresa e evite envios em massa.
- Licença da Evolution API: [whatsapp/docs/third-party/EVOLUTION-LICENSE-2.3.7.txt](../whatsapp/docs/third-party/EVOLUTION-LICENSE-2.3.7.txt).
