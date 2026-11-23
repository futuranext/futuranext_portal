import {cp, lstat, mkdir, readFile, realpath, rm, writeFile} from 'node:fs/promises';
import {dirname, join, resolve} from 'node:path';
import {loadEnvFile} from 'node:process';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const root = await realpath(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
const source = join(root, 'frontend');
const output = join(root, 'dist');

try {
  loadEnvFile(join(root, '.env'));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

function publicOrigin(name) {
  const value = process.env[name]?.trim();
  if (!value) return '';
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} precisa ser uma origem HTTP(S) válida.`);
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  const validProtocol = url.protocol === 'https:' || (url.protocol === 'http:' && loopback);
  if (!validProtocol || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error(`${name} precisa conter apenas a origem. HTTP é permitido somente em loopback.`);
  }
  return url.origin;
}

function isPublicKey(value) {
  if (/^sb_publishable_[a-zA-Z0-9_-]+$/.test(value)) return true;
  if (!/^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$/.test(value)) return false;
  try {
    return JSON.parse(Buffer.from(value.split('.')[1], 'base64url').toString('utf8')).role === 'anon';
  } catch {
    return false;
  }
}

const supabaseUrl = publicOrigin('PUBLIC_SUPABASE_URL');
const supabaseKey = process.env.PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ?? '';
const apiBase = publicOrigin('PUBLIC_API_BASE_URL');
if (supabaseUrl && !supabaseKey || supabaseKey && !supabaseUrl) {
  throw new Error('Configure PUBLIC_SUPABASE_URL e PUBLIC_SUPABASE_PUBLISHABLE_KEY juntas.');
}
if (supabaseKey && !isPublicKey(supabaseKey)) {
  throw new Error('PUBLIC_SUPABASE_PUBLISHABLE_KEY precisa ser uma chave publishable ou anon.');
}

const bundleDir = join(output, 'platform');
const bundle = await build({
  absWorkingDir: root,
  entryPoints: ['auth.js', 'portal.js', 'admin.js'].map((name) => join(source, 'js', name)),
  outdir: bundleDir,
  define: {__FUTURANEXT_PUBLIC_CONFIG__: JSON.stringify({supabaseUrl, supabaseKey, apiBase})},
  bundle: true,
  splitting: true,
  format: 'esm',
  platform: 'browser',
  target: ['es2022'],
  minify: true,
  legalComments: 'eof',
  write: false,
});

try {
  const metadata = await lstat(output);
  if (!metadata.isDirectory() || metadata.isSymbolicLink() || await realpath(output) !== output) {
    throw new Error('dist precisa ser um diretório regular na raiz do projeto.');
  }
  await rm(output, {recursive: true});
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

await mkdir(bundleDir, {recursive: true});
await Promise.all([
  ...['index.html', 'styles.css', 'script.js'].map((file) => cp(join(root, file), join(output, file))),
  // O chat do site chama a API pública; o endereço vem do mesmo PUBLIC_API_BASE_URL do portal.
  readFile(join(root, 'config.js'), 'utf8').then((text) => writeFile(join(output, 'config.js'),
    `${text}
window.FUTURANEXT_CONFIG.apiBase = ${JSON.stringify(apiBase)};
`)),
  ...bundle.outputFiles.map((file) => writeFile(file.path, file.contents)),
  mkdir(join(output, 'assets')).then(() => Promise.all([
    cp(join(root, 'assets', 'logo-mark.svg'), join(output, 'assets', 'logo-mark.svg')),
    cp(join(root, 'assets', 'favicon.svg'), join(output, 'assets', 'favicon.svg')),
  ])),
  cp(join(source, 'platform.css'), join(bundleDir, 'platform.css')),
  cp(join(source, '404.html'), join(output, '404.html')),
]);

const authRoutes = [
  ['login', 'Entrar na FuturaNext', 'login'],
  ['recuperar-senha', 'Recuperar senha', 'recovery'],
  ['ativar-conta', 'Ativar conta', 'invite'],
  ['cadastro', 'Teste grátis por 15 dias', 'signup'],
];
const portalRoutes = [
  ['portal', 'Visão geral', 'overview'],
  ['portal/produtos', 'Ferramentas', 'products'],
  ['portal/perfil', 'Meu perfil', 'profile'],
  ['portal/usuarios', 'Usuários da empresa', 'members'],
  ['portal/pagamento', 'Pagamento', 'billing'],
  // Endereço antigo mantido para links já enviados.
  ['portal/cobrancas', 'Pagamento', 'billing'],
];
const adminRoutes = [
  ['admin', 'Visão geral', 'overview'],
  ['admin/clientes', 'Empresas', 'clients'],
  ['admin/usuarios', 'Usuários', 'members'],
  ['admin/produtos', 'Catálogo de produtos', 'products'],
  ['admin/assinaturas', 'Licenças', 'licenses'],
  ['admin/acessos', 'Acessos por usuário', 'access'],
  ['admin/solicitacoes', 'Solicitações de ferramentas', 'requests'],
  ['admin/cobrancas', 'Cobranças por empresa', 'billing'],
];

async function generatePages(templateFile, routes, common = {}) {
  const template = await readFile(join(source, templateFile), 'utf8');
  for (const [route, title, view] of routes) {
    const target = join(output, `${route}.html`);
    const fields = {...common, title, view};
    await mkdir(dirname(target), {recursive: true});
    await writeFile(target, template.replace(/{{(\w+)}}/g, (_, name) => fields[name]));
  }
}

const connectSources = [...new Set(["'self'", supabaseUrl, apiBase].filter(Boolean))];
const csp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data:",
  `connect-src ${connectSources.join(' ')}`,
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
];
const headers = `/*\n  Content-Security-Policy: ${csp.join('; ')}\n  Referrer-Policy: no-referrer\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n`;

await Promise.all([
  generatePages('auth.html', authRoutes),
  generatePages('workspace.html', portalRoutes, {entry: 'portal', area: 'Portal do cliente'}),
  generatePages('workspace.html', adminRoutes, {entry: 'admin', area: 'Administração'}),
  writeFile(join(output, '_headers'), headers),
]);

console.log('Frontend pronto em dist/.');
