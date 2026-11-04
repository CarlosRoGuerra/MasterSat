"""Dados únicos do beneficiário para API, remessa e impressão Ailos/Viacredi.

Parametrização comunicada pela Ailos para 01/11/2026. Códigos oficiais de
boletos anteriores são preservados; não se recalcula título registrado.
"""
from dataclasses import dataclass, replace

from app.core.config import settings


@dataclass(frozen=True)
class BeneficiarioAilos:
    convenio: str
    agencia: str
    agencia_dv: str
    conta: str
    conta_dv: str
    carteira: str
    variacao: str
    nome: str
    cnpj: str
    cooperativa_nome: str

    @property
    def conta_com_dv(self) -> str:
        return self.conta.zfill(7) + self.conta_dv


def beneficiario_atual() -> BeneficiarioAilos:
    def numero(valor, tamanho, campo):
        texto = str(valor).strip()
        if not texto.isascii() or not texto.isdigit() or len(texto) > tamanho:
            raise ValueError(f'{campo} Ailos inválido: esperado número de até {tamanho} dígitos.')
        return texto.zfill(tamanho)

    return BeneficiarioAilos(
        convenio=numero(settings.ailos_numero_convenio, 6, 'Convênio'),
        agencia=numero(settings.ailos_agencia, 4, 'Agência'),
        agencia_dv=numero(settings.ailos_agencia_dv, 1, 'DV da agência'),
        conta=numero(settings.ailos_conta_corrente, 7, 'Conta corrente'),
        conta_dv=numero(settings.ailos_conta_dv, 1, 'DV da conta'),
        carteira=numero(settings.ailos_default_carteira, 2, 'Carteira'),
        variacao=numero(settings.ailos_variacao_carteira, 3, 'Variação'),
        nome=settings.ailos_beneficiario_nome, cnpj=numero(settings.ailos_beneficiario_cnpj, 14, 'CNPJ'),
        cooperativa_nome=settings.ailos_cooperativa_nome,
    )


def beneficiario_do_codigo(codigo: str) -> BeneficiarioAilos | None:
    """Identidade histórica a partir do campo livre do título oficial, pp. 9–10 CNAB."""
    if len(codigo) != 44 or not codigo.isdigit() or not codigo.startswith('0859'):
        return None
    atual = beneficiario_atual()
    convenio, conta_dv = codigo[19:25], codigo[25:33]
    # Dados da configuração anterior, somente para reimpressão de títulos já emitidos.
    agencia, dv = ('0102', '3') if convenio == '102004' else (atual.agencia, atual.agencia_dv)
    return replace(atual, convenio=convenio, agencia=agencia, agencia_dv=dv,
                   conta=conta_dv[:-1], conta_dv=conta_dv[-1], carteira=codigo[42:44])
