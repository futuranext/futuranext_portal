# Segurança e privacidade

Atualização de cobrança (14/11/2026): Cobrança Asaas desabilitada por padrão e ambiente sandbox separado. Sem dados de cartão, CPF/CNPJ apenas em trânsito ao provedor, destinatário cifrado. Webhook com segredo independente e status consultado no provedor. Operações incertas não repetem; preço e tenant vêm do servidor. Ver docs/BILLING.md.

Requisitos de segurança. Na Fase 3, autenticação, CSRF, hashes, criptografia de conteúdo, escopos/tenant, limites, consentimento, opt-out e auditoria foram implementados. HTTPS público, MFA, proteção/criptografia de disco, backup/restore e rotação completa de chaves continuam pendentes. Ambiente disponível somente em loopback, não apto a publicação em produção.

## Identidade

Atualização 14/11/2026: cadastro depende do e-mail confirmado, com papel/tenant definidos no servidor. Recuperação usa token de 30min/uso único e incremento atômico da versão da autenticação para invalidar sessões antigas. global_admin pode escolher conta por X-Tenant-ID após validação da sessão; acesso auditado. Cabeçalho rejeitado para clientes e API Keys. Consentimentos manuais/legados não liberam envios: exige confirmação no canal email_zoho, sempre subordinada ao opt-out. Zoho compartilhado pelos e-mails de cadastro, recuperação e consentimento. Limites antes da produção em docs/CONSENT_EMAIL.md.

Senhas Argon2id calibrado no host; sem senha padrão. Bootstrap por comando local interativo. Sessões opacas aleatórias, hash no banco, TTL 8h, rotação no login, revogação no logout; cookies HttpOnly/Secure/SameSite, CSRF, Origin e CORS allowlist. Login limitado por IP/conta, respostas uniformes. MFA antes de operação comercial.

API Keys: 32 bytes aleatórios, prefixo e hash SHA-256/HMAC com pepper externo; segredo exibido apenas na criação, no-store. Revogação imediata; rotação limitada. Toda operação exige contexto da empresa e escopo/instância. Testar isolamento em listas, acesso direto, mutações, SSE, QR, mídia e filas.

## Rede, segredos e dados

Gateway/bancos sem portas publicadas. Produção: HTTPS Caddy, ingresso interno não roteado, secrets montados, usuários de banco mínimos e credenciais diferentes por ambiente. .env local ignorado e protegido por ACL. Não imprimir Compose expandido, Authorization, apikey ou URIs. Acesso Docker equivale a acesso privilegiado aos dados.

Volumes devem ser protegidos por ACL e criptografia do host (BitLocker ou equivalente Linux); essa configuração de sistema não foi alterada. Conteúdo e segredos reversíveis do MVP usam Fernet/cryptography com chave externa ao banco; rotação versionada completa ainda pendente. Senhas/API Keys apenas hash. Backend/worker/health já usam readWrite no banco platform; root fica restrito ao provisionamento e operações administrativas.

QR é credencial de pareamento: administrador autorizado, no-store, sem logs/CDN/persistência. Detalhes de saúde restritos; probes públicos mínimos. Logs JSON por allowlist: request_id, IDs opacos, ação, duração e status. Sem conteúdo, telefone, payload bruto, QR ou segredo. Auditoria sem endpoint de alteração; não prometer imutabilidade contra administrador do host.

## Abuso, mídia e webhooks

Rate limit atômico Redis por IP/usuário/chave/empresa; envio fail-closed se indisponível. Consentimento/opt-out revalidado no worker. Recebimento de SAIR/PARAR inequívocos cria supressão; disponibilizar revogação manual. Não implementar disparo não autorizado ou evasão.

Fase 4: PDF/JPEG/PNG até 10 MB; validar magic bytes, quarentena/scanner, storage privado, nomes aleatórios. Proibir SVG/HTML/executáveis/ZIP/caminhos fornecidos. Download autenticado attachment+nosniff. Sem URLs arbitrárias de mídia no MVP.

Webhooks de saída: só HTTPS e destinos aprovados; bloquear loopback, privados, link-local, metadata e IPv6 equivalentes. Validar DNS a cada tentativa e conectar ao IP validado com TLS/SNI correto, impedindo rebinding. Sem redirects, resposta/tempo/conexões limitados; segredo cifrado, nunca em URL. Ingresso valida segredo em header, esquema e associação da instância; payload do gateway não é confiável.

## Privacidade e liberação

Mapear controlador/operador, finalidade, base legal e fornecedores antes de uso real. Retenção de DATABASE.md é proposta, não prazo legal universal. Exportação/expurgo incluem gateway, arquivos e backups. Backup cifrado com chave separada, ensaio isolado e evidências; não restaurar sessões reais em staging nem conectar duas cópias do mesmo número.

Antes de produção: testes de isolamento, rotação/revogação, opt-out, ausência de segredos em logs, SSRF, limites, restore, análise de dependências/imagens, autenticação/HTTPS, atribuição/licença. WhatsApp Web não oficial mantém risco de restrição, desconexão e incompatibilidade.
