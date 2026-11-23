# Publicação: cobrança mensal, compra de ferramentas, teste grátis e chat com IA

Esta release **tem migração de banco** (`0014_commercial`). Ela segue os passos de [ATUALIZAR_API.md](ATUALIZAR_API.md), com o backup e a migração no meio, como na publicação do WhatsApp.

O que muda:

- **Cobrança mensal automática:** uma assinatura no Asaas por empresa. Ela é ligada e desligada em **Administração › Cobranças**, com o valor igual à soma dos serviços ativos.
- **Compra de ferramentas:** o administrador da empresa contrata no portal. A ferramenta é liberada na hora e o preço entra na assinatura.
- **Teste grátis de 15 dias:** fica em `/cadastro`, e cada CPF/CNPJ só pode usá-lo uma vez. O teste libera as ferramentas marcadas como "Oferecer para aquisição".
- **Chat do site com IA:** usa `gpt-4o-mini`, responde só sobre a FuturaNext e envia os contatos para `suporte@futuranext.com.br`.
- **Usuários e perfil:** o menu "Usuários" sai do portal dos clientes, o topo mostra o nome de quem está conectado e o link de aceite do WhatsApp volta ao destino depois do login.

## Antes de começar

1. **Chave da OpenAI.** Em [platform.openai.com](https://platform.openai.com/api-keys), crie uma chave só para o site e defina um limite mensal de gasto (por exemplo US$ 20). Guarde a chave no cofre.
2. **Preço das ferramentas.** Depois da publicação, abra cada ferramenta em **Administração › Catálogo de produtos**, preencha "Mensalidade em reais" e marque a oferta. Sem preço, a ferramenta não pode ser comprada.
3. **Webhook do Asaas (obrigatório para a assinatura).** No Asaas, vá em **Integrações › Webhooks** e configure:
   - URL: `https://api.futuranext.com.br/api/v1/integrations/asaas/events`;
   - token de autenticação igual a `FN_ASAAS_WEBHOOK_TOKEN`;
   - eventos de **Cobranças**.

   Sem o webhook, as faturas mensais geradas pelo Asaas não aparecem no portal.

## L1. Passos 1, 2 e 3 do ATUALIZAR_API.md

Faça os **Passos 1, 2 e 3** de [ATUALIZAR_API.md](ATUALIZAR_API.md). **Não faça o Passo 4.**

## L2. Conferir a migração e fazer o backup

Use a função `migrate` e o bloco de backup do **W2** de [WHATSAPP_PUBLICACAO.md](WHATSAPP_PUBLICACAO.md#w2-conferir-a-migração-e-fazer-o-backup-do-banco).

✅ `migrate current` mostra `0013_whatsapp_service` e `migrate heads` mostra `0014_commercial (head)`. Se não for assim, **pare**.

## L3. Migrar

> ⚠️ Daqui em diante a produção é alterada. Faça L3 e L4 em sequência. Entre os dois, a API aparece como `unhealthy` porque exige a revisão nova, mas continua atendendo.

```bash
migrate upgrade head
migrate current
```

✅ `0014_commercial (head)`.

A migração **cria** objetos novos:
- tabelas `billing_subscriptions` e `trials`;
- funções de compra, de teste e do webhook da assinatura.

Ela também **acrescenta** colunas que aceitam nulo:
- `products.price_cents`;
- `billing_services.product_id`;
- `billing_invoices.subscription_id`.

Nenhum dado existente é alterado. Em erro, nada é aplicado, porque a migração roda numa transação.

## L4. Publicar a API com a chave da OpenAI

1. Faça o **Passo 5** de [ATUALIZAR_API.md](ATUALIZAR_API.md).
2. Grave a chave da OpenAI. Ela não aparece na tela e não fica no histórico:

   ```bash
   read -rsp 'Chave da OpenAI: ' KEY; echo
   sed -i '/^FN_OPENAI_API_KEY=/d' "$release/env.runtime.next"
   printf "FN_OPENAI_API_KEY='%s'\n" "$KEY" >> "$release/env.runtime.next"
   unset KEY
   grep -c "^FN_OPENAI_API_KEY='sk-" "$release/env.runtime.next"
   ```

   ✅ `1`.

3. Faça os **Passos 6, 7 e 8** de [ATUALIZAR_API.md](ATUALIZAR_API.md).

✅ `futuranext-api-1` `healthy` com a imagem nova.

## L5. Site e portal

Depois do `git push`, o Cloudflare Pages publica o site e o portal. Confira se `https://futuranext.com.br/cadastro` abre.

## L6. Teste depois da publicação

1. **Chat:** no site, abra o chat e pergunte "o que vocês fazem?".
   - O esperado é uma resposta sobre a FuturaNext.
   - Pergunte algo fora do tema, como uma receita de bolo; o chat deve recusar.
   - Use "Falar com a equipe" e confira se o e-mail chega em `suporte@futuranext.com.br`.
2. **Teste grátis:** em `/cadastro`, use um CNPJ de teste.
   - O convite chega por e-mail.
   - Depois de criar a senha, o portal mostra "Teste grátis até …" e as ferramentas ofertadas.
3. **Compra:** como administrador dessa empresa, contrate uma ferramenta em **Ferramentas › Contratar ferramentas**.
   - Em **Administração › Cobranças**, a empresa aparece com "Assinatura ativa" e o valor somado.
   - No Asaas (sandbox), aparece a assinatura com a referência `futuranext:subscription:<id>`.
4. **Liga e desliga:** em **Administração › Cobranças**, desligue e ligue a cobrança.
   - No Asaas, a assinatura passa a `INACTIVE` e volta a `ACTIVE`.
5. **Fatura mensal:** no Asaas sandbox, a cobrança da assinatura é criada antes do vencimento.
   - Com o webhook configurado, ela aparece em **Pagamento** no portal, com boleto, Pix e cartão.

## Limites conhecidos

- **Débito automático no cartão:** ainda não existe. Todo mês o cliente paga pelo portal, por boleto, Pix ou cartão. A documentação do Asaas descreve de dois jeitos o endpoint que grava o cartão na assinatura (`POST /v3/subscriptions/{id}/updateCreditCard` e `PUT /v3/subscriptions/{id}/creditCard`). Confirme no sandbox antes de implementar.
- **Fim do teste:** a assinatura não é criada sozinha. As ferramentas que não forem contratadas param no fim do teste.
- **Limites do chat e do cadastro:** os limites por visitante ficam na memória da API e são zerados quando ela reinicia.
  - Chat: 20 mensagens a cada 10 minutos e 80 por dia, com teto diário em `FN_SITE_CHAT_DAILY_LIMIT`.
  - Cadastro: 3 por hora por visitante.
  - O limite de gasto na OpenAI é a proteção final.

## Voltar atrás

1. Volte a imagem anterior da API, como em "Se precisar voltar à imagem anterior" de [ATUALIZAR_API.md](ATUALIZAR_API.md). Ela exige a revisão `0013`, então antes rode `migrate downgrade 0013_whatsapp_service`.
2. O downgrade apaga as assinaturas, os testes grátis e os preços registrados. **As assinaturas criadas no Asaas continuam lá:** pause-as no Asaas.
