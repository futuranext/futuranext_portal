# Testes e evidências

## Fase 0 · 14/11/2026

| Verificação | Resultado |
| --- | --- |
| Inventário de `README.md`, HTML, CSS, dois scripts e dois SVGs | Arquivos lidos e hashes de referência registrados. [Diagnóstico](DIAGNOSTICO_ATUAL.md) |
| Sintaxe de `script.js` e `config.js`, Node.js v24.16.0 | Ambos com código de saída 0. |
| Ensaio de lógica por `node:vm` com DOM simulado | Passaram menu, `aria-expanded`, calculadora, nove cases, filtros combinados, validação de nome, autorização e recusa, cópia simulada do resumo, referência de case e retorno de foco. |
| Landing em navegador real | Chrome headless não gerou DOM no sandbox e o navegador integrado bloqueou o protocolo `file:`. Uma tentativa de HTTP local recebeu `WinError 10013`. A verificação visual e das APIs reais de download e clipboard ficou pendente. |
| Python e Compose | Python 3.13 instalado no host. Docker CLI 28.0.4 e Compose 2.34.0 disponíveis. `docker version` recebeu `Access is denied` no pipe do Docker Desktop neste ambiente. |

```powershell
node --check script.js
node --check config.js
node docs/fixtures/landing-dom-smoke.cjs
Get-FileHash -Algorithm SHA256 -LiteralPath README.md,config.js,index.html,script.js,styles.css,assets/logo-mark.svg,assets/favicon.svg
py -0p
node --version
docker version --format '{{.Client.Version}} {{.Server.Version}}'
docker compose version
```

O ensaio usa apenas módulos nativos do Node.js, roda na raiz do projeto e emprega entradas sintéticas. As asserções verificam a lógica JavaScript, com simulação de eventos, controles, foco e clipboard.

Para reproduzir a verificação da landing em um ambiente com loopback disponível, execute `python -m http.server 5500 --bind 127.0.0.1` na raiz e abra `http://127.0.0.1:5500/`. A página também pode ser aberta diretamente no navegador do operador. Use entradas sintéticas no chat e mantenha a configuração comercial entregue para os testes locais.

1. Verifique a composição em larguras de 390, 768 e 1.440 px. Em 390 px, expanda o menu, siga o link Soluções e confirme o fechamento do menu.
2. Confirme `R$ 2.800` na calculadora. Mantendo os demais valores iniciais, ajuste horas para 2,5 e confirme `R$ 3.500`.
3. Verifique os nove cases iniciais e a indicação de conteúdo ilustrativo. Busque `conciliacao` sem acento, selecione `Financeiro` e `Automação`, e confira a contagem de um exemplo. Troque o segmento para `RH` e confira zero. Verifique os três depoimentos demonstrativos e a navegação para Quem Somos.
4. Acione o CTA de um case. Confira que um nome de um caractere gera mensagem de validação. Preencha o roteiro com dados fictícios, como `Pessoa Teste`, `Empresa Teste` e `teste@example.invalid`. Confirme que `Não autorizo` encerra o roteiro sem oferecer resumo.
5. Reinicie, preencha novamente, aceite pelo botão `Sim, autorizo` e confira o título do case em `Baixar resumo (.txt)` e `Copiar resumo`. Confira que os botões de envio via canal permanecem ocultos com a configuração recebida. Feche com `Escape` e verifique a restauração do foco ao CTA. Teste a navegação por teclado no diálogo.

## Matriz de validação da Fase 1

Os itens abaixo são critérios planejados. A Fase 0 usa os arquivos estáticos originais e ainda carece de API, migrações e suíte `pytest`. Registrar comandos definitivos e resultados na implementação.

| Camada | Cenários a demonstrar |
| --- | --- |
| API e contratos | OpenAPI coerente com as rotas; liveness e readiness com banco presente, ausente e schema incompatível; erro de entrada; origens permitidas e recusadas no CORS. |
| JWT | Aceitar sessão válida; rejeitar assinatura, algoritmo, emissor, audiência, expiração ou `sub` inválidos. Conferir rotação de JWKS e comportamento quando a verificação estiver indisponível. |
| Auth completo | Fluxo com Supabase local e Mailpit para login, convite, ativação, recuperação, expiração, revogação de convite, TOTP e logout. Verificar que e-mail confirmado errado não aceita convite e que uma conta pode integrar mais de uma empresa. |
| RBAC e licenciamento | Conta A lista apenas produtos com licença e ação permitida na empresa selecionada. Cobrir licença vencida, inativa ou ausente, empresa suspensa e permissão negada. Suporte só opera empresas atribuídas. Operações administrativas exigem MFA. |
| Isolamento pela API | Duas empresas sintéticas com IDs e dados distintos. Leitura, atualização e concessão de licença em empresa não autorizada devem falhar. Troca deliberada de contexto por conta multiempresa só retorna dados da empresa selecionada. Testar alterações de escopo e tentativas de delegação acima do papel do ator. |
| PostgreSQL e RLS | Rodar com PostgreSQL real e role de runtime. Sem contexto, acesso é negado. Validar leitura, escrita e atualização por tenant, políticas `WITH CHECK`, integridade de vínculos, privilégios diretos e limpeza após `COMMIT`/`ROLLBACK` e reuso da conexão. Exercitar vínculo cruzado e divergência entre tenant do contexto e o da linha. |
| Migração e bootstrap | Aplicar migrações em banco limpo e ver o efeito do upgrade em banco sintético. Testar compatibilidade com reversão documentada. Validar bootstrap repetido ou concorrente, UUID incorreto, auditoria e recusa de sessão sem `aal2`. |
| Frontend e publicação | Repetir regressão da landing, verificar rotas acessadas diretamente em local e Pages, navegar com teclado e tela pequena, alternar empresas sem estado residual e inspecionar a lista de arquivos de `dist/`. Conferir que saída de build e logs contêm apenas configuração pública. |

Planejar `python -m pytest`, `docker compose config --quiet`, testes com stack Supabase e jobs de CI em ambiente limpo. A validação com Postgres e Auth reais depende de um daemon Docker permitido ou ambiente de testes dedicado. Testes com retornos sintéticos do provedor cobrem falhas controladas e complementam os fluxos reais. Testes negativos de Storage, cache, jobs, anexos, webhooks e exportações serão incluídos quando esses recursos forem implementados.
