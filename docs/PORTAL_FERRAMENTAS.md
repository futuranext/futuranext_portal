# Portal de ferramentas FuturaNext

## Administração

1. Entre em **Administração** e conclua a verificação em duas etapas.
2. Use **Empresas** para criar ou editar clientes. A migração já inclui Equinos Seguros, CCP - Clegio Clemente Pavan, PetCamp, Estetica Paraiso e Barbearia Stylus com as licenças iniciais.
3. Para uma conta nova, abra **Usuários**, selecione a primeira empresa e envie o convite. O usuário confirma o e-mail e aceita o vínculo no portal. A entrega segue a configuração de e-mail do Supabase Auth.
4. Para uma conta confirmada, abra **Acessos por usuário**, busque o e-mail e selecione a conta. Em cada empresa, marque **Liberar acesso a esta empresa**, escolha o perfil e salve. Para revogar, desmarque e salve. Esse procedimento também vale para o próprio administrador da FuturaNext que queira usar a área de uma empresa.
5. Em **Catálogo de produtos**, cadastre e edite ferramentas. Para uma ferramenta externa, informe a URL HTTPS sem credenciais. Em **Licenças**, escolha as empresas que terão acesso e a duração da licença.
6. Uma ferramenta marcada como oferta aparece em **Portal → Ferramentas → Adquirir ferramentas** para as empresas elegíveis. A aprovação em **Administração → Solicitações** libera a licença. A venda usa solicitação e aprovação manual. Pagamento e cobrança precisam de uma integração à parte.

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

3. Versione a revisão que será publicada. Os comandos do guia original que usam `git archive HEAD` incluem os arquivos do commit escolhido. Construa a imagem com o código novo e transfira os arquivos de deploy. No servidor, execute `python -m alembic upgrade head` **na imagem nova**, com o ambiente de migração e a mesma montagem da CA do Supabase já usadas na primeira publicação. A revisão esperada é `0007_agenda_products`. Em seguida, suba a nova imagem da API e confira `/health/live` e `/health/ready`.
4. Reconstrua o frontend com as três variáveis públicas atuais do Supabase e da API e publique a nova pasta `dist`. Se sua publicação manual segue o guia original, reaproveite os comandos de [envio da API e publicação do frontend](PRIMEIRA_PUBLICACAO.md#4-coloque-a-api-no-droplet).
5. Entre com seu administrador em `/admin/acessos`, vincule sua conta a uma empresa e verifique a ferramenta dessa empresa no portal. Teste uma segunda conta confirmada com acesso apenas a outro cliente, e o envio de um convite com o provedor de e-mail do ambiente.

## Adicionar ferramentas

Para uma ferramenta por link, cadastre nome, identificador e URL no catálogo e escolha os clientes nas licenças. O portal usa o cadastro para exibir a ferramenta. O serviço de destino deve aplicar a própria autorização do usuário.

Para uma ferramenta com telas e dados próprios, implemente suas rotas em `backend/app/modules/` com `require_tool` e políticas por empresa nas tabelas do módulo. Registre o identificador e a importação sob demanda em `frontend/js/tools/index.js`. O catálogo e as licenças continuam sendo administrados pelo mesmo painel.
