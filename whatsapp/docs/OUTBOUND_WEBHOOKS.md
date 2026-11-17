# Webhooks de saída

O operador registra destinos no segredo `WHATSAPP_OUTBOUND_WEBHOOKS` do worker. Use um array JSON com `tenant_id`, `instance_id`, `kind`, `url` e `secret`. A instalação de bootstrap aceita `local-company`; novas empresas usam seu UUID. O UUID de instância é o emitido pelo Api_WhatsApp. A empresa e a instância são verificadas na fila e no cadastro antes do envio. Cada destino tem um segredo exclusivo de pelo menos 32 caracteres. Para cadastrar ou remover um destino, atualize o segredo e recarregue a configuração do worker. Todos os workers devem carregar o mesmo cadastro.

```json
[
  {
    "tenant_id": "33333333-3333-4333-8333-333333333333",
    "instance_id": "22222222-2222-4222-8222-222222222222",
    "kind": "bot",
    "url": "https://bot.example.com/webhooks/whatsapp",
    "secret": "<segredo do cofre>",
    "include_message": false
  },
  {
    "tenant_id": "33333333-3333-4333-8333-333333333333",
    "instance_id": "22222222-2222-4222-8222-222222222222",
    "kind": "futuranext",
    "url": "https://api.example.com/api/v1/integrations/whatsapp/11111111-1111-4111-8111-111111111111/events",
    "secret": "<segredo da FuturaNext>"
  }
]
```

O destino `futuranext` usa a URL exibida no portal da empresa. Habilite o webhook daquela empresa e use o mesmo segredo configurado na FuturaNext. A empresa no caminho pertence à FuturaNext. A instância cadastrada no portal usa o UUID do Api_WhatsApp.

O worker consulta o DNS de cada destino e conecta a um IP público validado. O TLS usa o hostname registrado, com verificação do certificado. A URL deve usar HTTPS, hostname DNS e porta padrão. Redirecionamentos, credenciais na URL, query string e fragmento são rejeitados. As credenciais do gateway e os segredos do cadastro permanecem no ambiente do serviço.

## Receptor

O callback envia `Content-Type: application/json`, `X-Event-Id` com o UUID do evento, `X-Timestamp` com segundos Unix e `X-Signature: sha256=<hex>`. Calcule HMAC-SHA256 com o segredo do destino sobre `timestamp + '.' + bytes do corpo`. Use comparação em tempo constante, recuse assinaturas fora da janela de 5 minutos e confira a igualdade de `X-Event-Id` com o `id` do envelope e a instância esperada. Persista o `id` para deduplicar antes de iniciar efeitos no bot. Retorne `2xx` após aceitar de forma durável.

O objeto contém `id`, `type`, `instance_id` e `occurred_at`, em ISO 8601. O destino `futuranext` recebe apenas esses metadados. O destino `bot` recebe também o objeto `data`.

| Tipo | `data` do bot |
| --- | --- |
| `message.received` | Vazio por padrão. Com `include_message: true`, inclui `phone`, `message` e `message_id` da mensagem privada normalizada. |
| `message.status` | `status` e `message_id` da mensagem correlacionada. |
| `instance.connection` | `state` da instância. |

## Fila e tentativas

Para cada novo evento processado do gateway, o worker grava uma entrega por destino na coleção `webhook_deliveries` antes de marcar o evento de origem como processado. Um mesmo evento de origem conserva seu UUID e as novas tentativas são assinadas com um timestamp atualizado. A fila guarda apenas metadados e uma referência ao evento, cujo conteúdo fica criptografado na origem. As entregas expiram com o evento de origem, em até 14 dias. O worker registra `callback_config_invalid` e pausa a fila de callbacks quando a configuração é inválida. Os demais fluxos continuam ativos. Eventos processados antes de carregar um cadastro válido ficam fora da fila.

Há uma tentativa inicial e até três repetições, com intervalos de 5, 30 e 120 segundos, para falhas anteriores ao envio e HTTP `429` do receptor. O receptor deve usar `429` quando recusar o evento inteiro. Para a FuturaNext, que trata o ID de forma idempotente, o worker também repete em falha de leitura, interrupção da entrega, `408` ou `5xx`. Uma entrega ambígua para um bot é marcada `unknown` para conferência. Remover o destino cancela entregas pendentes durante a próxima execução do worker. Configure o receptor antes de ativar o cadastro.
