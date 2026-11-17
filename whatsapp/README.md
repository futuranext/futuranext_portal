# Serviço WhatsApp da FuturaNext

Este é o backend e o worker do WhatsApp, vindos do projeto `Api_WhatsApp`. Ele funciona como um **serviço interno do portal**. Os clientes não acessam este serviço diretamente: o aceite, o QR Code, o webhook e as chaves ficam no portal, em **Ferramentas › WhatsApp**.

- `backend/app/portal_service.py` tem as rotas `/service/v1/...` usadas pela API do portal, com o token `PORTAL_SERVICE_TOKEN`.
- `backend/app/callbacks.py` envia webhooks assinados. Os destinos são cadastrados pelo portal na coleção `callback_destinations` ou, opcionalmente, em `WHATSAPP_OUTBOUND_WEBHOOKS`.
- `compose.yaml` define a produção no Droplet: projeto `futuranext-whatsapp`, sem portas publicadas, com o backend na rede `futuranext_default` com o nome `whatsapp-api`.
- `integrations/python/whatsapp_client.py` é o SDK para bots. Use `WHATSAPP_API_URL=https://api.futuranext.com.br/whatsapp`.

## Testes

```powershell
cd whatsapp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Os testes usam um Mongo, um Redis e um gateway simulados. Para publicar, siga [docs/WHATSAPP_PUBLICACAO.md](../docs/WHATSAPP_PUBLICACAO.md).

Este sistema utiliza a **Evolution API** como gateway. © 2025 Evolution API. [Licença da versão 2.3.7](docs/third-party/EVOLUTION-LICENSE-2.3.7.txt). O WhatsApp Web não é oficial e está sujeito a restrição, desconexão e incompatibilidade.
