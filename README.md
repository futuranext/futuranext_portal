# FuturaNext

Plataforma multiempresa com landing page estática, frontend em JavaScript, API FastAPI e Supabase Auth/PostgreSQL. A primeira entrega cobre login, recuperação de senha, administração de empresas e usuários, catálogo, licenças e portal com permissões por empresa. A configuração local usa Docker e Mailpit e funciona sem contratar serviços.

## Executar localmente

Requer Python 3.12 ou superior, Node.js 22 ou superior, npm e Docker com a CLI Compose. O primeiro setup precisa de rede para baixar pacotes e imagens públicas. Use a raiz do repositório.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e './backend[dev]'
.venv/Scripts/python.exe scripts/local.py setup
.venv/Scripts/python.exe scripts/local.py serve
```

No Linux ou macOS, use `.venv/bin/python` no lugar de `.venv/Scripts/python.exe`.

O setup inicia o Supabase CLI local em uma rede Docker exclusiva, verifica o bind de loopback, executa a migração com a credencial administrativa, configura a credencial de execução `fn_app` e prepara `dist/`. As credenciais geradas ficam em `.env.local`, ignorado pelo Git. A senha de `fn_app` permanece estável entre execuções do setup. A API recebe somente a credencial de execução do banco.

| Acesso | Endereço local |
| --- | --- |
| Landing, login, portal e administração | `http://127.0.0.1:8088`, com `/login`, `/portal` e `/admin` |
| OpenAPI e documentação interativa | `http://127.0.0.1:8088/openapi.json` e `http://127.0.0.1:8088/docs` |
| Healthchecks | `http://127.0.0.1:8088/health/live` e `http://127.0.0.1:8088/health/ready` |
| Supabase Studio | `http://127.0.0.1:54323` |
| Mailpit, e-mails de teste | `http://127.0.0.1:54324` |

Use `127.0.0.1` no navegador para manter a origem igual aos redirecionamentos de Auth. Se o Docker apontar erro de acesso ao socket ou pipe, abra o Docker Desktop e confira `docker version` no terminal com o qual você executa o setup. Se os serviços locais já estiverem em outra rede, execute `npx --yes supabase@3.1.0 stop` na raiz e repita o setup. A pilha Supabase reserva as portas `54321` a `54324` e a API usa `8088`.

### Primeira conta administrativa

1. Em Studio → Authentication → Users, crie um usuário local com e-mail sintético, por exemplo `admin@example.test`, senha forte e e-mail confirmado. Copie o UUID do usuário.
2. Execute `.venv/Scripts/python.exe scripts/local.py bootstrap-admin UUID`, substituindo `UUID` pelo valor anterior. O comando exige uma conta confirmada e só cria o primeiro super admin. Reexecutá-lo para o mesmo UUID é seguro.
3. Faça login em `/login` e entre em `/admin`. A tela de administração solicita cadastrar ou confirmar um autenticador TOTP. Valide o código no portal para prosseguir.
4. Em `/admin`, cadastre a primeira empresa, configure o catálogo e as licenças e convide uma conta de teste da empresa. Abra o Mailpit, siga o convite e defina a senha. Entre com o usuário convidado para verificar o portal e o catálogo da empresa. A tela de recuperação também usa Mailpit.

Os templates mantêm os tokens de convite e recuperação no fragmento da URL. O Auth verifica o token após confirmação do visitante. Para operações administrativas no ambiente hospedado, execute a migração e o bootstrap com a credencial de migração em uma sessão operacional isolada. [Guia de implantação](docs/DEPLOY_DIGITALOCEAN.md).

Conceda suporte por empresa em uma sessão administrativa com `FN_MIGRATION_DATABASE_URL` carregada. A partir de `backend/`, use `python -m app.db_admin assign-support UUID_USUARIO UUID_EMPRESA` para conceder e `python -m app.db_admin revoke-support UUID_USUARIO UUID_EMPRESA` para revogar. Use uma identidade com e-mail confirmado no Auth. O acesso do suporte exige MFA e uma atribuição ativa à empresa.

### Executar a API em contêiner

Depois de `setup`, com a API nativa parada, execute `python scripts/local.py docker-up` com o Python da `.venv`. O Compose aguarda a readiness, conecta o contêiner à rede exclusiva do Supabase e monta `dist/` somente para leitura. A API continua disponível em `127.0.0.1:8088`. Para encerrar o contêiner, execute `python scripts/local.py docker-down`. Para parar também o Supabase preservando os dados locais, execute `npx --yes supabase@3.1.0 stop` na raiz.

Depois de editar o frontend, execute `python scripts/local.py build` para recompilar com a configuração da pilha local. Execute `python scripts/local.py migrate` após receber novas migrações.

## Site público e configuração comercial

Abra `index.html` diretamente ou execute `python -m http.server 5500` na raiz para usar só a landing page. As rotas do portal e da administração precisam da plataforma local. O script de publicação usa `npm ci` e `npm run build`. A saída em `dist/` traz somente arquivos públicos.

Edite `config.js` para configurar os contatos comerciais. `whatsapp` usa DDI, DDD e número, só dígitos. Configure `email` com a caixa comercial. Com os campos vazios, o visitante ainda pode copiar ou baixar o resumo. Os CTAs abrem o chat guiado, que mantém as respostas na memória da aba. A abertura de WhatsApp ou e-mail cria um rascunho e o visitante decide se envia. Os nove cases são ilustrativos e os três depoimentos são fictícios.

## Testes, piloto e publicação

```powershell
.venv/Scripts/python.exe -m ruff check backend scripts
.venv/Scripts/python.exe -m pytest -q backend
node docs/fixtures/landing-dom-smoke.cjs
```

Os comandos para executar os testes de PostgreSQL em um banco descartável e os resultados desta entrega estão em [TESTES.md](docs/TESTES.md). A CI verifica a landing, compilação do frontend, lint, pytest com PostgreSQL, histórico de segredos e build da imagem. A promoção de uma release é manual e requer aprovação.

O piloto hospedado usa o **Supabase Free**, sem mensalidade, e pode usar um Droplet de 1 GiB por **US$ 6,00/mês**, ou **US$ 7,20/mês** com backup semanal opcional do host. O projeto Free pode pausar após uma semana de inatividade. Esses custos são referências de infraestrutura. [Custos e limites](docs/CUSTOS.md).

- [Arquitetura e decisões](docs/ARQUITETURA.md)
- [Implantação, ambientes e rollback](docs/DEPLOY_DIGITALOCEAN.md)
- [Integrações, e-mail de autenticação e Zoho](docs/INTEGRACOES.md)
- [Controles de segurança](docs/SEGURANCA.md)

Chatbots, agenda e emissão de NFS-e são módulos planejados. O catálogo e o licenciamento já permitem controlar quais produtos aparecem no portal, e os conectores externos serão desenvolvidos e homologados nas fases próprias.
