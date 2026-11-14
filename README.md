# FuturaNext — Site demonstrativo

Landing page estática e responsiva, com identidade visual própria, feita em HTML, CSS e JavaScript sem framework e sem backend.

## Testar localmente

**Opção A:** abra `index.html` com Chrome ou Edge. A maior parte dos recursos funciona sem servidor.

**Opção B (recomendada):** no VS Code, abra a pasta e use a extensão Live Server, ou, se tiver Python instalado:

```bash
cd futuranext_site
python -m http.server 5500
```

Navegue até `http://localhost:5500`.

## Configuração comercial

Edite `config.js`:

```js
window.FUTURANEXT_CONFIG = {
  whatsapp: "5511999999999", // trocar por seu número com DDI e DDD; só dígitos
  email: "contato@seudominio.com.br",
  companyName: "FuturaNext"
};
```

**Importante:** o número e o e-mail do exemplo acima NÃO são dados reais da empresa. Quando `whatsapp` estiver vazio, o botão de envio via WhatsApp não aparece. O chat sempre oferece baixar ou copiar o resumo.

## Como o chat funciona

- Botões **Começar agora**, **Quero meu diagnóstico gratuito**, **Quero avaliar uma automação** e CTAs dos cards levam ao MESMO chat.
- Conversa guiada com nome, empresa, área, descrição da necessidade, urgência, contato e autorização.
- As respostas ficam em memória na aba atual e podem ser baixadas em `.txt` ou copiadas.
- Com o WhatsApp definido no `config.js`, o chat abre uma mensagem pré-preenchida no WhatsApp. **O usuário ainda precisa enviá-la manualmente**.
- Com e-mail configurado, a opção de e-mail abre o cliente de e-mail do visitante. **O envio não é automático**.
- Não existe IA real, banco de dados, serviço de e-mail, CRM ou captura automática de leads nesta versão.
- Não é uma solução de consentimento LGPD pronta para produção; para salvar dados em servidor, implementar política de privacidade, segurança e retenção.

## Cases e depoimentos

Os nove cases são **cenários ilustrativos**, e os três depoimentos são **fictícios**, explicitamente identificados. Substitua-os por informações autorizadas e verificáveis antes de divulgar o site comercialmente. Evite inventar métricas, nomes e avaliações.

Para trocar cases, edite a lista `cases` no início do arquivo `script.js`. Para alterar depoimentos e Quem Somos, edite `index.html`.

## Estrutura

- `index.html` — páginas/seções, componentes do chat e SEO básico.
- `styles.css` — layout, animações simples, responsividade e identidade visual.
- `script.js` — navegação, calculadora, filtros de cases e chat guiado.
- `config.js` — configurações comerciais.
- `assets/logo-mark.svg` — símbolo vetorial FuturaNext.
- `assets/favicon.svg` — ícone da aba.

## Publicação

Por ser estático, funciona em serviços como Cloudflare Pages, GitHub Pages, Netlify ou Render Static Sites, conforme planos e condições disponíveis no momento da publicação. Não precisa de um servidor Python.

**Antes do lançamento oficial**: validar uso do nome FuturaNext e do domínio, atualizar conteúdo e contatos, implementar política de privacidade, revisar acessibilidade/SEO, e integrar o chat a backend ou CRM caso queira receber leads sem depender do WhatsApp ou e-mail do visitante.
