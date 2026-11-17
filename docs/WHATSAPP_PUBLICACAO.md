# Publicar a ferramenta WhatsApp

Guia para colocar o WhatsApp em produção no Droplet e liberar para os clientes. Use junto com [ATUALIZAR_API.md](ATUALIZAR_API.md): este guia diz **em que ponto** daquele procedimento entram os passos do WhatsApp.

## Como funciona

```
Cliente (portal)                  Bot do cliente
      │  aceite, QR, webhook, chaves     │  envia mensagens (chave wp_...)
      ▼                                  ▼
 api.futuranext.com.br ── Caddy ─┬─ /api/...                   → API do portal (futuranext-api-1)
                                 └─ /whatsapp/api/v1/messages  → serviço WhatsApp (whatsapp-api)
 API do portal ── token de serviço ──► whatsapp-api:8000 (rede interna futuranext_default)
 serviço WhatsApp ── Evolution/Baileys ──► WhatsApp
 worker ── webhook assinado (HMAC) ──► URL cadastrada pelo cliente
```

- O código fica na pasta `whatsapp/` deste repositório. Ele roda num projeto Compose separado, `futuranext-whatsapp`, em `/opt/futuranext/whatsapp`, com 7 containers: Mongo, Redis, Postgres e Redis da Evolution, Evolution, backend e worker.
- Nenhuma porta nova é aberta. O backend entra na rede `futuranext_default` com o nome `whatsapp-api`. O Caddy publica **somente** `/whatsapp/api/v1/messages` e `/whatsapp/api/v1/instances`, que são as rotas usadas pelo bot do cliente.
- O **aceite** é do administrador da empresa que conecta o número: ele recebe um e-mail com o link "Li e aceito". O link só vale na sessão dele e expira em 24 horas. Não há consentimento por destinatário. Quem responder **SAIR, PARAR, STOP ou CANCELAR** é bloqueado automaticamente.
- Limites iniciais do serviço: 1 envio a cada 5 segundos por número e 100 envios por dia por empresa.

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

3. **Envie o código para o GitHub** pelo PowerShell do seu computador:

   ```powershell
   git add -A
   git commit -m "Ferramenta WhatsApp integrada ao portal"
   git push origin main
   ```

## Publicação (console do Droplet)

As regras do console são as mesmas de [ATUALIZAR_API.md](ATUALIZAR_API.md#regras-do-console): é Linux, cole um bloco por vez e confira o ✅ antes de seguir.

### W1. Passos 1 a 5 do ATUALIZAR_API.md

Faça os **Passos 1, 2, 3, 4 e 5** de [ATUALIZAR_API.md](ATUALIZAR_API.md). Ao final você tem `$release`, `$short`, `$image`, a função `migrate` e o backup.

✅ No Passo 4, `migrate heads` mostra `0012_whatsapp_consent (head)`.

### W2. Criar a pasta do WhatsApp

```bash
install -d -m 700 /opt/futuranext/whatsapp /opt/futuranext/whatsapp/infra
install -m 644 "$release/source/whatsapp/compose.yaml" /opt/futuranext/whatsapp/compose.yaml
install -m 644 "$release/source/whatsapp/infra/mongo-init.js" /opt/futuranext/whatsapp/infra/mongo-init.js
ls -l /opt/futuranext/whatsapp /opt/futuranext/whatsapp/infra
```

✅ Aparecem `compose.yaml` e `infra/mongo-init.js`.

### W3. Gerar os segredos do WhatsApp (somente na primeira vez)

O bloco cria `/opt/futuranext/whatsapp/.env` com senhas novas e não mostra nenhuma delas. Se o arquivo já existir, ele não mexe em nada.

```bash
python3 - <<'PY'
import base64, os, secrets
from pathlib import Path

target = Path("/opt/futuranext/whatsapp/.env")
if target.exists():
    raise SystemExit("JA_EXISTE: mantendo os segredos atuais.")
hexa = lambda: secrets.token_hex(32)
values = {
    "MONGO_USERNAME": "platform_root", "MONGO_PASSWORD": hexa(),
    "POSTGRES_PASSWORD": hexa(), "REDIS_PASSWORD": hexa(), "EVOLUTION_REDIS_PASSWORD": hexa(),
    "EVOLUTION_API_KEY": hexa(), "APP_MONGO_PASSWORD": hexa(), "AUTH_PEPPER": hexa(),
    "DATA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "PORTAL_SERVICE_TOKEN": hexa(), "WA_IMAGE": "futuranext-whatsapp:pendente",
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("".join(f"{key}={value}\n" for key, value in values.items()))
print("SEGREDOS_CRIADOS")
PY
```

✅ `SEGREDOS_CRIADOS` na primeira vez, ou `JA_EXISTE` nas próximas.

> ⚠️ **Não apague nem regenere este arquivo depois que o WhatsApp subir.** As senhas ficam gravadas nos volumes na primeira subida. Com `DATA_ENCRYPTION_KEY` diferente, as sessões e mensagens guardadas ficam ilegíveis. Guarde uma cópia do arquivo no seu cofre de senhas.

### W4. Gerar a imagem do WhatsApp

```bash
wa_image="futuranext-whatsapp:$short"
docker build --platform=linux/amd64 --tag "$wa_image" "$release/source/whatsapp/backend"
sed -i "s|^WA_IMAGE=.*|WA_IMAGE=$wa_image|" /opt/futuranext/whatsapp/.env
grep '^WA_IMAGE=' /opt/futuranext/whatsapp/.env
```

✅ O build termina sem `ERROR` e a última linha mostra `WA_IMAGE=futuranext-whatsapp:<sha>`.

### W5. Subir o WhatsApp

```bash
docker network inspect futuranext_default --format 'REDE_OK {{.Name}}'
cd /opt/futuranext/whatsapp
docker compose --env-file .env -f compose.yaml config --quiet && echo "CONFIG_OK"
docker compose --env-file .env -f compose.yaml up -d --no-build --wait --wait-timeout 300
docker compose --env-file .env -f compose.yaml ps --format '{{.Name}} {{.Status}}'
cd /opt/futuranext/futuranext_portal
```

✅ `REDE_OK futuranext_default`, `CONFIG_OK` sem `WARN` e os 7 containers `futuranext-whatsapp-*` com `(healthy)`. Na primeira vez as imagens (cerca de 1,5 GB) são baixadas e o comando leva alguns minutos.

❌ `network futuranext_default not found`: rode `docker network ls | grep futuranext` e me envie o resultado.

Confira se a API do portal enxerga o serviço:

```bash
docker run --rm --network futuranext_default curlimages/curl:8.11.1 -fsS http://whatsapp-api:8000/health/ready; echo
```

✅ `{"status":"ready"}`.

### W6. Variáveis novas da API do portal

Faça o **Passo 6** de [ATUALIZAR_API.md](ATUALIZAR_API.md). No lugar do Passo 7, cole:

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

### W7. Passos 8 a 11 do ATUALIZAR_API.md

Faça os **Passos 8, 9, 10 e 11** de [ATUALIZAR_API.md](ATUALIZAR_API.md).

✅ No Passo 9, `migrate current` mostra `0012_whatsapp_consent (head)`. No Passo 11, as duas respostas de saúde dão sucesso.

### W8. Atualizar o Caddy

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

Quando uma release muda apenas a pasta `whatsapp/`, faça os Passos 1 e 2 de [ATUALIZAR_API.md](ATUALIZAR_API.md) e depois:

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
docker compose --env-file .env -f compose.yaml exec -T mongo sh -c \
  'mongodump --quiet --archive --gzip -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' > "$backup/mongo.archive.gz"
docker compose --env-file .env -f compose.yaml exec -T evolution-postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$backup/evolution.dump"
install -m 600 .env "$backup/env"
ls -lh "$backup"
cd /opt/futuranext/futuranext_portal
```

✅ Três arquivos com tamanho maior que zero. Copie a pasta para fora do Droplet. Sem o arquivo `env`, o backup do Mongo não pode ser lido, porque os dados estão criptografados com `DATA_ENCRYPTION_KEY`.

## Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| Tela "sendo preparada pela FuturaNext" | Faltam as variáveis `FN_WHATSAPP_*` na API do portal. Refaça W6 e W7 |
| "Serviço de WhatsApp indisponível" | `docker compose --env-file .env -f compose.yaml ps` em `/opt/futuranext/whatsapp`. Veja `docker logs --tail 80 futuranext-whatsapp-backend-1` |
| E-mail de aceite não chega | Confira a caixa de spam e as variáveis `FN_ZOHO_MAIL_*`. Depois de 2 minutos é possível reenviar |
| "Este link de aceite não vale para a sua conta" | O link foi aberto com outra conta ou outro e-mail foi pedido depois. Envie um novo e-mail e abra o mais recente |
| QR Code não aparece | Veja `docker logs --tail 80 futuranext-whatsapp-evolution-1`. Clique em **Gerar QR Code** de novo (até 3 vezes por minuto) |
| Bot recebe `401` | Chave revogada, expirada ou copiada errada. Crie outra no portal |
| Webhook não recebe eventos | A URL precisa ser HTTPS pública, com certificado válido, porta 443 e sem `?`. Veja `docker logs --tail 80 futuranext-whatsapp-worker-1` |
| Droplet lento ou containers reiniciando | `free -h` e `docker stats --no-stream`. Aumente a memória do Droplet |

## Segurança

- `/opt/futuranext/whatsapp/.env` contém todas as senhas do serviço. Mantenha-o com permissão `600` e guarde uma cópia no cofre.
- O `PORTAL_SERVICE_TOKEN` só existe nesse arquivo e em `FN_WHATSAPP_SERVICE_TOKEN`. Para trocá-lo, altere os dois e recrie `backend`, `worker` e a API do portal.
- Os segredos de webhook e as chaves `wp_...` aparecem uma única vez no portal. A FuturaNext guarda só o hash da chave e o segredo criptografado.
- O WhatsApp Web não é oficial. Use somente números autorizados pela empresa e evite envios em massa.
- Licença da Evolution API: [whatsapp/docs/third-party/EVOLUTION-LICENSE-2.3.7.txt](../whatsapp/docs/third-party/EVOLUTION-LICENSE-2.3.7.txt).
