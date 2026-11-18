# Atualizar o backend / API da FuturaNext no Droplet

Este procedimento publica **somente uma nova versão do backend/API** em `https://api.futuranext.com.br`, a partir da branch `main` do GitHub. Execute os comandos no **console Linux (bash)** do Droplet `futuranext-prod` (DigitalOcean).

**O que este guia faz:** busca o código, constrói uma nova imagem Docker, preserva as credenciais existentes em `.env.runtime`, recria somente o serviço `api` e verifica a saúde da API. **Não atualiza o frontend, não troca chaves do Asaas e não modifica o banco de dados.**

> **Pré-requisitos:** o `git push origin main` já deve ter sido concluído com sucesso; a versão desejada deve estar em `origin/main`. Execute um bloco por vez. Se houver erro, não avance para o próximo passo. As variáveis `revision`, `short`, `release` e `image` duram apenas enquanto o terminal estiver conectado.

## 1. Buscar a versão publicada no GitHub

```bash
cd /opt/futuranext/futuranext_portal
umask 077

git fetch origin main
revision=$(git rev-parse origin/main)
short=$(git rev-parse --short=7 "$revision")
echo "VERSAO PARA PUBLICAR: $short"
```

**Esperado:** o SHA curto da versão mais recente da `main`. Não é necessário executar `git pull` ou sobrescrever alterações locais.

## 2. Preparar a release e fazer backup da configuração

```bash
release=$(mktemp -d "/opt/futuranext/deploy-$short.XXXXXX")
mkdir "$release/source"
git archive "$revision" | tar -x -C "$release/source"

install -m 600 .env.runtime "$release/env.runtime.before"
install -m 644 compose.yaml "$release/compose.before"
docker inspect futuranext-api-1 --format '{{.Config.Image}}' > "$release/image.before"

echo "PASTA DA RELEASE: $release"
cat "$release/image.before"
```

**Esperado:** a pasta da release e a imagem anterior da API. **Anote o caminho da release**, pois ele será necessário para reverter uma publicação.

## 3. Construir a nova imagem do backend

```bash
image="futuranext-api:$short"
docker build --platform=linux/amd64 --tag "$image" "$release/source"
```

**Esperado:** término do build sem `ERROR`, com a imagem `futuranext-api:<SHA>` criada.

## 4. Verificar se a nova versão exige migração de banco

```bash
docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
  -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
  "$image" python -m alembic current

docker run --rm -e PGSSLMODE=require -e PGCONNECT_TIMEOUT=10 \
  -e 'PGOPTIONS=-c lock_timeout=5s' --env-file .env.migration \
  "$image" python -m alembic heads
```

**Compare as revisões:**

- **Iguais:** não há migração pendente; continue.
- **Diferentes ou erro em qualquer comando:** **pare**. A versão pode exigir backup e migração do banco antes de publicar; este guia é restrito à atualização da API sem migração.

## 5. Preparar a configuração da nova imagem sem alterar credenciais

```bash
python3 - "$release" "$image" <<'PY'
from pathlib import Path
from sys import argv
import re

release, image = Path(argv[1]), argv[2]
source = Path('.env.runtime').read_text()
updated, count = re.subn(r'(?m)^FN_API_IMAGE=[^\n]*', f'FN_API_IMAGE={image}', source)
if count != 1:
    raise SystemExit('ERRO: confira FN_API_IMAGE em .env.runtime')
target = release / 'env.runtime.next'
target.write_text(updated)
target.chmod(0o600)
print('CONFIGURACAO_PREPARADA')
PY
```

**Esperado:** `CONFIGURACAO_PREPARADA`. Todas as demais variáveis do `.env.runtime` são preservadas.

## 6. Validar antes de publicar

```bash
cmp -s .env.runtime "$release/env.runtime.before" && \
  cmp -s compose.yaml "$release/compose.before" && \
  echo "CONFIGURACAO_ORIGINAL_PRESERVADA"

docker compose -p futuranext --project-directory "$PWD" \
  --env-file "$release/env.runtime.next" \
  -f "$release/source/compose.yaml" config --quiet && \
  echo "CONFIG_OK"

docker compose -p futuranext --project-directory "$PWD" \
  --env-file "$release/env.runtime.next" \
  -f "$release/source/compose.yaml" config --services
```

**Esperado:** `CONFIGURACAO_ORIGINAL_PRESERVADA`, `CONFIG_OK` e o serviço `api` listado. Se aparecer qualquer erro ou aviso relevante, não publique.

## 7. Publicar somente o backend/API

> **Este passo altera a versão em produção.** Pode haver uma interrupção breve durante a recriação do contêiner. O proxy Caddy não será recriado.

```bash
install -m 600 "$release/env.runtime.next" .env.runtime.deploy-next
install -m 644 "$release/source/compose.yaml" compose.yaml.deploy-next
mv .env.runtime.deploy-next .env.runtime
mv compose.yaml.deploy-next compose.yaml

docker compose -p futuranext --env-file .env.runtime -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 api
```

**Esperado:** contêiner `futuranext-api-1` iniciado e `healthy`. **Não use `--remove-orphans`**: o proxy pode ser listado como *orphan* sem que isso impeça a publicação da API.

## 8. Confirmar que a API está funcionando

```bash
curl -fsS https://api.futuranext.com.br/health/live; echo
curl -fsS https://api.futuranext.com.br/health/ready; echo

docker compose -p futuranext --env-file .env.runtime -f compose.yaml ps

docker ps --filter name=futuranext \
  --format '{{.Names}} | {{.Image}} | {{.Status}}'
```

**Esperado:** os dois endpoints de saúde respondem com sucesso e `futuranext-api-1` aparece como `healthy`, usando a imagem `futuranext-api:<SHA>` construída no Passo 3.

Se precisar consultar erros:

```bash
docker logs --tail 80 futuranext-api-1
```

Não compartilhe logs que contenham tokens ou credenciais.

## Se precisar voltar à imagem anterior

Use apenas se **nenhuma migração de banco foi executada** e se você anotou a pasta da release do Passo 2.

```bash
# A variável $release precisa apontar para a pasta criada no Passo 2.
install -m 600 "$release/env.runtime.before" .env.runtime
install -m 644 "$release/compose.before" compose.yaml

docker compose -p futuranext --env-file .env.runtime -f compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 90 api

curl -fsS https://api.futuranext.com.br/health/ready; echo
```

Se a conexão com o console cair, retome com:

```bash
cd /opt/futuranext/futuranext_portal
umask 077
release=/opt/futuranext/deploy-COLE_A_PASTA_DA_RELEASE_AQUI
```

Substitua o valor de `release` pelo **caminho exato** anotado no Passo 2 e então execute somente a etapa necessária. Nunca cole senhas, arquivos `.env.*` ou tokens no GitHub.
