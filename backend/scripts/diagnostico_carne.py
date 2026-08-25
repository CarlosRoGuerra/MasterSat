"""
Diagnóstico: testa se POST /gerar/carne/lote registra TODAS as parcelas na
Ailos, ou só a primeira — hipótese levantada em análise externa de 25/08/2026
sobre por que o carnê "só saía com 1 boleto".

O que este script faz:
  1. Cria (ou reaproveita) 1 cliente de teste + 3 Billing de teste marcados
     '[DIAGNOSTICO CARNE]'.
  2. Registra as 3 como carnê via app.services.ailos_boletos.gerar_carne_lote
     (a MESMA função usada pela tela real) — 1 único POST com as 3 no array
     "carnes".
  3. Consulta CADA parcela INDIVIDUALMENTE na Ailos (consultar_boleto), a
     cada 5s por até 1 minuto — sem passar pela lógica de "espera todas"
     (_consultar_carne_por_boleto): aqui queremos ver o estado bruto de cada
     uma, não o resultado já agregado.
  4. Ao final, mostra o payload exato enviado (a partir do AilosApiLog
     persistido) e um veredito: quantas das 3 a Ailos confirma terem sido
     REALMENTE registradas.

Pré-requisitos (mesmos do ailos_homologacao.py):
  1. .env com AILOS_ENV=sandbox e credenciais de homologação (já é o padrão
     deste ambiente — NÃO roda contra produção sem trocar AILOS_ENV).
  2. Cooperado autorizado: POST /api/v1/ailos/connect (papel ADMIN) -> abrir
     a login_url no navegador -> confirmar GET /api/v1/ailos/status ->
     "cooperado_status": "authorized".

Uso:
  cd backend
  python scripts/diagnostico_carne.py

Seguro para re-executar: faz UPSERT do cliente/parcelas de teste pelo CPF.
Ao final, imprime o SQL para limpar os registros criados.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from decimal import Decimal

# Garante que 'app' é encontrado quando executado de dentro de backend/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento')

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.ailos_api_log import AilosApiLog  # noqa: E402
from app.models.ailos_boleto import AilosBoleto  # noqa: E402
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import BillingStatus, ClientStatus  # noqa: E402
from app.services import ailos_boletos  # noqa: E402
from app.services.ailos_boletos import consultar_boleto  # noqa: E402

NOTES_MARKER = '[DIAGNOSTICO CARNE]'
NUM_PARCELAS = 3
TENTATIVAS = 12          # 12 x 5s = 1 min de acompanhamento bruto
INTERVALO_S = 5


def _cpf_teste_valido(base9: str) -> str:
    """Gera um CPF com dígitos verificadores REAIS a partir de uma base de 9
    dígitos — evita depender de um CPF de teste memorizado que pode colidir
    com os já usados por scripts/ailos_homologacao.py."""
    def _dv(digs: str, pesos: range) -> str:
        soma = sum(int(d) * p for d, p in zip(digs, pesos))
        resto = soma % 11
        return '0' if resto < 2 else str(11 - resto)
    d1 = _dv(base9, range(10, 1, -1))
    d2 = _dv(base9 + d1, range(11, 1, -1))
    return base9 + d1 + d2


def _upsert_cliente(db) -> Client:
    cpf = _cpf_teste_valido('987654321')
    cliente = db.query(Client).filter_by(cpf_cnpj=cpf).first()
    if cliente is None:
        cliente = Client(cpf_cnpj=cpf)
        db.add(cliente)
    cliente.name = 'DIAGNOSTICO CARNE - CLIENTE DE TESTE'
    cliente.type = 'pf'
    cliente.status = ClientStatus.ACTIVE
    cliente.email = 'diagnostico.carne@mastersat.com.br'
    cliente.phone = '(47) 99999-0000'
    cliente.zip_code = '89200-000'
    cliente.address_line = 'Rua do Diagnostico'
    cliente.address_number = '1'
    cliente.address_complement = None
    cliente.neighborhood = 'Centro'
    cliente.city = 'Joinville'
    cliente.state = 'SC'
    cliente.notes = NOTES_MARKER
    db.commit()
    db.refresh(cliente)
    return cliente


def _upsert_parcelas(db, cliente: Client) -> list[Billing]:
    existentes = (
        db.query(Billing)
        .filter_by(client_id=cliente.id, notes=NOTES_MARKER, billing_type='carne')
        .order_by(Billing.installment_number.asc())
        .all()
    )
    if len(existentes) == NUM_PARCELAS:
        for b in existentes:
            b.status = BillingStatus.PENDING
        db.commit()
        return existentes

    for b in existentes:
        db.delete(b)
    db.flush()

    hoje = date.today()
    parcelas = []
    for i in range(1, NUM_PARCELAS + 1):
        b = Billing(
            client_id=cliente.id,
            title=f'DIAGNOSTICO CARNE - parcela {i}/{NUM_PARCELAS}',
            billing_type='carne',
            installment_number=i,
            installment_total=NUM_PARCELAS,
            amount=Decimal('1.00'),
            due_date=hoje + timedelta(days=30 * i),
            status=BillingStatus.PENDING,
            period_label=(hoje + timedelta(days=30 * i)).strftime('%m/%Y'),
            notes=NOTES_MARKER,
        )
        db.add(b)
        parcelas.append(b)
    db.commit()
    for b in parcelas:
        db.refresh(b)
    return parcelas


def _log_mais_recente(db, endpoint_contains: str, billing_id: int | None = None):
    q = db.query(AilosApiLog).filter(AilosApiLog.endpoint.contains(endpoint_contains))
    if billing_id is not None:
        q = q.filter(AilosApiLog.billing_id == billing_id)
    return q.order_by(AilosApiLog.id.desc()).first()


def main() -> None:
    print('=== Diagnóstico: /gerar/carne/lote registra TODAS as parcelas? ===\n')
    print(f'Ambiente Ailos: AILOS_ENV={settings.ailos_env}  base={settings.ailos_gateway_base_url}')
    if settings.ailos_env != 'sandbox':
        print('\n[ABORTADO] AILOS_ENV não é "sandbox" — este script não roda fora de homologação.')
        print('           Ajuste AILOS_ENV=sandbox no .env antes de continuar.')
        return
    print()

    db = SessionLocal()
    try:
        cliente = _upsert_cliente(db)
        parcelas = _upsert_parcelas(db, cliente)
        print(f'Cliente de teste: #{cliente.id} ({cliente.name})')
        print(f'Parcelas de teste: {[b.id for b in parcelas]}\n')

        print('--- Passo 1: registrando o carnê (POST /gerar/carne/lote) ---')
        try:
            lote = ailos_boletos.gerar_carne_lote(
                db,
                [(i + 1, b) for i, b in enumerate(parcelas)],
                {cliente.id: cliente},
            )
        except Exception as exc:
            print(f'[ERRO ao registrar o carnê] {exc}')
            print('\nIsso por si só já é um dado: a chamada nem completou.')
            print('Confira a sessão do cooperado (GET /api/v1/ailos/status).')
            return

        print(f'Lote criado: id={lote.id} ticket={lote.ticket} status={lote.status}\n')

        log_registro = _log_mais_recente(db, 'gerar/carne/lote')
        if log_registro:
            carnes_enviados = (log_registro.request_payload or {}).get('carnes', [])
            print(f'Payload enviado à Ailos: {len(carnes_enviados)} parcela(s) no array "carnes":')
            for item in carnes_enviados:
                doc = (item.get('documento') or {}).get('numeroDocumento')
                parcela = item.get('numeroParcela')
                print(f'  - numeroDocumento={doc}  numeroParcela={parcela}')
            print(f'HTTP {log_registro.status_code} — success={log_registro.success}')
            if log_registro.error_message:
                print(f'  erro: {log_registro.error_message}')
        else:
            print('[atenção] Não achei o log da chamada de registro em AilosApiLog.')
        print()

        print(f'--- Passo 2: consultando cada parcela INDIVIDUALMENTE na Ailos ---')
        print(f'(a cada {INTERVALO_S}s, até {TENTATIVAS} vezes — ~{TENTATIVAS * INTERVALO_S}s no total)\n')

        estado_final = {b.id: False for b in parcelas}
        for tentativa in range(1, TENTATIVAS + 1):
            linha = [f'tentativa {tentativa}/{TENTATIVAS}:']
            for b in parcelas:
                try:
                    resp = consultar_boleto(db, str(b.id))
                except Exception as exc:  # noqa: BLE001 — diagnóstico: qualquer falha vira "não existe"
                    resp = None
                existe = isinstance(resp, dict) and bool(
                    (resp.get('boleto') or resp).get('documento')
                    or (resp.get('boleto') or resp).get('codigoBarras')
                )
                estado_final[b.id] = estado_final[b.id] or existe
                linha.append(f'#{b.id}={"OK" if existe else "não existe"}')
            print('  ' + '  '.join(linha))
            if all(estado_final.values()):
                print('\n  Todas confirmadas — parando antes do tempo máximo.')
                break
            if tentativa < TENTATIVAS:
                time.sleep(INTERVALO_S)

        print()
        confirmadas = sum(1 for v in estado_final.values() if v)
        print(f'=== RESULTADO: {confirmadas} de {NUM_PARCELAS} parcelas confirmadas na Ailos ===\n')

        if confirmadas == NUM_PARCELAS:
            print('VEREDITO: hipótese REFUTADA — a Ailos registrou todas as parcelas do')
            print('carnê. O /gerar/carne/lote funciona como esperado neste ambiente; o')
            print('bug anterior era mesmo só o fechamento prematuro do lote (já corrigido).')
        elif confirmadas <= 1:
            print('VEREDITO: hipótese CONFIRMADA — a Ailos só efetivou 1 (ou nenhuma) das')
            print(f'{NUM_PARCELAS} parcelas enviadas no /gerar/carne/lote, mesmo o payload')
            print('tendo saído com as 3. Vale migrar o registro do carnê para chamadas')
            print('individuais de gerar_boleto() (já comprovadamente confiável), em vez')
            print('de depender do endpoint de lote de carnê.')
        else:
            print(f'VEREDITO: PARCIAL — {confirmadas} de {NUM_PARCELAS} confirmadas. Não é')
            print('nem "só a primeira" nem "todas" — pode ser inconsistência do lote de')
            print('carnê na Ailos (confirma o problema, mas com padrão diferente do')
            print('esperado). Vale investigar mais antes de decidir a migração.')

        print(f'\nParcelas de teste (para limpar depois):')
        billing_ids = [b.id for b in parcelas]
        print(f"  DELETE FROM ailos_boletos WHERE billing_id IN ({', '.join(str(i) for i in billing_ids)});")
        print(f"  DELETE FROM billings WHERE id IN ({', '.join(str(i) for i in billing_ids)});")
        print(f"  DELETE FROM clients WHERE id = {cliente.id};")
    finally:
        db.close()


if __name__ == '__main__':
    main()
