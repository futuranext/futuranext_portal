"""Execute na raiz: python -m examples.send_text. Configure as variáveis antes."""
import os
from integrations.python.whatsapp_client import WhatsAppClient


if __name__ == '__main__':
    # O número deve ter consentimento registrado no painel.
    with WhatsAppClient() as client:
        result = client.send_text(os.environ['WHATSAPP_TEST_PHONE'],
                                  'Sua cotação está disponível.',
                                  idempotency_key=os.environ['WHATSAPP_IDEMPOTENCY_KEY'])
        print('Pedido:', result['id'], '| Estado:', result['status'])
        print('Consulte get_message_status para acompanhar; queued não significa entregue.')
