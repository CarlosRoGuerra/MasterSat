"""Libera para reenvio os e-mails de fechamento recusados pela quota SMTP.

Antes da fila com intervalo, a recusa "450 4.7.1 ... Quota exceeded" da
DreamHost era gravada como resultado incerto ("desconhecido"), e o título
ficava preso em "Conferir envio" — nem a fila nem o botão reenviam esse estado.

Só é liberada a recusa em que NENHUMA mensagem foi aceita:
- ``SMTPRecipientsRefused``: todos os destinatários recusados (o erro gravado é
  o dicionário ``{'email': (450, ...)}``);
- ``SMTPDataError``: o servidor recusou o DATA (erro gravado como ``(450, ...)``).
A aceitação parcial ("Envio parcial; destinatários recusados") continua
"desconhecido": parte dos destinatários recebeu, e reenviar duplicaria.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f3c8a1d6b204'
down_revision = 'e7b5f0a32981'
branch_labels = None
depends_on = None

MENSAGEM = (
    'O provedor de e-mail recusou o envio por limite de quota; nenhuma mensagem foi aceita. '
    'Pode enviar novamente — a fila agora respeita o intervalo automaticamente.'
)


def upgrade():
    op.get_bind().execute(
        sa.text(
            """
            UPDATE closure_email_deliveries
               SET status = 'erro', error = :mensagem
             WHERE status = 'desconhecido'
               AND lower(error) LIKE '%quota exceeded%'
               AND lower(error) NOT LIKE '%envio parcial%'
               AND (error LIKE 'Resultado do envio incerto: {%'
                    OR error LIKE 'Resultado do envio incerto: (4%')
            """
        ),
        {'mensagem': MENSAGEM},
    )


def downgrade():
    # Correção de dados: o estado "desconhecido" anterior estava errado (nada
    # foi aceito pelo servidor), então não há o que restaurar.
    pass
