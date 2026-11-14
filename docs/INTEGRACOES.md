# Integrações e dependências externas

Registro da Fase 0 em 14/11/2026. A landing é executável com seus arquivos estáticos. O chat comercial usa respostas locais e pode abrir um rascunho em um canal configurado mediante ação do visitante. As contas e credenciais abaixo serão necessárias quando cada integração for ativada.

## Primeira implementação

| Serviço | Configuração prevista | O que validar |
| --- | --- | --- |
| Supabase Auth | URL do projeto, chave publicável, emissor e JWKS do projeto. Chave administrativa em segredo do backend, com uso restrito às operações de provisionamento. Cadastro público desabilitado quando o portal for por convite. | Login, recuperação, convite para conta nova, adesão de conta existente, MFA TOTP, logout, expiração e rotação de chave. |
| Supabase PostgreSQL | Projetos separados para HML e PROD, CA para TLS e credenciais distintas de migração e runtime. Usar conexão direta com IPv6 ou Supavisor em modo sessão quando a origem usar apenas IPv4. | Migrações, privilégios, RLS, limites de conexões e backup/restauração em banco isolado. |
| SMTP | Provedor, domínio/remetente autorizado, DNS de autenticação, credenciais por ambiente, templates de convite/recuperação e destinos permitidos. Usar Mailpit no Supabase local. | Fluxos com destinatários de teste e controle de taxa. O SMTP padrão do Supabase é restrito e voltado a desenvolvimento. |
| GitHub, Cloudflare e DigitalOcean | Repo privado, domínio e DNS sob controle do operador, configuração de Pages, imagem de API, Droplet e credenciais operacionais distintas por ambiente. | Build de HML, promoção autorizada, DNS/TLS, firewall, alertas e recuperação. |

Referências de configuração. [Auth local e e-mail de teste](https://supabase.com/docs/guides/auth/testing-auth-flows) · [SMTP próprio](https://supabase.com/docs/guides/auth/auth-smtp) · [Conectar ao PostgreSQL](https://supabase.com/docs/guides/database/connecting-to-postgres) · [Implantação](DEPLOY_DIGITALOCEAN.md)

Para convite a e-mail novo, usar o fluxo administrativo de Supabase Auth com registro pendente na aplicação. Para conta existente, autenticação e confirmação de um convite da empresa vinculam a nova associação, com revalidação de e-mail confirmado. Os botões de convite e recuperação apontam para rotas fixas do frontend. Testar uso único, expiração, erro e revogação, inclusive o efeito de scanners de link. O fluxo administrativo de convite do Supabase tem restrição sobre PKCE. Validar a implementação com templates via código de uso único ou token validado após ação explícita. [Convite administrativo no Supabase](https://supabase.com/docs/reference/python/auth-admin-inviteuserbyemail) · [Templates de e-mail](https://supabase.com/docs/guides/auth/auth-email-templates)

## Produtos e conectores futuros

| Serviço | Dependências e validação previstas |
| --- | --- |
| WhatsApp Meta Cloud API | Conta empresarial, WABA, aplicativo, número autorizado, credenciais, webhook HTTPS, templates aplicáveis e opt-in. Validar assinatura, isolamento por conta, deduplicação, políticas de mensagens e tarifa por mercado/categoria. [WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/overview) · [Webhooks da Meta](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/) |
| Provedores WhatsApp opcionais | Verificar se a oferta contratada usa API oficial, consentimento, retenção, suporte, assinatura e custo. Uma integração por sessão WhatsApp Web/QR é não oficial e tem risco de interrupção e bloqueio. Estabelecer contrato de adaptador comum e executar testes com provedor fake identificado. |
| NFS-e nacional e municípios | Identificar município, prestador, autorização e requisitos tributários. Conferir leiautes, versões, endpoints, certificado e poderes de emissão por conector. Validar produção restrita com autorização antes de usar produção. Registrar documento autorizado, protocolo e artefatos realmente disponibilizados. Após resposta incerta, consultar o provedor antes de reenviar. [Documentação técnica vigente da NFS-e](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual) |
| Supabase Storage | Buckets privados e políticas separadas por ambiente e tenant. Exercitar acesso direto, URL temporária e restauração independente de objetos XLSX, XML, PDF e anexos. [Buckets privados e públicos](https://supabase.com/docs/guides/storage/buckets/fundamentals) |
| URLs e aplicações externas | Cadastrar provedor/destino com aprovação operacional e validar autorização no momento de acesso. Definir proteção do destino, token de escopo mínimo, timeout e rastreabilidade. Robôs Python executam como jobs com entradas e limites validados. |
| IA e cobrança futura | Selecionar após definir contrato, retenção, custos, idempotência e modelo de acesso. Cobrança atualiza licenças via evento auditado e conciliável. IA só usa dados autorizados e limites por tenant/produto. |

Separar segredos dos ambientes e manter credenciais reais fora de arquivos do workspace e dos testes. Documentar de quem depende a habilitação de cada provedor no momento da implementação. [Controles de segurança](SEGURANCA.md)
