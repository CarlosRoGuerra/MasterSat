from app.services import email_smtp


def test_email_com_boleto_e_nfse_tem_dois_anexos(monkeypatch):
    mensagens = []

    class Servidor:
        def send_message(self, mensagem):
            mensagens.append(mensagem)

        def quit(self):
            pass

    monkeypatch.setattr(email_smtp, '_abrir_conexao', lambda config: Servidor())
    email_smtp.enviar_email(
        None, 'cliente@example.com', 'Boleto e nota', 'Segue em anexo.',
        config={'host': 'smtp.example.com', 'from_email': 'financeiro@example.com', 'from_name': ''},
        anexos=[
            ('boleto.pdf', b'%PDF-boleto', 'application/pdf'),
            ('nfse.pdf', b'%PDF-nota', 'application/pdf'),
        ],
    )

    assert len(mensagens) == 1
    assert [parte.get_filename() for parte in mensagens[0].iter_attachments()] == ['boleto.pdf', 'nfse.pdf']
