# Atualizar a API no Droplet: passo a passo

Guia operacional para publicar uma nova versão da API em `https://api.futuranext.com.br`. Ele usa o console web do DigitalOcean e foi validado na publicação do commit `13e5d69` (migração `0011_billing_customer` e chave do Asaas) em 17/11/2026.

O frontend (Cloudflare Pages) é publicado sozinho pelo `git push` na `main`. **A API não.** Toda alteração em `backend/`, `compose.yaml`, `Dockerfile` ou nas migrações precisa deste procedimento.

## Antes de começar

| Item | Como conferir |
| --- | --- |
| Código publicado | `git push origin main` feito e CI verde no GitHub |
| Acesso ao servidor | Painel DigitalOcean → Droplet `futuranext-prod` → **Access → Launch Droplet Console** (usuário `root`) |
| Segredos novos (se houver) | Valores copiados para um bloco de notas local. Nunca em print, chat ou commit |

### Regras do console

- O console do Droplet é **Linux (bash)**. Comandos de PowerShell (`$chave = ...`, `$env:USERPROFILE`, `scp -i $chave`) são do Windows e **não funcionam aqui**. No console web não é preciso `ssh` nem `scp`.
- Para colar, use o **botão direito → Colar** ou **Ctrl+Shift+V**.
- Cole **um bloco por vez** e confira o resultado esperado (✅) antes de seguir.
- Se algo diferente aparecer, **pare**. Até o Passo 8 nada mudou em produção.
- As variáveis (`$release`, `$image`, a função `migrate`) existem só na sessão aberta. Se o console desconectar, use [Retomar após desconexão](#retomar-após-desconexão).
- Os Passos 9 e 10 devem ser feitos **em seguida, sem pausa**. Entre eles a API fica alguns segundos indisponível.

---

## Passo 1: Entrar na pasta e buscar a versão nova

```bash
cd /opt/futuranext/futuranext_portal
umask 077
git fetch origin main
revision=$(git rev-parse origin/main)
short=$(git rev-parse --short=7 "$revision")
echo "VERSAO: $short"
```

✅ Mostra o SHA curto do último commit da `main`, igual ao do GitHub.

> Para publicar um commit específico em vez do último, use `revision=SHA_COMPLETO`.

## Passo 2: Preparar a pasta da release

```bash
release=$(mktemp -d "/opt/futuranext/deploy-$short.XXXXXX")
mkdir "$release/source"
git archive "$revision" | tar -x -C "$release/source"
install -m 600 .env.runtime "$release/env.runtime.before"
install -m 600 compose.yaml "$release/compose.before"
docker inspect futuranext-api-1 --format '{{.Config.Image}}' > "$release/image.before"
echo "PASTA: $release"
```

✅ `PASTA: /opt/futuranext/deploy-<sha>.XXXXXX`. **Anote esse caminho.**

## Passo 3: Gerar a imagem da API (alguns minutos)

```bash
image="futuranext-api:$short"
docker build --platform=linux/amd64 --tag "$image" "$release/source"
```

✅ Termina com `naming to docker.io/library/futuranext-api:<sha> done`, sem `ERROR`.

## Passo 4: Conferir as migrações

```bash
migrate() {
  docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
    -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
    "$image" python -m alembic "$@"
}
migrate current
migrate heads
```

✅ A primeira saída é a revisão atual do banco e a segunda é a revisão da versão nova.

- Se forem **diferentes**, a release tem migração. Siga todos os passos.
- Se forem **iguais**, não há migração. Faça mesmo assim o backup (Passo 5) e pule apenas o Passo 9.

## Passo 5: Backup do banco (obrigatório)

Cole o bloco inteiro:

```bash
python3 - "$release" <<'PY'
from pathlib import Path
from sys import argv
from urllib.parse import unquote, urlsplit

entry = next(line.split("=", 1)[1] for line in Path(".env.migration").read_text().splitlines()
             if line.startswith("FN_MIGRATION_DATABASE_URL="))
url = urlsplit(entry.strip().strip(chr(34)).strip(chr(39)))
fields = {
    "PGHOST": url.hostname or "",
    "PGPORT": str(url.port or 5432),
    "PGDATABASE": unquote(url.path.lstrip("/")),
    "PGUSER": unquote(url.username or ""),
    "PGPASSWORD": unquote(url.password or ""),
    "PGSSLMODE": "require",
    "PGCONNECT_TIMEOUT": "10",
}
if not (fields["PGHOST"].endswith(".pooler.supabase.com")
        and fields["PGPORT"] == "5432"
        and fields["PGUSER"] == "postgres.wcskiimwoqgauehwcwyo"
        and fields["PGDATABASE"] == "postgres" and fields["PGPASSWORD"]):
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

✅ Mostra um número maior que zero (por exemplo `57`) e o arquivo `pre-deploy.dump` com tamanho maior que zero (por exemplo `293K`).

❌ "Confira o destino de produção" ou qualquer erro: pare. O backup é a condição para migrar.

## Passo 6: Preparar a configuração da release

```bash
python3 - "$release" "$image" <<'PY'
from pathlib import Path
from sys import argv
import re

release, image = Path(argv[1]), argv[2]
source = Path(".env.runtime").read_text()
updated, count = re.subn(r"(?m)^FN_API_IMAGE=[^\n]*", f"FN_API_IMAGE={image}", source)
if count != 1:
    raise SystemExit("Confira FN_API_IMAGE no runtime.")
(release / "env.runtime.next").write_text(updated)
PY
echo "OK"
```

✅ `OK`. O arquivo `"$release/env.runtime.next"` é uma cópia da configuração atual com a imagem nova.

## Passo 7: Variáveis novas ou alteradas (somente se a release exigir)

Pule este passo se a release não trouxer variável nova (consulte `.env.example` e o README).

Use sempre a forma abaixo. **Não edite no `nano` colando valores com `$`.**

1. Remova as linhas antigas do grupo que vai mudar. O exemplo usa as variáveis do Asaas:

   ```bash
   sed -i '/^FN_ASAAS_/d' "$release/env.runtime.next"
   ```

2. Monte o bloco num bloco de notas com os valores reais e cole de uma vez:

   ```bash
   cat >> "$release/env.runtime.next" <<'FIM'
   FN_ASAAS_ENVIRONMENT=sandbox
   FN_ASAAS_API_KEY='COLE_A_CHAVE_AQUI'
   FN_ASAAS_WEBHOOK_TOKEN='COLE_O_TOKEN_AQUI'
   FIM
   ```

   - **Aspas simples** em todo valor que contenha `$` (chaves do Asaas começam com `$aact_`). Sem elas o Compose troca o valor por vazio.
   - `<<'FIM'` (com aspas) grava o texto exatamente como foi colado.
   - O ambiente precisa combinar com a chave: `$aact_hmlg_...` é `sandbox` e `$aact_prod_...` é `production`.
   - Token de webhook novo: `openssl rand -hex 32`. Ele precisa ser diferente da chave da API.

3. Confira sem expor os valores:

   ```bash
   sed -E "s/^([A-Z_]+=.{0,10}).*/\1.../" "$release/env.runtime.next" | grep '^FN_ASAAS_'
   ```

   ✅ Uma linha por variável, com a chave começando por `'$aact_`.

## Passo 8: Validar a configuração

```bash
cmp -s .env.runtime "$release/env.runtime.before" && cmp -s compose.yaml "$release/compose.before" && echo "SEM_ALTERACOES_PARALELAS"
docker compose -p futuranext --project-directory "$PWD" \
  --env-file "$release/env.runtime.next" -f "$release/source/compose.yaml" config --quiet \
  && echo "CONFIG_OK"
```

✅ `SEM_ALTERACOES_PARALELAS` e `CONFIG_OK`, **sem nenhuma linha `WARN`**.

❌ `WARN ... variable is not set` indica um valor com `$` sem aspas simples. Volte ao Passo 7.

---

> ⚠️ **Daqui em diante a produção é alterada.** Faça os Passos 9 e 10 em sequência.

## Passo 9: Migrar o banco

Pule se o Passo 4 mostrou revisões iguais.

```bash
migrate upgrade head
migrate current
```

✅ A última linha é a revisão nova com `(head)`.

## Passo 10: Trocar a configuração e subir a API

```bash
install -m 600 "$release/env.runtime.next" .env.runtime.deploy-next
install -m 644 "$release/source/compose.yaml" compose.yaml.deploy-next
mv .env.runtime.deploy-next .env.runtime
mv compose.yaml.deploy-next compose.yaml
docker compose -p futuranext --env-file .env.runtime -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 api
```

✅ `Container futuranext-api-1  Healthy`.

O comando seleciona só o serviço `api`. O proxy Caddy (`futuranext-proxy-1`) continua como está.

## Passo 11: Conferir

```bash
curl -fsS https://api.futuranext.com.br/health/live; echo
curl -fsS https://api.futuranext.com.br/health/ready; echo
docker ps --filter name=futuranext --format '{{.Names}} {{.Image}} {{.Status}}'
```

✅ As duas respostas de saúde dão sucesso e `futuranext-api-1` aparece com a imagem nova e `(healthy)`.

Para confirmar que um segredo chegou ao contêiner sem exibir o valor:

```bash
docker exec futuranext-api-1 sh -c 'test -n "$FN_ASAAS_API_KEY" && echo "CHAVE_ASAAS_OK"'
```

Depois, no navegador:

1. Abra `https://futuranext.com.br` com **Ctrl+F5**, entre com MFA e teste a funcionalidade publicada.
2. Confira que o deployment do Cloudflare Pages corresponde ao mesmo commit.
3. Registre o SHA, a pasta da release (`$release`) e a imagem anterior (`cat "$release/image.before"`).

---

## Retomar após desconexão

Reconecte o console e recrie as variáveis, trocando a pasta pela anotada no Passo 2:

```bash
cd /opt/futuranext/futuranext_portal
umask 077
release=/opt/futuranext/deploy-SHA.XXXXXX
short=SHA_CURTO
image="futuranext-api:$short"
migrate() {
  docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
    -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
    "$image" python -m alembic "$@"
}
```

Continue a partir do passo em que parou. Os arquivos da pasta da release (imagem, backup e `env.runtime.next`) continuam lá.

## Alterar só uma variável, sem nova versão

Serve para trocar a chave do Asaas, mudar de `sandbox` para `production` ou girar um token. O exemplo usa as variáveis do Asaas:

```bash
cd /opt/futuranext/futuranext_portal
umask 077
cp -p .env.runtime "/opt/futuranext/env.runtime.bak-$(date +%Y%m%d%H%M)"
sed -i '/^FN_ASAAS_/d' .env.runtime
cat >> .env.runtime <<'FIM'
FN_ASAAS_ENVIRONMENT=production
FN_ASAAS_API_KEY='COLE_A_CHAVE_AQUI'
FN_ASAAS_WEBHOOK_TOKEN='COLE_O_TOKEN_AQUI'
FIM
docker compose -p futuranext --env-file .env.runtime -f compose.yaml config --quiet && echo "CONFIG_OK"
docker compose -p futuranext --env-file .env.runtime -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 api
curl -fsS https://api.futuranext.com.br/health/ready; echo
```

Ao trocar o token do webhook, atualize o mesmo valor no painel do Asaas (**Integrações → Webhooks**).

Ao trocar de `sandbox` para `production`, as empresas recebem um cliente novo no Asaas de produção quando a tela de Cobranças é aberta. Faturas já emitidas continuam no ambiente em que foram criadas.

## Se algo der errado

| Situação | O que fazer |
| --- | --- |
| Erro nos Passos 1 a 8 | Nada mudou em produção. Corrija e repita a partir do passo com erro |
| `migrate upgrade head` falhou | A migração roda em transação. Confira `migrate current`. Se a revisão não mudou, a API antiga segue funcionando. Guarde a saída do erro |
| API não fica `Healthy` no Passo 10 | Veja `docker logs --tail 80 futuranext-api-1`. `/health/ready` com 503 indica banco, revisão ou configuração |
| Voltar a configuração anterior | `install -m 600 "$release/env.runtime.before" .env.runtime` e `install -m 644 "$release/compose.before" compose.yaml`, depois o comando `up` do Passo 10. Só funciona se a imagem anterior for compatível com o banco já migrado. Após uma migração, prefira corrigir com uma nova release |
| Restaurar o banco | Use `"$release/pre-deploy.dump"` com `pg_restore`, avaliando as alterações feitas depois do backup. Faça isso de forma planejada, não às pressas |

## Segurança

- Não mande prints ou textos com chaves, tokens, senhas ou `.env.*` para chats, tickets ou commits. Uma chave exposta deve ser **desativada e gerada de novo** no painel do fornecedor.
- `.env.runtime` e `.env.migration` ficam com permissão `600` no servidor e nunca vão para o Git.
- As pastas `/opt/futuranext/deploy-*` contêm backup com dados de autenticação. Mantenha-as no servidor e apague as antigas quando não forem mais necessárias para reversão.
