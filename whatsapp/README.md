# Serviço WhatsApp da FuturaNext

Este é o backend e o worker do WhatsApp, vindos do projeto `Api_WhatsApp`. Ele funciona como um **serviço interno do portal**. Os clientes não acessam este serviço diretamente: o aceite, o QR Code, o webhook e as chaves ficam no portal, em **Ferramentas › WhatsApp**.

- `backend/app/portal_service.py` tem as rotas `/service/v1/...` usadas pela API do portal, com o token `PORTAL_SERVICE_TOKEN`.
- Os dados ficam no PostgreSQL (Supabase) do portal, no schema privado `whatsapp`, criado pela migração Alembic `0013_whatsapp_service` do portal. O backend usa o papel `wa_api` e o worker usa o papel `wa_worker`. Veja [docs/WHATSAPP_POSTGRES.md](../docs/WHATSAPP_POSTGRES.md).
- `backend/app/callbacks.py` envia webhooks assinados. Os destinos são cadastrados pelo portal em `whatsapp.callback_destinations` ou, opcionalmente, em `WHATSAPP_OUTBOUND_WEBHOOKS`.
- `compose.yaml` define a produção no Droplet: projeto `futuranext-whatsapp`, sem portas publicadas, com o backend na rede `futuranext_default` com o nome `whatsapp-api`.
- `integrations/python/whatsapp_client.py` é o SDK para bots. Use `WHATSAPP_API_URL=https://api.futuranext.com.br/whatsapp`.

## Testes

```powershell
cd whatsapp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock -r backend/requirements-dev.txt ..\backend
$env:WA_TEST_MIGRATION_DATABASE_URL = 'postgresql://postgres:SENHA@127.0.0.1:55432/futuranext_test'
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam **PostgreSQL real** e um Redis e um gateway simulados. Use um banco local e descartável, cujo nome contenha `test`: o fixture aplica as migrações do portal, inclusive downgrade e upgrade da 0013, e ativa os papéis `wa_api`/`wa_worker`. Sem `WA_TEST_MIGRATION_DATABASE_URL`, só os testes que não usam banco rodam. Para publicar, siga [docs/WHATSAPP_PUBLICACAO.md](../docs/WHATSAPP_PUBLICACAO.md).

Este sistema utiliza a **Evolution API** como gateway. © 2025 Evolution API. [Licença da versão 2.3.7](docs/third-party/EVOLUTION-LICENSE-2.3.7.txt). O WhatsApp Web não é oficial e está sujeito a restrição, desconexão e incompatibilidade.
