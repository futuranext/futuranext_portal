# Portal de ferramentas FuturaNext

## Administração

1. Entre em **Administração** e conclua a verificação em duas etapas.
2. Use **Empresas** para criar ou editar clientes. A migração já inclui Equinos Seguros, CCP - Clegio Clemente Pavan, PetCamp, Estetica Paraiso e Barbearia Stylus com as licenças iniciais.
3. Para uma conta nova, abra **Usuários**, selecione a primeira empresa e envie o convite. O usuário confirma o e-mail e aceita o vínculo no portal. A entrega segue a configuração de e-mail do Supabase Auth.
4. Para uma conta confirmada, abra **Acessos por usuário**, busque o e-mail e selecione a conta. Em cada empresa, marque **Liberar acesso a esta empresa**, escolha o perfil e salve. Para revogar, desmarque e salve. Esse procedimento também vale para o próprio administrador da FuturaNext que queira usar a área de uma empresa.
5. Em **Catálogo de produtos**, cadastre e edite ferramentas. Para uma ferramenta externa, informe a URL HTTPS sem credenciais. Em **Licenças**, escolha as empresas que terão acesso e a duração da licença.
6. Uma ferramenta marcada como oferta aparece em **Portal → Ferramentas → Adquirir ferramentas** para as empresas elegíveis. A aprovação em **Administração → Solicitações** libera a licença. A cobrança da empresa é configurada em **Administração → Cobranças**.

Em **Administração → Usuários**, o administrador da FuturaNext também pode abrir **Cadastro por senha temporária**, selecionar o perfil e cadastrar o e-mail e uma senha para entrega privada. A opção atende contas novas e convites pendentes da empresa sem vínculo e sem autenticador verificado, inclusive se o convite já foi aberto. Ela substitui a credencial da conta pendente e confirma o endereço para login, por isso confira o e-mail antes de salvar. O usuário troca a senha ao entrar e faz novo login para concluir a verificação por autenticador. A senha temporária anterior e as sessões emitidas com ela perdem acesso na troca.

## Cobranças por empresa

1. Em **Administração → Cobranças**, selecione a empresa e vincule o identificador `cus_…` do cliente cadastrado no Asaas. O identificador precisa ser do mesmo ambiente da integração. Use um cliente Asaas distinto para cada empresa.
2. Cadastre os serviços e seus valores em reais. Para Equinos Seguros, hospedagem e API WhatsApp podem ser dois serviços com preços independentes.
3. Informe a competência e o vencimento e gere a fatura. Ela soma os serviços ativos e grava os valores daquela emissão. Os preços editados no cadastro valem para novas faturas. É possível cancelar e refazer a fatura local enquanto ela ainda estiver disponível para cancelamento.
4. Em **Portal → Cobranças**, o cliente escolhe a fatura e abre o pagamento no Asaas. O Asaas apresenta Boleto, Pix e Cartão de crédito conforme os meios ativados na conta.
5. O webhook atualiza a situação no portal. Use **Atualizar situação** na administração para consultar o Asaas manualmente. Uma cobrança em processamento após falha de comunicação deve ser conciliada pela referência antes de qualquer nova emissão.

A emissão é manual por competência. Pagamento e liberação de licença são ações independentes. Ao consolidar um serviço que já é cobrado por assinatura em outro sistema, confira a recorrência existente no Asaas antes de incluí-lo na fatura da FuturaNext. [Configuração do Asaas](INTEGRACOES.md#asaas)

## Ferramentas iniciais

| Empresa | Área liberada |
| --- | --- |
| Equinos Seguros | Aba **Portal de cotações**. O endereço HTTPS é configurado por empresa. A autenticação e a automação de cotações continuam no projeto de origem. |
| CCP - Clegio Clemente Pavan | **Emissor de NFS-e em lote** com cadastro do prestador e parâmetros fiscais, certificado A1, validade e modelos vazios genérico e compatível com o robô CCP. |
| PetCamp | **Consulta de NFS-e** com cadastro de CNPJs, certificado A1 e validade. |
| Estetica Paraiso | **Chatbot para Clínicas de Estética** com profissionais, expediente, serviços, produtos, bloqueios, agendamentos e acompanhamento. |
| Barbearia Stylus | **Chatbot para Barbearias** com os mesmos recursos e dados próprios. |

Configure [as integrações externas](INTEGRACOES_EXTERNAS.md) por UUID de empresa para oferecer a URL da Equinos, habilitar o painel WhatsApp e ligar seus webhooks. Só a conta já provisionada no serviço externo pode abrir o painel daquele serviço. Mantenha o bot da Equinos na máquina em que já opera.

O módulo fiscal prepara e protege os dados de configuração. Ele requer homologação e conexão de um executor para emitir ou consultar notas. O cadastro do certificado apresenta vigência e dias restantes no portal. A autenticação e as autorizações fiscais seguem os requisitos do ambiente de destino. [Guia dos módulos fiscais](NFSE.md) · [Documentação técnica do Emissor Nacional](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual)

As duas agendas podem funcionar pelo portal com operação manual. O código de conversação por WhatsApp entra em uma integração posterior. Os agendamentos e bloqueios ocupam horários no mesmo calendário por profissional.

## Atualizar a publicação existente

1. Faça backup do banco antes da migração. Guarde uma cópia das configurações privadas do backend e a referência das versões atualmente publicadas.
2. No servidor da API, adicione a variável privada `FN_FISCAL_MASTER_KEY` ao arquivo de runtime. Para gerar uma chave, execute o comando abaixo em terminal privado e armazene a saída no cofre e no backend. Guarde essa mesma chave para futuras atualizações, pois ela é necessária para ler os certificados já cadastrados. Configure a variável privada `FN_EXTERNAL_INTEGRATIONS` conforme os endereços e as instâncias que estiverem disponíveis.

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

3. Versione a revisão que será publicada. Os comandos do guia original que usam `git archive HEAD` incluem os arquivos do commit escolhido. Construa a imagem com o código novo e transfira os arquivos de deploy. No servidor, execute `python -m alembic upgrade head` **na imagem nova**, com a configuração de migração do Supabase desse ambiente. A revisão esperada é `0009_billing`. Em seguida, suba a nova imagem da API e confira `/health/live` e `/health/ready`.
4. Reconstrua o frontend com as três variáveis públicas atuais do Supabase e da API e publique a nova pasta `dist`. Se sua publicação manual segue o guia original, reaproveite os comandos de [envio da API e publicação do frontend](PRIMEIRA_PUBLICACAO.md#4-coloque-a-api-no-droplet).
5. Entre com seu administrador em `/admin/acessos`, vincule sua conta a uma empresa e verifique a ferramenta dessa empresa no portal. Teste uma segunda conta confirmada com acesso apenas a outro cliente, e o envio de um convite com o provedor de e-mail do ambiente.

## Adicionar ferramentas

Para uma ferramenta por link, cadastre nome, identificador e URL no catálogo e escolha os clientes nas licenças. O portal usa o cadastro para exibir a ferramenta. O serviço de destino deve aplicar a própria autorização do usuário.

Para uma ferramenta com telas e dados próprios, implemente suas rotas em `backend/app/modules/` com `require_tool` e políticas por empresa nas tabelas do módulo. Registre o identificador e a importação sob demanda em `frontend/js/tools/index.js`. O catálogo e as licenças continuam sendo administrados pelo mesmo painel.
