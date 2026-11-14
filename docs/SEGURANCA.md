# Segurança e privacidade

Controles de referência para a implementação. Os resultados executados constam em [Testes](TESTES.md).

## Identidade e autorização

- Aceitar JWT de sessão com algoritmo permitido por ambiente e assinatura validada. Verificar emissor, audiência `authenticated`, validade, `sub` UUID e tipo de identidade. Usar JWKS de endereço definido na configuração, rotação de chaves e tempos limite. Para ambiente local com chave simétrica legada, configurar validação no Auth e confrontar a identidade retornada com as claims. A API ignora papéis e permissões de metadados editáveis pelo usuário. [JWT no Supabase](https://supabase.com/docs/guides/auth/jwts)
- Autorização é exigida em cada rota privada, incluindo downloads. A ação administrativa exige `aal2` e papel explícito, e suporte precisa de atribuição vigente. Cliente admin só delega papéis de cliente autorizados na própria empresa. Alterações de licenças, atribuições, membros ou papéis valem na próxima operação autorizada. Responder `404` a recursos privados sem visibilidade para evitar revelar sua existência. [MFA no Supabase](https://supabase.com/docs/guides/auth/auth-mfa/totp)
- Bootstrap offline para conceder `super_admin` a um UUID de conta confirmada e conferido pelo operador. Primeiro privilégio concedido por comando temporário e auditado, com checagem transacional contra duplicação e execução concorrente. Credenciais de bootstrap ficam separadas do runtime. Revogação e recuperação de MFA precisam de procedimento operacional auditável.
- Convite pendente por empresa, e-mail normalizado e papel delegável, com prazo e revogação. A concessão da associação ocorre em transação após autenticação, confirmação do e-mail e validação do convite vigente. Para identidades existentes, exigir que a conta autenticada corresponda ao convite. Evitar dois efeitos quando o mesmo convite for retomado. [Usuários e convites no Supabase](https://supabase.com/docs/guides/auth/users)
- Usar a role de runtime com mínimo privilégio e RLS forçada nas tabelas de tenant. Verificar que papéis públicos e chaves expostas no cliente carecem de acesso ao schema do aplicativo. Aplicar restrições de integridade no SQL. Separar autorização global e por empresa, inclusive nas funções auxiliares de políticas.

## Transporte, navegador, logs e credenciais

- Usar HTTPS na web e TLS com validação de CA no banco hospedado. Expor o proxy reverso e restringir SSH ao acesso operacional por chave. Serviços internos ficam na rede privada do Compose. CORS por origem precisa e headers mínimos. Evitar caches públicos, inclusive no CDN, para respostas de portal e dados de sessão. [Conectividade do Supabase PostgreSQL](https://supabase.com/docs/guides/database/connecting-to-postgres)
- O portal usa Bearer no cabeçalho `Authorization`. Preferir sessão por aba, logout com limpeza local e validade curta do token de acesso. Se cookies de sessão passarem a ser usados, exigir `Secure`, `HttpOnly`, `SameSite` e proteção CSRF apropriada às mutações. Validar redirecionamentos pós-login e recuperação contra lista de rotas locais. CSP por superfície e escopo previsto de fontes da landing. Usar `textContent` ou escape por contexto para conteúdo de terceiros.
- Usar consultas parametrizadas e contratos de entrada, corpo e paginação limitados. Aplicar limites de taxa por IP e identidade em login e recuperação no caminho que de fato recebe essas chamadas, e proteger ações administrativas também na API. URLs de provedores cadastráveis futuramente precisam de controle de saída e validação de destino contra SSRF e de redirecionamento. Registrar alertas de falhas e saturação.
- Logs estruturados usam ID de correlação, rota normalizada e resultado. Redigir tokens, links de ativação/recuperação, segredos, anexos e payloads de conversa em API, proxy e coleta de erros. Auditar ator, empresa, ação, alvo e horário em alterações críticas, com metadados mínimos. Armazenar chaves de serviço, SMTP, DB e provedores por ambiente em armazenamento de segredos. Somente chaves Supabase publicáveis, URLs e parâmetros públicos podem ir ao bundle.
- Backups independentes de bancos e objetos, criptografados e com acesso operacional mínimo. Definir RPO/RTO com os clientes e demonstrar restauração isolada. Rotacionar e revogar credenciais após incidentes ou saídas de operadores. [Guia de implantação e recuperação](DEPLOY_DIGITALOCEAN.md)

## Dados de clientes e futuras integrações

- Mapear finalidades, dados, bases legais e retenção antes de ativar cada módulo. Definir papéis no tratamento, fluxo de pedidos dos titulares, exclusão e responsabilidades por incidentes. Para clínicas, restringir cadastro e mensagens ao mínimo exigido pela agenda. Evitar dados de saúde em logs, prompts, relatórios ou mensagens. Documentar a base e o mecanismo aplicável quando houver transferência internacional. [Guia de segurança da ANPD](https://www.gov.br/anpd/pt-br/centrais-de-conteudo/materiais-educativos-e-publicacoes/guia_seguranca_da_informacao_para_atpps___defeso_eleitoral.pdf) · [Regulamento sobre transferência internacional](https://www.gov.br/anpd/pt-br/acesso-a-informacao/institucional/atos-normativos/regulamentacoes_anpd/resolucao-cd-anpd-no-19-de-23-de-agosto-de-2024)
- Criar políticas de Storage por tenant e vincular cada objeto a registros cuja autorização seja verificada na API. Emitir URL temporária somente após autorização e manter validade reduzida. Se um download exigir revogação imediata, intermediar o arquivo pela API.
- Para planilhas futuras, validar estrutura XLSX e expansão do ZIP, limitar tamanho, entradas, linhas e tempo de processamento. Tratar fórmulas e links como dados não confiáveis e exportar texto de usuário como texto. Conservar originais em bucket privado somente pelo período definido.
- Certificados fiscais, quando exigidos, ficam criptografados em repouso com chave separada e acesso exclusivo do executor fiscal autorizado. Configurar rotação, revogação e expiração por prestador, tenant e ambiente. Credenciais e arquivos fiscais permanecem restritos por política de acesso e retenção.
- Webhooks validam assinatura e origem conforme o provedor, resolvem tenant a partir de identificadores vinculados no servidor e persistem o evento e job de forma idempotente. Impedir que um evento vinculado a uma conta produza efeito em outra.

## Verificação para liberação

| Domínio | Evidência para a primeira entrega |
| --- | --- |
| Autenticação e sessão | Login, recuperação, convite e MFA com Auth local e caixa de e-mail de teste. Validação de assinatura, emissor, audiência, expiração e escopo. |
| Autorização | Teste negativo de permissão, empresa inativa, licença vencida e suporte fora da atribuição. Teste de API e de RLS real com usuários de duas empresas. |
| Configuração, entrada e segredos | CORS e redirecionamentos testados, lint, builds com lista explícita de arquivos, varredura de segredos e redação observável. |
| Operação e dados | Evidência de migração e recuperação em ambiente isolado antes do uso de dados reais. Storage, filas, uploads e webhooks têm testes junto de seus módulos. |

Referenciar o OWASP ASVS 5.0.0 para detalhar critérios de segurança de aplicação e registrar evidência por controle implementado. [OWASP ASVS 5.0.0](https://asvs.owasp.org/)
