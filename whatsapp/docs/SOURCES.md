# Fontes, versões e licença

Consultadas em 12/11/2026. Prioridade: código versionado e registries oficiais. Os links públicos de algumas páginas v2 retornaram erro/404; rotas e variáveis foram conferidas na tag, não inventadas a partir de exemplos antigos.

## Evolution API

- [Release estável 2.3.7](https://github.com/evolution-foundation/evolution-api/releases/tag/2.3.7), confirmada também pelo endpoint GitHub releases/latest.
- [LICENSE da versão](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/LICENSE), cópia integral preservada em third-party/EVOLUTION-LICENSE-2.3.7.txt.
- [.env.example versionado](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/.env.example): DATABASE_PROVIDER, DATABASE_CONNECTION_URI, CACHE_REDIS_URI, AUTHENTICATION_API_KEY, TELEMETRY_ENABLED e flags usados no Compose.
- [Compose oficial](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/docker-compose.yaml): PostgreSQL 15, Redis e volume /evolution/instances. Adaptado para isolamento, autenticação e ausência de portas públicas; não copiar rede externa Dokploy do exemplo.
- [Rotas de instância](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/src/api/routes/instance.router.ts), [mensagens](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/src/api/routes/sendMessage.router.ts) e [webhook](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/src/api/integrations/event/webhook/webhook.router.ts).
- [Dockerfile](https://github.com/evolution-foundation/evolution-api/blob/2.3.7/Dockerfile): entrypoint executa migração Prisma e runtime Node 24; health usa Node, sem presumir curl na imagem final.
- [Imagem oficial](https://hub.docker.com/r/evoapicloud/evolution-api/tags): tag v2.3.7 consultada. Tag Git não tem v; imagem tem v.

### Condições de uso

A licença consultada combina Apache 2.0 com condições adicionais; não classificá-la simplesmente como Apache sem ressalvas. Permite uso comercial condicionado aos termos, exige aviso de uso visível aos administradores e acessível em documentação/configurações. Componentes de frontend Evolution devem preservar logo e copyright. A redação prevê licença comercial em situações de descumprimento; não concluir que qualquer modalidade de revenda está automaticamente liberada.

Nosso frontend é próprio e não copia o manager. Mesmo assim, exibe aviso de uso e link da licença. A documentação preserva a atribuição © 2025 Evolution API e o texto integral. NOTICE e TRADEMARKS.md não foram encontrados na tag 2.3.7 (404); não atribuir retroativamente documentos de main à release. Revalidar termos na atualização e antes de modelo comercial que altere atribuições; contato indicado na licença: contato@evolution-api.com. Nenhuma licença comercial foi adquirida nem contato externo realizado.

## Versões selecionadas

| Componente | Versão fixada | Evidência / observação |
|---|---|---|
| Python container | 3.12.16-slim-bookworm | tag Docker Hub verificada; host 3.13.3 |
| FastAPI | 0.162.5 | metadata PyPI |
| Uvicorn | 0.54.0 | metadata PyPI |
| PyMongo | 4.19.0 | metadata PyPI; AsyncMongoClient |
| redis-py | 8.1.0 | metadata PyPI |
| httpx | 0.28.1 | metadata PyPI |
| pydantic-settings | 2.16.2 | metadata PyPI |
| pytest | 9.2.0 | metadata PyPI |
| Node container | 24.16.0-alpine3.23 | tag Docker Hub verificada |
| Next.js | 16.4.6 | registry npm |
| React / React DOM | 19.3.1 | registry npm |
| TypeScript | 5.9.3 | versão estável existente, escolha conservadora em relação a latest 7.0.2 |
| MongoDB | 8.0.38 | tag Docker Hub da linha 8.0 |
| PostgreSQL | 15.19-alpine3.23 | tag Docker Hub e política oficial; major 15 do gateway |
| Redis server | 8.6.8-alpine3.23 | tag Docker Hub verificada |
| Evolution | v2.3.7 | imagem verificada; release estável Git 2.3.7 |

Dependências transitivas em package-lock.json e requirements.lock. Compatibilidade de execução dos containers ainda depende de Docker disponível. Tags fixam versão, não digest imutável; registrar digests e SBOM após pull/validação antes de produção. Não inferir ausência de vulnerabilidades pela versão recente.

Atualização Fase 3: containers Linux validados; adicionados argon2-cffi 25.1.0 e cryptography 50.0.2, versões consultadas no PyPI e fixadas no lock. Mocks usam mongomock-motor 0.0.36 somente nos testes. DTOs instance.dto.ts, sendMessage.dto.ts, event.dto.ts e controlador webhook da tag 2.3.7 confirmaram payloads e headers; event.router.ts confirmou `/webhook/set` e `/webhook/find`. Criação, consulta do webhook e QR foram ensaiados contra a imagem real. Nenhum número foi pareado.

Tailwind foi consultado (4.3.5 no npm), mas será introduzido com o painel na Fase 3. Caddy/versão e Compose de produção serão definidos na Fase 6, não produzir configuração com aparência de pronta antes da autenticação. Redis 8 exige revisão da opção de licença aplicável à distribuição escolhida; preservar avisos de todos os componentes antes da distribuição comercial.

## Referências complementares

- [SQLAlchemy](https://www.sqlalchemy.org/) e [PyMongo Async](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/): separação relacional/documental e escolha do driver.
- [Política PostgreSQL](https://www.postgresql.org/support/versioning/): major 15 suportado até novembro de 2027; planejar upgrade antes disso.
- [PyPI FastAPI](https://pypi.org/project/fastapi/), [PyMongo](https://pypi.org/project/pymongo/), [Uvicorn](https://pypi.org/project/uvicorn/), [redis](https://pypi.org/project/redis/), [httpx](https://pypi.org/project/httpx/), [pydantic-settings](https://pypi.org/project/pydantic-settings/), [pytest](https://pypi.org/project/pytest/).
- [Next npm](https://registry.npmjs.org/next/latest), [React npm](https://registry.npmjs.org/react/latest), [TypeScript 5.9.3](https://registry.npmjs.org/typescript/5.9.3).
- [Docker Python](https://hub.docker.com/_/python), [Node](https://hub.docker.com/_/node), [Mongo](https://hub.docker.com/_/mongo), [PostgreSQL](https://hub.docker.com/_/postgres), [Redis](https://hub.docker.com/_/redis).
