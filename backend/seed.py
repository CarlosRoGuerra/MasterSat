"""
Script de seed para popular o banco com dados de exemplo.

Cria:
  - 3 planos (mensal, trimestral, anual)
  - 5 produtos de serviço (taxa instalação, desinstalação, manutenção, etc.)
  - 8 clientes (mix PF e PJ)
  - 12 veículos distribuídos entre os clientes
  - 8 rastreadores em estoque (sem vínculo com veículo)
  - 10 contratos ativos
  - ~30 cobranças (pagas, pendentes, vencidas)

Uso:
  cd backend
  python seed.py

Seguro para re-executar: pula registros que já existem pelo campo único.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from random import choice, randint, uniform

# Garante que 'app' é encontrado quando executado de dentro de backend/
sys.path.insert(0, os.path.dirname(__file__))

# Driver psycopg3 (psycopg[binary]) — ajuste usuário/senha/dbname se necessário
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/rastreamento")

from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402 — side-effect imports registram metadados
    billing, client, contract, plan, service_product, tracker, vehicle,
    user,
)
from app.models.billing import Billing  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.contract import Contract  # noqa: E402
from app.models.enums import BillingStatus, ClientStatus, TrackerStatus, VehicleStatus  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.service_product import ServiceProduct  # noqa: E402
from app.models.tracker import Tracker  # noqa: E402
from app.models.vehicle import Vehicle  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exists(db, model, **filters):
    return db.query(model).filter_by(**filters).first()


def _upsert_plan(db, name, price, interval, description=None) -> Plan:
    obj = _exists(db, Plan, name=name)
    if obj:
        return obj
    obj = Plan(
        name=name,
        price=Decimal(str(price)),
        billing_interval_months=interval,
        description=description,
        active=True,
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Plano: {name}")
    return obj


def _upsert_product(db, name, category, price, **kwargs) -> ServiceProduct:
    obj = _exists(db, ServiceProduct, name=name)
    if obj:
        return obj
    obj = ServiceProduct(
        name=name,
        category=category,
        default_price=Decimal(str(price)),
        active=True,
        **kwargs,
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Produto: {name}")
    return obj


def _upsert_client(db, name, cpf_cnpj, tipo, email, phone, city, state, billing_day) -> Client:
    obj = _exists(db, Client, cpf_cnpj=cpf_cnpj)
    if obj:
        return obj
    obj = Client(
        name=name,
        cpf_cnpj=cpf_cnpj,
        type=tipo,
        status=ClientStatus.ACTIVE,
        email=email,
        phone=phone,
        city=city,
        state=state,
        billing_day=billing_day,
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Cliente: {name}")
    return obj


def _upsert_vehicle(db, client_id, plate, brand, model_name, year, color, tipo) -> Vehicle:
    obj = _exists(db, Vehicle, plate=plate)
    if obj:
        return obj
    obj = Vehicle(
        client_id=client_id,
        plate=plate,
        brand=brand,
        model=model_name,
        year=year,
        color=color,
        type=tipo,
        status=VehicleStatus.ACTIVE,
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Veículo: {plate} ({brand} {model_name})")
    return obj


def _upsert_tracker(db, imei, brand, model_name, serial=None) -> Tracker:
    obj = _exists(db, Tracker, imei=imei)
    if obj:
        return obj
    obj = Tracker(
        imei=imei,
        brand=brand,
        model=model_name,
        serial_number=serial or imei[-8:],
        status=TrackerStatus.STOCK,
        # sem client_id nem vehicle_id — apenas em estoque
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Rastreador: IMEI {imei} ({brand} {model_name})")
    return obj


def _upsert_contract(db, client_id, plan_id, vehicle_id, start_date, billing_day) -> Contract:
    obj = db.query(Contract).filter_by(
        client_id=client_id, vehicle_id=vehicle_id, status="ativo"
    ).first()
    if obj:
        return obj
    obj = Contract(
        client_id=client_id,
        plan_id=plan_id,
        vehicle_id=vehicle_id,
        tracker_id=None,  # sem vínculo com rastreador
        start_date=start_date,
        status="ativo",
        billing_day=billing_day,
        payment_method="boleto",
        billing_modality="boleto",
    )
    db.add(obj)
    db.flush()
    print(f"  [+] Contrato: cliente #{client_id} / veículo #{vehicle_id}")
    return obj


def _add_billing(
    db, contract_id, client_id, vehicle_id, plan_name, plan_price,
    due_date, status, period_label, paid=False,
) -> None:
    exists = db.query(Billing).filter_by(
        contract_id=contract_id,
        period_label=period_label,
        billing_type="recorrente",
        is_deleted=False,
    ).first()
    if exists:
        return
    b = Billing(
        contract_id=contract_id,
        client_id=client_id,
        vehicle_id=vehicle_id,
        title=f"Plano {plan_name}",
        billing_type="recorrente",
        amount=Decimal(str(plan_price)),
        due_date=due_date,
        status=status,
        period_label=period_label,
        payment_date=due_date if paid else None,
        paid_amount=Decimal(str(plan_price)) if paid else None,
        payment_method="boleto",
    )
    db.add(b)


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------

PLANOS = [
    ("Básico",     89.90,  1, "Rastreamento básico mensal"),
    ("Padrão",    129.90,  1, "Rastreamento padrão com suporte"),
    ("Premium",   189.90,  1, "Rastreamento premium + cercas virtuais"),
    ("Trimestral",340.00,  3, "Plano trimestral com desconto"),
    ("Anual",    1099.00, 12, "Plano anual com maior desconto"),
]

PRODUTOS = [
    ("Taxa de Instalação",     "taxa",    150.00, dict(allow_installments=False, remove_after_payment=True,  auto_add_on_uninstall=False)),
    ("Taxa de Desinstalação",  "taxa",    100.00, dict(allow_installments=False, remove_after_payment=True,  auto_add_on_uninstall=True)),
    ("Manutenção Preventiva",  "servico",  80.00, dict(allow_installments=False, remove_after_payment=False, auto_add_on_uninstall=False)),
    ("Revisão de Firmware",    "servico",  60.00, dict(allow_installments=False, remove_after_payment=False, auto_add_on_uninstall=False)),
    ("Substituição de Chip",   "servico",  40.00, dict(allow_installments=False, remove_after_payment=True,  auto_add_on_uninstall=False)),
]

CLIENTES = [
    # (nome, cpf_cnpj, tipo, email, fone, cidade, estado, dia_cobrança)
    ("João da Silva",              "111.222.333-44", "pf", "joao@email.com",      "(11) 99001-1001", "São Paulo",       "SP", 5),
    ("Maria Fernanda Costa",       "222.333.444-55", "pf", "maria@email.com",     "(21) 98002-2002", "Rio de Janeiro",  "RJ", 10),
    ("Carlos Eduardo Rocha",       "333.444.555-66", "pf", "carlos@email.com",    "(31) 97003-3003", "Belo Horizonte",  "MG", 15),
    ("Ana Paula Souza",            "444.555.666-77", "pf", "ana@email.com",       "(41) 96004-4004", "Curitiba",        "PR", 20),
    ("TransLog Soluções Ltda",     "12.345.678/0001-90", "pj", "fin@translog.com.br",  "(11) 3001-4001", "São Paulo",  "SP", 5),
    ("Agro Rota Transportes ME",   "23.456.789/0001-01", "pj", "admin@agrorota.com",   "(65) 3002-5002", "Cuiabá",     "MT", 10),
    ("Constru Fast Engenharia SA", "34.567.890/0001-12", "pj", "pagamento@cfast.ind",  "(85) 3003-6003", "Fortaleza",  "CE", 15),
    ("Redes Norte Comunicações",   "45.678.901/0001-23", "pj", "financeiro@rdnorte.net","(92) 3004-7004","Manaus",     "AM", 25),
]

VEICULOS = [
    # (idx_cliente, placa, marca, modelo, ano, cor, tipo)
    (0, "ABC1D23", "Toyota",      "Corolla",      2022, "Prata",   "passeio"),
    (0, "DEF2E34", "Honda",       "Civic",        2021, "Preto",   "passeio"),
    (1, "GHI3F45", "Volkswagen",  "Gol",          2020, "Branco",  "passeio"),
    (2, "JKL4G56", "Ford",        "Ka",           2019, "Vermelho","passeio"),
    (2, "MNO5H67", "Fiat",        "Argo",         2023, "Azul",    "passeio"),
    (3, "PQR6I78", "Chevrolet",   "Onix",         2022, "Cinza",   "passeio"),
    (4, "STU7J89", "Mercedes-Benz","Sprinter",    2021, "Branco",  "utilitario"),
    (4, "VWX8K90", "Ford",        "Transit",      2020, "Branco",  "utilitario"),
    (4, "YZA9L01", "Volkswagen",  "Delivery",     2022, "Azul",    "caminhao"),
    (5, "BCD0M12", "Scania",      "R450",         2021, "Vermelho","caminhao"),
    (6, "EFG1N23", "Toyota",      "Hilux",        2023, "Prata",   "pickup"),
    (7, "HIJ2O34", "Mitsubishi",  "L200 Triton",  2022, "Preto",   "pickup"),
]

RASTREADORES = [
    # (imei, marca, modelo)
    ("351234567890001", "Teltonika", "FMB920"),
    ("351234567890002", "Teltonika", "FMB920"),
    ("351234567890003", "Teltonika", "FMB140"),
    ("351234567890004", "Teltonika", "FMB140"),
    ("351234567890005", "Suntech",   "ST600"),
    ("351234567890006", "Suntech",   "ST600"),
    ("351234567890007", "Queclink",  "GV310LAU"),
    ("351234567890008", "Queclink",  "GV310LAU"),
]

# Contratos: (idx_cliente, idx_veiculo, idx_plano, meses_atrás_início, dia_cobrança)
CONTRATOS_CFG = [
    (0, 0,  1, 14, 5),   # João → Corolla → Padrão
    (0, 1,  0, 8,  5),   # João → Civic → Básico
    (1, 2,  2, 6,  10),  # Maria → Gol → Premium
    (2, 3,  0, 18, 15),  # Carlos → Ka → Básico
    (2, 4,  1, 3,  15),  # Carlos → Argo → Padrão
    (3, 5,  2, 10, 20),  # Ana → Onix → Premium
    (4, 6,  3, 12, 5),   # TransLog → Sprinter → Trimestral
    (4, 7,  1, 9,  5),   # TransLog → Transit → Padrão
    (4, 8,  1, 5,  5),   # TransLog → Delivery → Padrão
    (5, 9,  3, 7,  10),  # AgroRota → Scania → Trimestral
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed():
    db = SessionLocal()
    today = date.today()

    try:
        print("\n=== PLANOS ===")
        planos = [_upsert_plan(db, n, p, i, d) for n, p, i, d in PLANOS]

        print("\n=== PRODUTOS DE SERVIÇO ===")
        for nome, cat, preco, kwargs in PRODUTOS:
            _upsert_product(db, nome, cat, preco, **kwargs)

        print("\n=== CLIENTES ===")
        clientes = [_upsert_client(db, *c) for c in CLIENTES]

        print("\n=== VEÍCULOS ===")
        veiculos = [
            _upsert_vehicle(db, clientes[ci].id, placa, marca, modelo, ano, cor, tipo)
            for ci, placa, marca, modelo, ano, cor, tipo in VEICULOS
        ]

        print("\n=== RASTREADORES (sem vínculo com veículo) ===")
        for imei, marca, modelo in RASTREADORES:
            _upsert_tracker(db, imei, marca, modelo)

        print("\n=== CONTRATOS ===")
        contratos = []
        for ci, vi, pi, meses_atras, bday in CONTRATOS_CFG:
            start = today - timedelta(days=meses_atras * 30)
            ct = _upsert_contract(
                db,
                clientes[ci].id,
                planos[pi].id,
                veiculos[vi].id,
                start,
                bday,
            )
            contratos.append((ct, planos[pi]))

        print("\n=== COBRANÇAS ===")
        for ct, plano in contratos:
            interval = plano.billing_interval_months
            price = float(plano.price)
            bday = ct.billing_day or 1

            # Gera apenas histórico (meses passados) — mês atual em diante fica
            # limpo para o wizard de fechamento gerar manualmente
            for ciclo in range(-6, 0):
                # Data de vencimento do ciclo
                base_month = today.month + ciclo * interval
                base_year = today.year + (base_month - 1) // 12
                base_month = (base_month - 1) % 12 + 1
                import calendar
                last_day = calendar.monthrange(base_year, base_month)[1]
                due_day = min(bday, last_day)
                due = date(base_year, base_month, due_day)

                # Só gera se o contrato já existia nessa data
                if due < ct.start_date:
                    continue

                # Label do período
                if interval == 1:
                    label = due.strftime("%m/%Y")
                elif interval == 3:
                    q = ((due.month - 1) // 3) + 1
                    label = f"{due.year} • T{q}"
                else:
                    label = str(due.year)

                # Define status com base na data
                if due < today - timedelta(days=5):
                    # Passado: 80% pago, 20% vencido
                    if randint(1, 10) <= 8:
                        status = BillingStatus.PAID
                        paid = True
                    else:
                        status = BillingStatus.OVERDUE
                        paid = False
                elif due <= today + timedelta(days=30):
                    status = BillingStatus.PENDING
                    paid = False
                else:
                    status = BillingStatus.PENDING
                    paid = False

                _add_billing(
                    db, ct.id, ct.client_id, ct.vehicle_id,
                    plano.name, price, due, status, label, paid=paid,
                )

        db.commit()
        print("\n✔ Seed concluído com sucesso!")

        # Resumo
        print(f"\n  Planos:         {db.query(Plan).count()}")
        print(f"  Produtos:       {db.query(ServiceProduct).count()}")
        print(f"  Clientes:       {db.query(Client).count()}")
        print(f"  Veículos:       {db.query(Vehicle).count()}")
        print(f"  Rastreadores:   {db.query(Tracker).count()}")
        print(f"  Contratos:      {db.query(Contract).count()}")
        print(f"  Cobranças:      {db.query(Billing).count()}")
        pagas = db.query(Billing).filter_by(status=BillingStatus.PAID).count()
        pendentes = db.query(Billing).filter_by(status=BillingStatus.PENDING).count()
        vencidas = db.query(Billing).filter_by(status=BillingStatus.OVERDUE).count()
        print(f"    → Pagas:      {pagas}")
        print(f"    → Pendentes:  {pendentes}")
        print(f"    → Vencidas:   {vencidas}")

    except Exception as exc:
        db.rollback()
        print(f"\n✘ Erro durante o seed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
