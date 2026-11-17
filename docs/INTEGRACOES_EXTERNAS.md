# WhatsApp e Cotação de Seguros Equinos

As ferramentas aparecem no portal da empresa com licença e acesso ao produto. A configuração do serviço externo é feita pelo operador da FuturaNext por UUID de empresa. Cada portal mantém a própria autenticação. Os links abrem em outra aba e a FuturaNext entrega apenas os endereços registrados para a empresa autenticada.

## Configuração do runtime

Defina `FN_EXTERNAL_INTEGRATIONS` no backend em formato JSON. Exemplos de valores fictícios, em uma única linha quando armazenados em um arquivo de ambiente:

```json
{"11111111-1111-4111-8111-111111111111":{"equinos_portal_url":"https://equinos.example.com/portal","whatsapp_portal_url":"https://whatsapp.example.com/","whatsapp_instance_id":"22222222-2222-4222-8222-222222222222","whatsapp_webhook_secret":"COFRE_COM_SEGREDO_EXCLUSIVO_DE_32_BYTES_OU_MAIS"}}
```

- Para Equinos, configure um endereço HTTPS do portal dedicado da empresa, no caminho `/portal`. A autenticação continua no React/Flask do projeto Equinos. A máquina local que hospeda o clone precisa disponibilizar esse frontend por uma rota acessível aos colaboradores autorizados. Preserve a autenticação e restrinja o acesso à origem e às rotas da automação.
- Para WhatsApp, use o UUID real da instância da empresa no serviço existente. O endereço `whatsapp_portal_url` é opcional e deve ser a raiz de um painel dedicado ao cliente, com sua própria conta de cliente. A rota de controle global, a administração da infraestrutura e o ingresso interno da Evolution ficam no serviço de origem.
- Configure `whatsapp_instance_id` e `whatsapp_webhook_secret` juntos. Use um segredo exclusivo por empresa que tenha pelo menos 32 bytes e fique no cofre do backend e do bot. O frontend recebe URL, UUID da instância e estado da integração. A API rejeita portais, instâncias e segredos duplicados entre empresas.
- Depois de atualizar o ambiente do backend, o administrador da empresa habilita o recebimento em **Ferramentas → WhatsApp → Webhook**. O backend vincula a configuração à instância da empresa e registra a alteração na auditoria.

## Contrato do webhook

O painel informa a URL completa do webhook. O código do bot envia `POST` com `Content-Type: application/json`, `X-Timestamp` em segundos Unix e `X-Signature` no formato `sha256=<hex>`. A assinatura é HMAC-SHA256 dos bytes `timestamp + '.' + corpo`. A API aceita uma janela de cinco minutos para o timestamp da assinatura, valida a instância e reavalia a licença no banco. O corpo tem limite de 4 KiB.

O envelope aceita `id`, `type`, `instance_id` e `occurred_at`. Use um UUID estável por evento, o UUID da instância autorizada e uma data ISO 8601 com fuso. Os tipos são `message.received`, `message.status` e `instance.connection`. O contrato armazena metadados operacionais. Números de telefone, conteúdo de mensagens, QR e credenciais ficam no sistema de origem.

| Resposta | Significado |
| --- | --- |
| `201 accepted` | O portal registrou os metadados. |
| `200 duplicate` | O portal já possui o mesmo evento. |
| `401` | Credencial, assinatura ou instância inválida. |
| `404` | Webhook ou licença indisponível. |
| `409` | O ID já referencia metadados diferentes. |
| `422` | O envelope contém campos ou valores incompatíveis. |

## Registrar destinos no Api_WhatsApp

No ambiente do `worker` do projeto `Api_WhatsApp`, defina o segredo de runtime `WHATSAPP_OUTBOUND_WEBHOOKS` como um array JSON. O mesmo array pode registrar a FuturaNext e um endpoint HTTPS no código do chatbot. O worker começa a enfileirar novos eventos ao carregar a configuração.

```json
[
  {
    "tenant_id": "33333333-3333-4333-8333-333333333333",
    "instance_id": "22222222-2222-4222-8222-222222222222",
    "kind": "futuranext",
    "url": "https://api.example.com/api/v1/integrations/whatsapp/11111111-1111-4111-8111-111111111111/events",
    "secret": "COFRE_COM_SEGREDO_EXCLUSIVO_DE_32_BYTES_OU_MAIS"
  },
  {
    "tenant_id": "33333333-3333-4333-8333-333333333333",
    "instance_id": "22222222-2222-4222-8222-222222222222",
    "kind": "bot",
    "url": "https://bot.example.com/webhooks/whatsapp",
    "secret": "OUTRO_SEGREDO_EXCLUSIVO_DO_BOT_COM_32_BYTES_OU_MAIS",
    "include_message": false
  }
]
```

1. Em `tenant_id`, use o identificador da empresa no **Api_WhatsApp**. Use o mesmo UUID de instância que aparece na configuração da FuturaNext. A instalação local de bootstrap do Api_WhatsApp usa `local-company` como ID da empresa.
2. Para o destino `futuranext`, copie a URL exibida no painel WhatsApp da FuturaNext e use o mesmo segredo de `whatsapp_webhook_secret`. O UUID no caminho da URL é o da empresa na **FuturaNext**. Habilite o webhook no portal antes de ativar o worker com essa configuração.
3. Para o destino `bot`, aponte para a rota de callback do chatbot e configure um segredo exclusivo no receptor. Verifique o HMAC sobre o corpo bruto, a janela de cinco minutos, a instância esperada e a igualdade de `X-Event-Id` com `id`. Registre o `id` antes de acionar um efeito, de forma que uma nova entrega do mesmo evento seja idempotente. Use `include_message: true` quando o bot autorizado precisar do telefone e do texto recebidos. O campo `data` aparece somente no destino `bot`.
4. Carregue a configuração no processo do worker. O Compose do Api_WhatsApp encaminha a variável somente a esse processo. Ao rodar mais de um worker, use a mesma configuração em todos eles. Guarde o array no cofre do serviço.

O worker confirma entregas por HTTP `2xx` e limita as repetições com o mesmo `id`. Uma falha de leitura, interrupção ou resposta ambígua de um bot fica marcada como `unknown` para conferência. Remova um destino da configuração do worker para suspender novos envios. Consulte `docs/OUTBOUND_WEBHOOKS.md` no Api_WhatsApp para o conteúdo dos eventos e os estados da fila.

## Adaptador para envios diretos do bot

O adaptador Python está em `backend/app/modules/external_whatsapp.py`. Importe-o do pacote ou copie esse módulo para um código de bot Python autorizado com `httpx` e `pydantic` instalados. Use as variáveis `FN_WHATSAPP_WEBHOOK_URL` e `FN_WHATSAPP_WEBHOOK_SECRET` no ambiente do bot:

```python
import os
from datetime import UTC, datetime
from uuid import UUID

import httpx
from external_whatsapp import WhatsAppEvent, send_whatsapp_event

event = WhatsAppEvent(
    id=UUID(event_id),  # UUID estável guardado junto ao evento de origem
    type="message.received",
    instance_id=UUID(os.environ["FN_WHATSAPP_INSTANCE_ID"]),
    occurred_at=datetime.now(UTC),  # guarde este valor com o ID para repetir o evento
)
with httpx.Client() as client:
    send_whatsapp_event(
        client,
        os.environ["FN_WHATSAPP_WEBHOOK_URL"],
        os.environ["FN_WHATSAPP_WEBHOOK_SECRET"],
        event,
    )
```

Use o adaptador depois que o código do bot confirmar um evento na origem que não passa pelo worker do Api_WhatsApp. Em falha de rede, repita com os mesmos metadados e o mesmo ID. O adaptador assina novamente a requisição e o portal evita duplicação. O SDK do `Api_WhatsApp` continua responsável pelas solicitações de envio de mensagens.
