# Diagnóstico do site original

A FuturaNext chegou ao workspace como uma landing page em português do Brasil, com HTML, CSS e JavaScript sem framework. O inventário abaixo descreve o conteúdo recebido antes da evolução para SaaS. A pasta extraída continha sete arquivos e não tinha metadados Git.

## Inventário

| Arquivo | Responsabilidade |
| --- | --- |
| `index.html` | Cabeçalho e menu, hero, dores, Soluções, calculadora, Cases, Depoimentos, Quem Somos, processo de atendimento, CTAs, rodapé e o diálogo do chat. Define metadados básicos de busca e compartilhamento. |
| `styles.css` | Paleta azul-marinho e verde, tipografia, grades, cartões, ilustrações CSS, estados de interação e comportamento responsivo. Importa DM Sans e Outfit do Google Fonts com fontes de fallback. |
| `script.js` | Menu móvel, cálculo de custo, dados e filtros dos cases e fluxo do chat de diagnóstico. |
| `config.js` | Configuração comercial pública no navegador. O nome configurado é FuturaNext e os valores de WhatsApp e e-mail estão vazios. |
| `assets/logo-mark.svg` | Símbolo vetorial da marca, referenciado pelo cabeçalho, hero, Quem Somos, rodapé e chat. |
| `assets/favicon.svg` | Versão reduzida do símbolo para a aba do navegador. |
| `README.md` | Instruções para abrir a landing ou servi-la localmente, configurar contatos e entender o caráter demonstrativo do conteúdo. |

## Comportamento a preservar

- O menu de âncoras dá acesso a Soluções, Cases, Depoimentos e Quem Somos. As regras responsivas mudam a composição a 1.050, 800 e 560 px. A 800 px o menu passa a ter botão de abertura e a 560 px o chat passa a ocupar a altura da tela. Há um ajuste para a preferência de movimento reduzido.
- A calculadora multiplica pessoas, horas diárias e 22 dias úteis pelo custo mensal individual dividido por 220 horas. Para os valores iniciais de 4 pessoas, 2 horas diárias e R$ 3.500 mensais por pessoa, o resultado é R$ 2.800. A página identifica a simulação como ilustrativa.
- Cases exibe nove cenários ilustrativos. A busca ignora maiúsculas e acentos e combina com os filtros por segmento e área. O CTA de um case registra seu título como referência no resumo da conversa. A seção Depoimentos identifica os três perfis como ilustrativos e os textos como demonstrativos. O rodapé reforça a identificação dos dados fictícios.
- Os CTAs levam ao mesmo chat local. O diálogo pergunta nome, empresa, área, problema, urgência, contato e autorização. Pode ser fechado via botão, clique no fundo ou `Escape` e devolve o foco ao acionador. A abertura de uma nova conversa redefine as respostas em memória.
- O visitante pode baixar um `.txt` ou copiar o resumo. Com valores comerciais válidos em `config.js`, pode abrir o WhatsApp com mensagem pré-preenchida ou um rascunho no cliente de e-mail e concluir o envio manualmente. A configuração entregue deixa essas duas opções ocultas. O chat executa um roteiro fixo e não transmite nem armazena as respostas em um serviço.

## Lacunas para a plataforma

- A versão recebida tem uma página pública. Login, recuperação de senha, portal, administração, API, banco e autorização são capacidades novas. A landing pode continuar como página estática com os mesmos arquivos públicos. O portal e a área administrativa terão pontos de entrada e código próprios.
- O código do site público guarda as respostas do chat em memória. A pergunta de autorização aceita texto livre e apenas a resposta literal `Não autorizo` interrompe a geração do resumo. Antes de implementar persistência de leads, exigir uma escolha inequívoca e definir aviso de privacidade, retenção e descarte. A autorização exibida hoje integra o fluxo demonstrativo.
- Nome, empresa e problema entram no DOM como texto. O campo de telefone faz uma validação superficial por quantidade mínima de dígitos. Ao configurar contatos públicos, confirmar destino e formato. O resumo encaminhado via WhatsApp ou e-mail pode conter informações pessoais digitadas pelo visitante.
- As fontes externas influenciam a tipografia e requerem conexão na primeira visita quando não estiverem em cache. O CSS define alternativas locais. Releases que adicionem CSP precisam tratar a origem das fontes ou hospedá-las mantendo a família tipográfica.
- A landing inclui semântica do diálogo, região de mensagens ao vivo e tratamento básico de foco. A revisão de acessibilidade deve exercitar teclado, leitores de tela e contraste, especialmente na conclusão do chat, quando o formulário é ocultado após o envio da última resposta.
- A publicação comercial depende de contatos aprovados e de manter a identificação de conteúdo ilustrativo enquanto não houver substituição autorizada. Os chatbots de produtos, emissão fiscal, chamados, auditoria e integrações exigem implementação própria conforme as fases do projeto.

## Referência de regressão

Sintaxe original de `script.js` e `config.js` validada com Node.js v24.16.0. Um ensaio em memória com `node:vm` e elementos DOM simulados passou para o menu, valores inicial e atualizado da calculadora, busca sem acentos e filtros combinados, validação de resposta, autorização e recusa, cópia do resumo com referência ao case e devolução do foco com `Escape`. A leitura de código também confirmou a ausência de chamadas de API ou persistência de respostas no navegador. Os hashes foram coletados antes de qualquer alteração nas fontes públicas.

O Chrome headless terminou sem gerar DOM no sandbox e a conexão HTTP local recebeu WinError 10013. O ensaio em memória cobre a lógica JavaScript. A aparência, o layout responsivo, o download e a integração com a área de transferência do navegador ainda exigem conferência em navegador real.

```powershell
node --check script.js
node --check config.js
node docs/fixtures/landing-dom-smoke.cjs
Get-FileHash -Algorithm SHA256 -LiteralPath README.md,config.js,index.html,script.js,styles.css,assets/logo-mark.svg,assets/favicon.svg
```

| Arquivo recebido | SHA-256 |
| --- | --- |
| `README.md` | `AA62A820720B38BFD6EC004861BCC7A0BB4E20105CDFA9ECB390A7A9865899D7` |
| `config.js` | `4CD8F0B8632DA3985EF32752E8E9E9FE0FD6FD69925509B44A9EADF52A383E8A` |
| `index.html` | `F9E0A66E32F1FA92B6F5D62CDFFC5AD57D25CB81318367038839F351917458DF` |
| `script.js` | `F339D2D41B9EFF6EC53C786220AF7C7A2F389409BC4B65C612B7262C0C7B8D85` |
| `styles.css` | `250C905F57C2A300949CF807C363C6132580F4CFA3C9DC65BEB46BB1F674096A` |
| `assets/logo-mark.svg` | `457014DBB23F63C1C98278014191F60B2E21D0E07862251A3F63F45AFA3683DB` |
| `assets/favicon.svg` | `27FAAA3C192016FA1113E6D4EEC660C5F04DBAB2C932AD38A0EE55DD4A7B84F1` |
