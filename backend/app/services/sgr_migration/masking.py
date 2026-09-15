"""
Mascaramento de dados sensíveis para logs e para o relatório de diagnóstico
gravado em disco (backend/scripts/sgr_saida/*.json — sempre fora do git).

Regra geral (ver ETAPA 13 da POC de migração SGR):
  - segredos (senha, chave de API, tokens de autenticação) NUNCA aparecem,
    nem mascarados — são omitidos por completo;
  - dados pessoais (CPF/CNPJ, telefone, IMEI) aparecem só parcialmente,
    o suficiente para conferência humana sem expor o dado completo.
"""
from __future__ import annotations


def mask_document(value: str | None) -> str:
    """CPF/CNPJ: mantém só os 2 últimos dígitos."""
    if not value:
        return ''
    digits = ''.join(c for c in value if c.isdigit())
    if len(digits) <= 2:
        return '*' * len(digits)
    return '*' * (len(digits) - 2) + digits[-2:]


def mask_phone(value: str | None) -> str:
    """Telefone: mantém só os 4 últimos dígitos."""
    if not value:
        return ''
    digits = ''.join(c for c in value if c.isdigit())
    if len(digits) <= 4:
        return '*' * len(digits)
    return '*' * (len(digits) - 4) + digits[-4:]


def mask_imei(value: str | None) -> str:
    """IMEI/número de série: mantém só os 4 últimos dígitos."""
    if not value:
        return ''
    digits = ''.join(c for c in str(value) if c.isdigit())
    if len(digits) <= 4:
        return '*' * len(digits)
    return '*' * (len(digits) - 4) + digits[-4:]


def mask_email(value: str | None) -> str:
    """E-mail: mantém o primeiro caractere do usuário e o domínio inteiro."""
    if not value or '@' not in value:
        return '***' if value else ''
    user, _, domain = value.partition('@')
    if not user:
        return f'***@{domain}'
    return f'{user[0]}***@{domain}'
