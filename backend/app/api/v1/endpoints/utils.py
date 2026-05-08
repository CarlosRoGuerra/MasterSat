from fastapi import APIRouter, HTTPException
import requests

router = APIRouter()


@router.get('/cep/{cep}')
def lookup_cep(cep: str):
    normalized = ''.join(ch for ch in cep if ch.isdigit())
    if len(normalized) != 8:
        raise HTTPException(status_code=400, detail='CEP inválido.')

    try:
        response = requests.get(f'https://viacep.com.br/ws/{normalized}/json/', timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        raise HTTPException(status_code=502, detail='Não foi possível consultar o CEP no momento.')

    if data.get('erro'):
        raise HTTPException(status_code=404, detail='CEP não encontrado.')

    return {
        'zip_code': data.get('cep') or normalized,
        'address_line': data.get('logradouro') or '',
        'neighborhood': data.get('bairro') or '',
        'city': data.get('localidade') or '',
        'state': data.get('uf') or '',
        'address_complement': data.get('complemento') or '',
    }
