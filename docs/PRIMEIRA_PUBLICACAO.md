# Primeira publicação da FuturaNext

Este guia publica o site e o portal em `https://futuranext.com.br`, com a API em `https://api.futuranext.com.br`. Use **PowerShell no Windows** para os comandos locais e **SSH no Droplet** para os comandos do servidor. Substitua os valores em maiúsculas nos exemplos pelos que os painéis fornecerem.

O piloto usa Cloudflare Free, Supabase Free e um Droplet Basic Regular de 1 GiB por **US$ 6/mês**, antes de impostos e backup opcional. O backup semanal leva essa estimativa a **US$ 7,20/mês**. O Supabase Free pode pausar após uma semana de inatividade. [Cloudflare Pages](https://developers.cloudflare.com/pages/functions/pricing/) · [Supabase](https://supabase.com/pricing) · [DigitalOcean](https://www.digitalocean.com/pricing/droplets) · [Backups da DigitalOcean](https://docs.digitalocean.com/products/backups/details/pricing/)

## 1. Crie o projeto Supabase Free

1. Abra o [painel do Supabase](https://supabase.com/dashboard), crie sua conta e entre. Crie uma organização no plano **Free** se o painel solicitar. Em **New project**, crie `futuranext`, defina uma senha forte e única para o banco e guarde-a em seu gerenciador de senhas. Selecione **South America (São Paulo)** se aparecer para o projeto. Aguarde ficar ativo. [Regiões disponíveis](https://supabase.com/docs/guides/platform/regions)
2. No botão **Connect**, copie a **Project URL**, como `https://REF.supabase.co`, e a **Publishable key** (`sb_publishable_…`). Ainda em **Connect**, selecione **Session pooler** e anote o host exibido e a referência `REF`. Esse host usa a porta `5432`. Em **Settings → API Keys**, identifique a chave **Secret** (`sb_secret_…`) para uso no servidor. Em **Database Settings → SSL Configuration**, baixe a CA do Supabase e salve-a no Windows como `Downloads\supabase-ca.crt`. [Chaves do Supabase](https://supabase.com/docs/guides/getting-started/api-keys) · [Conexões PostgreSQL](https://supabase.com/docs/guides/database/connecting-to-postgres) · [Certificado do banco](https://supabase.com/docs/guides/database/psql)
3. Em **Authentication → URL Configuration**, defina a **Site URL** como `https://futuranext.com.br`. Em **Redirect URLs**, adicione `https://futuranext.com.br/ativar-conta` e `https://futuranext.com.br/recuperar-senha`. [URLs de redirecionamento](https://supabase.com/docs/guides/auth/redirect-urls)
4. Em **Authentication → Sign In / Providers**, mantenha o provedor **Email** ativo e a confirmação de e-mail habilitada. Desative cadastro público e login anônimo. Em **Settings → JWT Signing Keys**, confira que a chave atual usa **RS256** ou **ES256**. O backend valida essas assinaturas em produção. Se a chave atual for a legada, siga o procedimento de migração e rotação do Supabase. [Opções de autenticação](https://supabase.com/docs/guides/auth/general-configuration) · [Chaves JWT](https://supabase.com/docs/guides/auth/signing-keys)
5. Em **Authentication → Users**, use **Add user → Create new user**. Crie seu administrador, marque a confirmação de e-mail e copie o **User UID**. Use uma senha com pelo menos 12 caracteres. Guarde o UUID para o passo 4. Essa conta é a que você vai usar no domínio público. [Usuários no Supabase](https://supabase.com/docs/guides/auth/users)

Para ativar **convites e recuperação de senha**, configure o SMTP do Zoho em **Authentication → Emails → SMTP Settings**. Use host, porta e credenciais exibidos na sua conta Zoho. Se a conta exigir, use uma senha de aplicativo. Depois, em **Email Templates**, cole [invite.html](../supabase/templates/invite.html) em **Invite user** e [recovery.html](../supabase/templates/recovery.html) em **Reset password**. O SMTP próprio libera a personalização dos modelos nos projetos Free novos. A conta administrativa confirmada no painel pode entrar e configurar o TOTP antes dessa etapa de e-mail. [SMTP no Zoho](https://www.zoho.com/mail/help/zoho-smtp.html) · [SMTP no Supabase](https://supabase.com/docs/guides/auth/auth-smtp) · [Regra dos modelos no Free](https://supabase.com/changelog/46599-changes-to-email-template-customisation-on-free-tier)

## 2. Crie o Droplet na DigitalOcean

1. No **PowerShell**, crie uma chave SSH com senha de proteção. Copie a chave pública e guarde a chave privada no seu computador.

   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.ssh" | Out-Null
   $chave = "$env:USERPROFILE\.ssh\futuranext_do"
   ssh-keygen -t ed25519 -C "futuranext" -f $chave
   Get-Content "$chave.pub" | Set-Clipboard
   ```

2. Crie uma conta na [DigitalOcean](https://cloud.digitalocean.com/), configure o pagamento e abra **Create → Droplets**. Escolha **Ubuntu 24.04 LTS x64**, **Basic → Regular → 1 GiB** e uma região onde o plano esteja disponível, como Nova York. Em **Authentication**, escolha **SSH Key** e cole a chave pública. Revise o preço e as opções de backup antes de clicar em **Create Droplet**. Anote o IPv4 público. [Criar um Droplet](https://docs.digitalocean.com/products/droplets/how-to/create/) · [Disponibilidade por região](https://docs.digitalocean.com/products/droplets/details/availability/)
3. Em **Networking → Firewalls**, associe um firewall ao Droplet. Para entrada, permita **TCP 22** a partir do seu IP público e **TCP 443** das faixas Cloudflare. Use a lista oficial de IPv4 para o registro `A` da API. Mantenha a saída padrão, usada pelo Supabase e pelas atualizações. [Firewall na DigitalOcean](https://docs.digitalocean.com/products/networking/firewalls/how-to/create/) · [IPs da Cloudflare](https://www.cloudflare.com/ips/)
4. Entre no novo servidor pelo PowerShell. Na primeira conexão, compare a impressão digital com a exibida pelo comando `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` no console do Droplet.

   ```powershell
   $ip = 'IP_PUBLICO_DO_DROPLET'
   ssh -i $chave "root@${ip}"
   ```

5. No **SSH do Droplet**, crie um usuário de operação com a mesma chave pública. O `adduser` pede uma senha local para o `sudo`. Em outro PowerShell, confirme a conexão do usuário antes de sair da sessão inicial.

   ```bash
   adduser deploy
   usermod -aG sudo deploy
   install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh
   install -m 0600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
   ```

   ```powershell
   ssh -i $chave "deploy@${ip}"
   ```

   [Configuração recomendada do Droplet](https://docs.digitalocean.com/products/droplets/getting-started/recommended-droplet-setup/)

6. Na sessão SSH com `deploy`, cole o bloco abaixo para instalar **Docker Engine e Compose** no Ubuntu 24.04 x64. O nome `noble` corresponde a essa versão do Ubuntu. [Instalação oficial no Ubuntu](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository)

   ```bash
   sudo apt-get update
   sudo apt-get install -y ca-certificates curl
   sudo install -d -m 0755 /etc/apt/keyrings
   sudo curl --fail --silent --show-error --location \
     https://download.docker.com/linux/ubuntu/gpg --output /etc/apt/keyrings/docker.asc
   sudo chmod 0644 /etc/apt/keyrings/docker.asc
   sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<'DOCKER_APT'
   Types: deb
   URIs: https://download.docker.com/linux/ubuntu
   Suites: noble
   Components: stable
   Architectures: amd64
   Signed-By: /etc/apt/keyrings/docker.asc
   DOCKER_APT
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo docker version
   sudo docker compose version
   ```

## 3. Adicione o domínio na Cloudflare e confira o e-mail

1. Crie sua conta na [Cloudflare](https://dash.cloudflare.com/). Em **Domains → Onboard a domain**, informe `futuranext.com.br`, escolha o plano **Free** e deixe a Cloudflare buscar os registros existentes.
2. No Registro.br, abra a zona DNS atual e guarde uma cópia dos registros. Compare a zona com **DNS → Records** na Cloudflare. Confira especialmente os três MX do Zoho (`mx.zoho.com`, `mx2.zoho.com`, `mx3.zoho.com`), com suas prioridades `10`, `20`, `50`, e os registros SPF, verificação do Zoho, `zmail._domainkey` e DMARC, se houver. Copie manualmente os registros que faltarem. [Busca de registros](https://developers.cloudflare.com/dns/zone-setups/reference/dns-quick-scan/) · [DNS de e-mail na Cloudflare](https://developers.cloudflare.com/dns/troubleshooting/email-issues/)
3. Depois da comparação, abra **DNS → Alterar servidores DNS** no painel do [Registro.br](https://registro.br/) e salve o par de nameservers que o painel Cloudflare atribuiu à sua zona. O Registro.br trata a retirada do DNSSEC antigo durante a troca do DNS hospedado por ele. Aguarde o status **Active** na Cloudflare. [Troca dos servidores DNS](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/) · [DNSSEC no Registro.br](https://registro.br/tecnologia/dnssec/dns-e-dnssec/)
4. Confira a delegação e os MX no PowerShell. Faça um teste de envio e recebimento no Zoho. Em seguida, em **Cloudflare → DNS → Settings → DNSSEC**, habilite o DNSSEC e cadastre no Registro.br os novos dados do registro **DS** fornecidos pela Cloudflare. [Ativar DNSSEC](https://developers.cloudflare.com/dns/dnssec/)

   ```powershell
   Resolve-DnsName futuranext.com.br -Type NS -Server 1.1.1.1
   Resolve-DnsName futuranext.com.br -Type MX -Server 1.1.1.1
   ```

## 4. Coloque a API no Droplet

### Transfira a versão e o certificado do banco

No **PowerShell do Windows**, abra a raiz do projeto, com o Docker Desktop funcionando. Os comandos abaixo usam o último commit do repositório. Se o arquivo da CA estiver em outro lugar, ajuste seu caminho no `scp`.

```powershell
Set-Location 'C:\Projetos\futuranext_site'
$rev = (git rev-parse --short HEAD).Trim()
$imagem = "futuranext-api:$rev"
$envio = New-Item -ItemType Directory -Force (Join-Path $env:TEMP "futuranext-$rev")
$src = Join-Path $envio.FullName 'futuranext-src.tar'
$tar = Join-Path $envio.FullName 'futuranext-api.tar'
docker build --platform linux/amd64 -t $imagem .
docker image save -o $tar $imagem
git archive --format=tar -o $src HEAD
scp -i $chave $src $tar "$env:USERPROFILE\Downloads\supabase-ca.crt" "deploy@${ip}:~/"
Write-Output $imagem
```

No **SSH do Droplet**, prepare a primeira versão, carregue a imagem e baixe o Caddy. Anote a tag `futuranext-api:REV` exibida no Windows para usar nos próximos comandos.

```bash
mkdir -p ~/futuranext/certs/origin
tar -xf ~/futuranext-src.tar -C ~/futuranext
cd ~/futuranext
install -m 644 ~/supabase-ca.crt certs/supabase-ca.crt
sudo docker image load -i ~/futuranext-api.tar
sudo docker pull caddy:2-alpine
```

[Exportar e carregar imagens Docker](https://docs.docker.com/reference/cli/docker/image/save/)

### Aplique as migrações e promova o administrador

No **SSH**, dentro de `~/futuranext`, execute `umask 077` e abra `nano .env.migrate`. Preencha com a senha do banco e o host do **Session pooler** copiados do painel. Gere uma segunda senha com `openssl rand -hex 24` para `SENHA_RUNTIME`. Se a senha do banco tiver caracteres reservados como `@`, `#` ou `%`, codifique apenas a senha com `[Uri]::EscapeDataString()` no PowerShell para usá-la na URL. No `nano`, salve com **Ctrl+O**, pressione **Enter** e saia com **Ctrl+X**. [Formato da conexão](https://supabase.com/docs/guides/database/connecting-to-postgres)

```dotenv
FN_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres.REF:SENHA_DO_BANCO@HOST_POOLER:5432/postgres?sslmode=verify-full&sslrootcert=/run/futuranext/supabase-ca.crt
FN_APP_DB_PASSWORD=SENHA_RUNTIME
```

Ainda no **SSH**, troque `REV` pela tag exibida e `UUID_ADMIN` pelo UUID do projeto hospedado. Execute uma linha de cada vez e prossiga após ela concluir com sucesso.

```bash
imagem='futuranext-api:REV'
ca_mount="$PWD/certs/supabase-ca.crt:/run/futuranext/supabase-ca.crt:ro"
sudo docker run --rm --env-file .env.migrate -v "$ca_mount" "$imagem" python -m app.db_admin setup-role
sudo docker run --rm --env-file .env.migrate -v "$ca_mount" "$imagem" python -m alembic upgrade head
sudo docker run --rm --env-file .env.migrate -v "$ca_mount" "$imagem" python -m app.db_admin bootstrap-admin UUID_ADMIN
```

### Configure TLS e inicie a API

1. Na Cloudflare, abra **SSL/TLS → Origin Server → Create Certificate** para `api.futuranext.com.br`. Use formato PEM. No **SSH**, salve o certificado em `certs/origin/cert.pem` e a chave em `certs/origin/key.pem`, com `nano`, ainda sob `umask 077`. O Caddy lê esses arquivos. [Cloudflare Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)
2. No **SSH**, crie `compose.certs.yaml` com o texto abaixo. Esse arquivo monta a CA pública do Supabase no contêiner da API.

   ```yaml
   services:
     api:
       volumes:
         - ./certs/supabase-ca.crt:/run/futuranext/supabase-ca.crt:ro
   ```

3. Abra `nano .env.runtime` e preencha. Use `SENHA_RUNTIME` igual à da migração. Aqui o papel do banco é `fn_app.REF` e a chave administrativa do Supabase fica apenas neste servidor.

   ```dotenv
   FN_ENVIRONMENT=production
   FN_DATABASE_URL=postgresql+psycopg://fn_app.REF:SENHA_RUNTIME@HOST_POOLER:5432/postgres?sslmode=verify-full&sslrootcert=/run/futuranext/supabase-ca.crt
   FN_SUPABASE_URL=https://REF.supabase.co
   FN_SUPABASE_ISSUER=https://REF.supabase.co/auth/v1
   FN_SUPABASE_PUBLISHABLE_KEY=SB_PUBLISHABLE_DO_PROJETO
   FN_SUPABASE_ADMIN_KEY=SB_SECRET_DO_PROJETO
   FN_AUTH_MODE=jwks
   FN_FRONTEND_URL=https://futuranext.com.br
   FN_CORS_ORIGINS='["https://futuranext.com.br"]'
   FN_OPENAPI_ENABLED=false
   FN_API_IMAGE=futuranext-api:REV
   FN_API_HOSTNAME=api.futuranext.com.br
   FN_CADDY_CERT_DIR=/home/deploy/futuranext/certs/origin
   ```

4. No **SSH**, confira e inicie os serviços. O Compose usa as imagens carregadas no Droplet.

   ```bash
   chmod 600 .env.migrate .env.runtime certs/origin/key.pem
   sudo docker compose --env-file .env.runtime -f compose.yaml -f infra/compose.proxy.yaml -f compose.certs.yaml config --quiet
   sudo docker compose --env-file .env.runtime -f compose.yaml -f infra/compose.proxy.yaml -f compose.certs.yaml up -d --no-build --pull never --wait
   ```

   [Comando `docker compose up`](https://docs.docker.com/reference/cli/docker/compose/up/)

5. Na Cloudflare, selecione **SSL/TLS → Overview → Full (strict)**. Em **DNS → Records**, adicione um registro **A** com nome `api`, IP público do Droplet e **Proxied** ativado. Espere o certificado de borda ficar disponível e confira a API no PowerShell. O resultado esperado em ambas as rotas é HTTP 200. [Modo Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)

   ```powershell
   curl.exe -fsS https://api.futuranext.com.br/health/live
   curl.exe -fsS https://api.futuranext.com.br/health/ready
   ```

## 5. Publique o site no Cloudflare Pages

1. Faça o build em uma cópia do código para preservar o portal que já roda no Windows. O frontend recebe a URL do Supabase hospedado, sua chave **publishable** e o endereço da API.

   ```powershell
   Set-Location 'C:\Projetos\futuranext_site'
   $pasta = New-Item -ItemType Directory (Join-Path $env:TEMP ('futuranext-pages-' + [guid]::NewGuid().ToString('N')))
   $zip = Join-Path $pasta.FullName 'site.zip'
   $codigo = Join-Path $pasta.FullName 'codigo'
   git archive --format=zip -o $zip HEAD
   Expand-Archive -LiteralPath $zip -DestinationPath $codigo
   Set-Location $codigo
   $env:PUBLIC_SUPABASE_URL = 'https://REF.supabase.co'
   $env:PUBLIC_SUPABASE_PUBLISHABLE_KEY = 'SB_PUBLISHABLE_DO_PROJETO'
   $env:PUBLIC_API_BASE_URL = 'https://api.futuranext.com.br'
   npm.cmd ci
   npm.cmd run build
   (Resolve-Path -LiteralPath 'dist').Path
   ```

2. Na Cloudflare, abra **Workers & Pages → Create application → Get started → Drag and drop your files**. Escolha um nome para o projeto, por exemplo `futuranext`, e envie somente a pasta `dist` cujo caminho apareceu no PowerShell. Verifique que a página inicial abre no endereço `pages.dev` gerado. [Direct Upload no Pages](https://developers.cloudflare.com/pages/get-started/direct-upload/)
3. No projeto Pages, abra **Custom domains → Set up a custom domain**, informe `futuranext.com.br` e aguarde a ativação. A Cloudflare cria o registro do domínio principal na zona ativa. [Domínio próprio no Pages](https://developers.cloudflare.com/pages/configuration/custom-domains/)
4. Configure o redirecionamento de `www`. No painel Cloudflare, vá em **Bulk Redirects**, crie uma lista com `www.futuranext.com.br` como origem e `https://futuranext.com.br` como destino. Selecione **301** e as opções **Subpath matching**, **Preserve path suffix** e **Preserve query string**. Salve e crie uma regra ativa usando essa lista. Em **DNS → Records**, adicione **A `www` → `192.0.2.1`** com **Proxied**. Esse endereço permite à Cloudflare aplicar a regra de redirecionamento. [Redirecionamento de `www`](https://developers.cloudflare.com/pages/how-to/www-redirect/)

## 6. Teste pelo domínio público

1. Abra `https://futuranext.com.br` em uma janela privada. Clique em **Área do Cliente** e entre com o usuário criado no Supabase hospedado. Abra **Administração** e cadastre o autenticador TOTP para concluir a verificação em duas etapas.
2. No painel, cadastre uma empresa de teste e confira o acesso ao portal. Se configurou SMTP e os modelos, teste também um convite e a recuperação de senha com um endereço que você controla. Os links devem abrir no domínio principal.
3. Confira se `www` redireciona para a mesma página no domínio principal e se a API segue pronta. [Roteiro de validação e recuperação](DEPLOY_DIGITALOCEAN.md)

   ```powershell
   curl.exe -I https://www.futuranext.com.br/login
   curl.exe -fsS https://api.futuranext.com.br/health/ready
   ```
