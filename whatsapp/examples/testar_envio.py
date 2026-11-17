"""Teste interativo local: python -m examples.testar_envio. Não salva credenciais."""
import argparse
import getpass
import re
import time
import warnings
from uuid import uuid4

import httpx

from integrations.python.whatsapp_client import WhatsAppClient


API_URL = 'http://localhost:8000'
STATUS = {
    'queued': 'Na fila', 'processing': 'Processando', 'retry_wait': 'Aguardando nova tentativa segura',
    'sent': 'Aceita pelo provedor (ainda sem confirmação de entrega)',
    'delivered': 'Entregue', 'read': 'Lida', 'failed': 'Falhou',
    'unknown': 'Resultado incerto; não reenvie automaticamente', 'cancelled': 'Cancelada',
}


def select_instance(api_key):
    with httpx.Client(base_url=API_URL, headers={'Authorization': 'Bearer ' + api_key}, timeout=30) as http:
        response = http.get('/api/v1/instances')
        response.raise_for_status()
        instances = response.json()['items']
    if not instances:
        raise ValueError('Nenhuma instância disponível para esta chave. Confira as instâncias permitidas no painel.')
    print('\nInstâncias permitidas:')
    for index, instance in enumerate(instances, 1):
        print(f"  {index}. {instance['name']} — {instance['connection_state']} — ID: {instance['id']}")
    choice = input('Número da instância [1]: ').strip() or '1'
    if not choice.isdigit() or not 1 <= int(choice) <= len(instances):
        raise ValueError('Seleção de instância inválida.')
    return instances[int(choice) - 1]['id']


def track(client, message_id, seconds=60):
    print('ID da mensagem:', message_id)
    print('Consultando por até 60 segundos. Ctrl+C encerra a consulta, sem cancelar o envio.')
    deadline = time.monotonic() + seconds
    previous = None
    while True:
        result = client.get_message_status(message_id)
        state = result['status']
        if state != previous:
            print('Estado:', STATUS.get(state, state))
            previous = state
        if state in {'delivered', 'read', 'failed', 'unknown', 'cancelled'}:
            return 0 if state in {'delivered', 'read'} else 1
        if time.monotonic() >= deadline:
            print('Consulta encerrada; não há confirmação de entrega. Acompanhe em Mensagens no painel.')
            print(f'Para consultar novamente: python -m examples.testar_envio --message-id {message_id}')
            return 0
        time.sleep(min(3, max(0, deadline - time.monotonic())))


def run(message_id=None):
    print('Teste de mensagem pela API local —', API_URL)
    print('Use a API Key completa, não o prefixo abreviado exibido na lista do painel.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        api_key = getpass.getpass('API Key (oculta ao digitar): ').strip()
    if not api_key or any(char.isspace() for char in api_key):
        raise ValueError('Informe a API Key completa, sem espaços.')
    instance_id = select_instance(api_key)
    with WhatsAppClient(API_URL, api_key, instance_id) as client:
        if message_id:
            return track(client, message_id)
        if client.get_status()['connection_state'] != 'connected':
            raise ValueError('Conecte a instância pelo QR Code no painel antes de enviar.')
        phone = input('Destino com país e DDD, somente dígitos (ex.: 5511999999999): ').strip()
        if not re.fullmatch(r'[1-9][0-9]{7,14}', phone):
            raise ValueError('Telefone inválido. Use país + DDD + número, sem +, espaços ou pontuação.')
        message = input('Mensagem [Olá! Esta é uma mensagem de teste da minha API.]: ').strip()
        message = message or 'Olá! Esta é uma mensagem de teste da minha API.'
        if len(message) > 4096:
            raise ValueError('O texto deve ter no máximo 4096 caracteres.')
        key = input('Chave do pedido anterior, apenas se estiver repetindo o mesmo pedido [Enter = novo]: ').strip()
        key = key or 'teste-' + str(uuid4())
        if not re.fullmatch(r'[\x21-\x7e]{1,128}', key):
            raise ValueError('Chave do pedido inválida: use até 128 caracteres ASCII sem espaços.')
        print('\nDestino:', phone)
        print('Texto:', message)
        print('Chave do pedido:', key)
        print('Guarde essa chave: ao repetir o mesmo pedido, use a mesma instância, destino, texto e chave.')
        print('O consentimento do destinatário deve estar registrado no painel.')
        if input('Enviar esta mensagem agora? Digite ENVIAR: ').strip() != 'ENVIAR':
            print('Cancelado. Nenhuma mensagem solicitada.')
            return 0
        print('Solicitando envio. Se a conexão falhar, consulte o painel antes de tentar novamente.')
        result = client.send_text(phone, message, idempotency_key=key)
        print('Solicitação aceita:', STATUS.get(result['status'], result['status']))
        return track(client, result['id'])


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message-id', help='Apenas consulta uma mensagem existente; não envia nada.')
    args = parser.parse_args()
    try:
        return run(args.message_id)
    except (KeyboardInterrupt, EOFError):
        print('\nInterrompido. Se o envio já foi solicitado, ele pode continuar. Consulte o painel.')
    except getpass.GetPassWarning:
        print('Este terminal não permite ocultar a chave. Execute em uma janela normal do PowerShell.')
    except ValueError as exc:
        print('Verifique:', exc)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            code = exc.response.json().get('error', {}).get('code', '')
        except ValueError:
            code = ''
        hints = {
            'CONSENT_REQUIRED': 'Registre o consentimento em Consentimentos. Um opt-out impede o envio.',
            'NOT_CONNECTED': 'Conecte a instância pelo QR Code e aguarde Conectada.',
            'IDEMPOTENCY_CONFLICT': 'Essa chave de pedido já foi usada com outro conteúdo. Confira o pedido anterior.',
        }
        fallback = {
            401: 'API Key inválida, expirada ou revogada. Use o segredo completo.',
            403: 'Confira as permissões Enviar mensagens, Consultar mensagens e Consultar conexão da chave.',
            404: 'Recurso não encontrado ou não permitido para esta API Key.',
            429: 'Limite atingido. Aguarde antes de consultar ou repetir o mesmo pedido.',
        }
        print(f'API retornou HTTP {status}.', hints.get(code, fallback.get(status, 'Confira os serviços e o painel.')))
        print('Não há reenvio automático. Para repetir um pedido, preserve sua chave e conteúdo.')
    except httpx.RequestError:
        print('Não foi possível concluir a comunicação com a API local. Confira o Docker e o painel.')
        print('Se já solicitou envio, o resultado pode ser incerto. Preserve a chave do pedido; não crie outro às cegas.')
    return 1


if __name__ == '__main__':
    raise SystemExit(cli())
