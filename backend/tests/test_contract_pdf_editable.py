"""PDF preenchível e assinatura configurável da contratada."""
from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfReader


def _signature_png() -> bytes:
    image = Image.new('RGB', (450, 173), 'white')
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def _reader(content: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(content))


def test_blank_contract_pdf_has_fillable_fields(http, plan):
    response = http.post('/api/v1/contracts/generate-pdf', json={'plan_id': plan.id})
    assert response.status_code == 200
    fields = _reader(response.content).get_fields()
    assert fields['cliente_nome']['/FT'] == '/Tx'
    assert fields['cliente_nome']['/V'] == ''
    assert fields['adesao_pagamento_2']['/FT'] == '/Btn'
    assert fields['placas']['/FT'] == '/Tx'
    assert fields['valor_mensal']['/V'] == 'R$ 99,90'


def test_signature_setting_applies_to_new_and_existing_contract_pdfs(http, plan, contrato):
    endpoint = '/api/v1/settings/contract-signature'
    assert http.get(endpoint).json() == {'configured': False}
    response = http.post(endpoint, files={'file': ('assinatura.png', _signature_png(), 'image/png')})
    assert response.status_code == 200
    assert response.json() == {'configured': True}
    image_response = http.get(f'{endpoint}/image')
    assert image_response.status_code == 200
    assert image_response.headers['content-type'] == 'image/png'

    for response in (
        http.post('/api/v1/contracts/generate-pdf', json={'plan_id': plan.id}),
        http.get(f'/api/v1/contracts/{contrato.id}/pdf'),
    ):
        assert response.status_code == 200
        reader = _reader(response.content)
        assert reader.get_fields()['cliente_nome']['/FT'] == '/Tx'
        assert '/XObject' in reader.pages[0]['/Resources']

    assert http.delete(endpoint).json() == {'configured': False}
    assert http.get(f'{endpoint}/image').status_code == 404


def test_signature_rejects_non_image(http):
    endpoint = '/api/v1/settings/contract-signature'
    assert http.post(endpoint, files={'file': ('x.txt', b'not an image', 'text/plain')}).status_code == 415
    assert http.post(endpoint, files={'file': ('x.png', b'not an image', 'image/png')}).status_code == 422


def test_only_admin_can_upload_signature(http_fin):
    endpoint = '/api/v1/settings/contract-signature'
    assert http_fin.post(endpoint, files={'file': ('x.png', _signature_png(), 'image/png')}).status_code == 403
