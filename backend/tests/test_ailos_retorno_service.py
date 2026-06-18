"""
Testes para app.services.ailos_retorno: solicitar_arquivo_retorno,
listar_arquivos_retorno, baixar_arquivo_retorno.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.models.ailos_retorno_arquivo import AilosRetornoArquivo
from app.services import ailos_client
from app.services.ailos_retorno import (
    baixar_arquivo_retorno,
    listar_arquivos_retorno,
    solicitar_arquivo_retorno,
)


@pytest.fixture(autouse=True)
def _ailos_settings(monkeypatch):
    monkeypatch.setattr(settings, 'ailos_gateway_base_url', 'https://gateway.ailos.test')
    monkeypatch.setattr(settings, 'ailos_timeout_seconds', 5)
    monkeypatch.setattr(settings, 'ailos_developer_key', 'dev-key')
    monkeypatch.setattr(settings, 'ailos_numero_convenio', '102004')


@pytest.fixture(autouse=True)
def _tokens(monkeypatch):
    monkeypatch.setattr(ailos_client, 'get_valid_client_token', lambda db: 'client-token')
    monkeypatch.setattr(ailos_client, 'get_valid_cooperado_token', lambda db: 'coop-token')


def _resp(status_code=200, json_data=None, text='', headers=None, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers if headers is not None else {'Content-Type': 'application/json'}
    resp.text = text
    resp.content = content if content is not None else text.encode()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError('no json')
    return resp


class TestSolicitarArquivoRetorno:
    def test_cria_registro_requested(self, db):
        resp = _resp(202, json_data={'ticket': 'TICKET-RETORNO-1'})

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            row = solicitar_arquivo_retorno(db, date(2026, 6, 10))

        assert row.id is not None
        assert row.numero_convenio == '102004'
        assert row.data_movimento == date(2026, 6, 10)
        assert row.ticket == 'TICKET-RETORNO-1'
        assert row.status == 'requested'

        args, _ = mock_requests.request.call_args
        assert args[1] == 'https://gateway.ailos.test/v1/boletos/solicitar/arquivo/retorno/convenios/102004/2026-06-10'

        persisted = db.query(AilosRetornoArquivo).filter_by(id=row.id).first()
        assert persisted is not None
        assert persisted.status == 'requested'


class TestListarArquivosRetorno:
    def test_retorna_resposta_da_api(self, db):
        body = {'arquivos': [{'ticket': 'TICKET-1'}]}
        resp = _resp(200, json_data=body)

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            result = listar_arquivos_retorno(db, date(2026, 6, 1), date(2026, 6, 10))

        assert result == body

        args, _ = mock_requests.request.call_args
        assert args[1] == (
            'https://gateway.ailos.test/v1/boletos/listar/arquivo/retorno/convenios/102004/2026-06-01/2026-06-10'
        )


class TestBaixarArquivoRetorno:
    def _criar_row(self, db, ticket='TICKET-BAIXAR-1', status='requested'):
        row = AilosRetornoArquivo(
            numero_convenio='102004',
            data_movimento=date(2026, 6, 10),
            ticket=ticket,
            status=status,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def test_processing_mantem_status_processing(self, db):
        row = self._criar_row(db)

        body = {'mensagem': '[99] Solicitação em processamento'}
        resp = _resp(400, json_data=body, text=str(body))

        with patch('app.services.ailos_client.requests') as mock_requests:
            mock_requests.request.return_value = resp
            row = baixar_arquivo_retorno(db, row)

        assert row.status == 'processing'
        assert row.storage_object_key is None
        assert row.downloaded_at is None

    def test_sucesso_armazena_zip_e_atualiza_status(self, db):
        row = self._criar_row(db)

        zip_content = b'PK\x03\x04-conteudo-zip-fake'
        resp = _resp(200, headers={'Content-Type': 'application/zip'}, content=zip_content)

        with patch('app.services.ailos_client.requests') as mock_requests, \
                patch('app.services.ailos_retorno.upload_bytes') as mock_upload:
            mock_requests.request.return_value = resp
            mock_upload.return_value = f'ailos/retorno/{row.ticket}.zip'
            row = baixar_arquivo_retorno(db, row)

        mock_upload.assert_called_once_with(f'ailos/retorno/{row.ticket}.zip', zip_content, 'application/zip')
        assert row.status == 'downloaded'
        assert row.storage_object_key == f'ailos/retorno/{row.ticket}.zip'
        assert row.downloaded_at is not None

    def test_sem_ticket_levanta_ailos_error(self, db):
        row = self._criar_row(db, ticket=None)

        with pytest.raises(ailos_client.AilosError):
            baixar_arquivo_retorno(db, row)
