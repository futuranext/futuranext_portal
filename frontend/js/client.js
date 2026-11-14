import {createClient} from '@supabase/supabase-js';
import {UserMessage} from './ui.js';

const config = __FUTURANEXT_PUBLIC_CONFIG__;

function authClient() {
  if (!config.supabaseUrl || !config.supabaseKey) return null;

  const key = `futuranext-auth:${new URL(config.supabaseUrl).host}`;
  // O SDK usa storageKey como canal de eventos. Um canal próprio por página
  // mantém eventos e tokens na aba enquanto a chave da sessão resiste à navegação.
  const storageKey = `${key}:${crypto.randomUUID()}`;
  const sessionKey = (name) => name.replace(storageKey, key);
  const storage = {
    getItem: (name) => sessionStorage.getItem(sessionKey(name)),
    setItem: (name, value) => sessionStorage.setItem(sessionKey(name), value),
    removeItem: (name) => sessionStorage.removeItem(sessionKey(name)),
  };

  return createClient(config.supabaseUrl, config.supabaseKey, {
    auth: {
      persistSession: true,
      storageKey,
      storage,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      flowType: 'pkce',
    },
  }).auth;
}

export const auth = authClient();

export async function session() {
  if (!auth) return null;
  const {data, error} = await auth.getSession();
  if (error) throw new UserMessage('Não foi possível recuperar sua sessão. Tente entrar novamente.');
  return data.session;
}

const apiErrors = new Map([
  [401, 'Sua sessão expirou. Entre novamente para continuar.'],
  [403, 'Seu perfil ou sua sessão atual não tem permissão para esta operação.'],
  [404, 'O registro não foi encontrado ou está fora do seu acesso.'],
  [409, 'Esta operação conflita com um registro existente. Atualize a página e confira os dados.'],
  [422, 'Confira os dados e os formatos informados.'],
  [429, 'Muitas solicitações em pouco tempo. Aguarde alguns instantes.'],
]);

export class ApiError extends UserMessage {
  constructor(status) {
    super(apiErrors.get(status) ?? 'O serviço está indisponível. Tente novamente em instantes.');
    this.status = status;
  }
}

export async function api(path, {method = 'GET', body, signal} = {}) {
  const current = await session();
  if (!current) throw new ApiError(401);
  const timeout = AbortSignal.timeout(15000);
  const combinedSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const headers = {Accept: 'application/json', Authorization: `Bearer ${current.access_token}`};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`${config.apiBase}/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: combinedSignal,
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new UserMessage('Não foi possível conectar ao serviço. Confira sua conexão e tente novamente.');
  }
  if (!response.ok) throw new ApiError(response.status);
  return response.status === 204 ? null : response.json();
}

export function resource(id) {
  return encodeURIComponent(id);
}
