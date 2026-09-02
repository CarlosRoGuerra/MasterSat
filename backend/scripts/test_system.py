"""
Sistema de testes completo — MasterSat
Testa o fluxo completo: auth → cliente → veículo → rastreador → vínculo → plano → contrato → OS → financeiro

Pré-requisitos:
    pip install requests
    Backend rodando em http://localhost:8000

Executar dentro de backend/ (lê o e-mail do admin de backend/.env; a senha
precisa ser passada via MASTERSAT_TEST_ADMIN_PASSWORD ou INITIAL_ADMIN_PASSWORD
em backend/.env — nunca hardcode a senha aqui, este arquivo é versionado):
    MASTERSAT_TEST_ADMIN_PASSWORD=... python scripts/test_system.py
"""

import os
import random
import string
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    import requests
except ImportError:
    print("Dependência ausente. Execute: pip install requests")
    sys.exit(1)

from app.core.config import settings as app_settings  # noqa: E402

BASE_URL = os.environ.get("MASTERSAT_TEST_BASE_URL", "http://localhost:8000/api/v1")
ADMIN_EMAIL = app_settings.initial_admin_email
ADMIN_PASSWORD = os.environ.get("MASTERSAT_TEST_ADMIN_PASSWORD") or app_settings.initial_admin_password

if not ADMIN_PASSWORD:
    print("❌  Defina MASTERSAT_TEST_ADMIN_PASSWORD (ou INITIAL_ADMIN_PASSWORD em backend/.env) com a senha do admin local.")
    sys.exit(1)

# ─── Helpers visuais ──────────────────────────────────────────────────────────
G, R, Y, C, RESET, BOLD = "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[0m", "\033[1m"

def ok(msg):   print(f"  {G}✔  {msg}{RESET}")
def err(msg):  print(f"  {R}✘  {msg}{RESET}"); FAILURES.append(msg)
def warn(msg): print(f"  {Y}⚠  {msg}{RESET}")
def info(msg): print(f"  {C}ℹ  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")

FAILURES: list[str] = []

def rand_str(n=6):
    return ''.join(random.choices(string.ascii_uppercase, k=n))

def rand_digits(n):
    return ''.join(random.choices(string.digits, k=n))

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

def api(method, path, **kwargs):
    resp = session.request(method, f"{BASE_URL}{path}", **kwargs)
    return resp

# Armazena IDs criados para uso entre etapas
state: dict = {}

# ─── 0 · Autenticação ─────────────────────────────────────────────────────────
header("0 · Autenticação")

resp = api("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
if resp.status_code == 200:
    token = resp.json().get("access_token")
    session.headers["Authorization"] = f"Bearer {token}"
    ok(f"Login admin OK — token obtido")
else:
    err(f"Login falhou [{resp.status_code}]: {resp.text}")
    sys.exit(1)

# ─── 1 · Criar Plano ──────────────────────────────────────────────────────────
header("1 · Criar plano de serviço")

plan_name = f"Plano Teste {rand_str()}"
resp = api("POST", "/plans", json={"name": plan_name, "price": 89.90, "description": "Plano criado pelo teste automatizado"})
if resp.status_code in (200, 201):
    state["plan"] = resp.json()
    ok(f"Plano criado — ID {state['plan']['id']} · {plan_name} · R$ 89,90/mês")
else:
    err(f"Erro ao criar plano [{resp.status_code}]: {resp.text}")

# ─── 2 · Criar Cliente ────────────────────────────────────────────────────────
header("2 · Criar cliente")

cpf = rand_digits(11)
client_name = f"Cliente Teste {rand_str()}"
resp = api("POST", "/clients", json={
    "name": client_name,
    "cpf_cnpj": cpf,
    "type": "pf",
    "status": "ativo",
    "email": f"teste.{rand_str(4).lower()}@mastersat.local",
    "phone": f"11{rand_digits(9)}",
})
if resp.status_code in (200, 201):
    state["client"] = resp.json()
    ok(f"Cliente criado — ID {state['client']['id']} · {client_name}")
else:
    err(f"Erro ao criar cliente [{resp.status_code}]: {resp.text}")

# Buscar cliente pelo nome
if "client" in state:
    resp = api("GET", f"/clients?search={client_name[:10]}")
    if resp.status_code == 200 and any(c["id"] == state["client"]["id"] for c in resp.json()):
        ok("Busca de cliente por nome retornou o registro criado")
    else:
        err(f"Busca de cliente falhou [{resp.status_code}]")

# ─── 3 · Criar Veículo ────────────────────────────────────────────────────────
header("3 · Criar veículo e vincular ao cliente")

plate = f"TST{rand_digits(4)}"
chassis = f"9BW{rand_digits(14)}"
resp = api("POST", "/vehicles", json={
    "client_id": state.get("client", {}).get("id"),
    "plate": plate,
    "chassis": chassis,
    "brand": "TESTE",
    "model": "MODELO TESTE",
    "year": 2024,
    "type": "carro",
    "color": "PRETO",
    "status": "ativo",
})
if resp.status_code in (200, 201):
    state["vehicle"] = resp.json()
    ok(f"Veículo criado — ID {state['vehicle']['id']} · placa {plate}")
else:
    err(f"Erro ao criar veículo [{resp.status_code}]: {resp.text}")

# Buscar veículo pela placa
if "vehicle" in state:
    resp = api("GET", f"/vehicles?search={plate}")
    if resp.status_code == 200 and any(v["id"] == state["vehicle"]["id"] for v in resp.json()):
        ok("Busca de veículo por placa retornou o registro")
    else:
        err(f"Busca de veículo falhou [{resp.status_code}]")

# ─── 4 · Criar Rastreador ─────────────────────────────────────────────────────
header("4 · Criar rastreador (em estoque)")

imei = rand_digits(15)
resp = api("POST", "/trackers", json={
    "imei": imei,
    "brand": "Suntech",
    "model": "ST300 Series",
    "status": "em_estoque",
    "external_manufacturer_id": 69,   # Suntech ST300 Series
    "external_manufacturer_label": "Suntech ST300 Series",
})
if resp.status_code in (200, 201):
    state["tracker"] = resp.json()
    ok(f"Rastreador criado — ID {state['tracker']['id']} · IMEI {imei}")
else:
    err(f"Erro ao criar rastreador [{resp.status_code}]: {resp.text}")

# Buscar rastreador pelo IMEI
if "tracker" in state:
    resp = api("GET", f"/trackers?search={imei[:8]}")
    if resp.status_code == 200 and any(t["id"] == state["tracker"]["id"] for t in resp.json()):
        ok("Busca de rastreador por IMEI retornou o registro")
    else:
        err(f"Busca de rastreador falhou [{resp.status_code}]")

# ─── 5 · Vincular rastreador ao veículo + criar contrato ──────────────────────
header("5 · Vincular rastreador ao veículo com plano contratado")

if all(k in state for k in ("tracker", "vehicle", "plan")):
    start = date.today().isoformat()
    resp = api("POST", f"/trackers/{state['tracker']['id']}/link-vehicle", json={
        "vehicle_id": state["vehicle"]["id"],
        "plan_id": state["plan"]["id"],
        "start_date": start,
        "billing_day": 10,
        "payment_method": "pix",
        "auto_generate_billings": True,
        "billing_cycles": 3,
    })
    if resp.status_code == 200:
        data = resp.json()
        ok(f"Rastreador vinculado ao veículo {plate}")
        if data.get("contract"):
            state["contract"] = data["contract"]
            ok(f"Contrato criado — ID {state['contract']['id']} · plano {state['plan']['name']}")
        else:
            warn("Vínculo criado mas sem contrato retornado")
    else:
        err(f"Erro ao vincular rastreador [{resp.status_code}]: {resp.text}")
else:
    warn("Pulando vínculo — tracker/veículo/plano não criados")

# ─── 6 · Verificar contrato + cobranças ───────────────────────────────────────
header("6 · Verificar contrato e cobranças geradas")

if "contract" in state:
    resp = api("GET", f"/contracts/{state['contract']['id']}")
    if resp.status_code == 200:
        c = resp.json()
        ok(f"Contrato consultado — status: {c.get('status')} · abertos: {c.get('open_billings')} cobranças")
    else:
        err(f"Erro ao consultar contrato [{resp.status_code}]")

    resp = api("GET", f"/billings?contract_id={state['contract']['id']}")
    if resp.status_code == 200:
        bills = resp.json()
        ok(f"{len(bills)} cobrança(s) gerada(s) automaticamente")
        if bills:
            state["billing"] = bills[0]
            info(f"Primeira cobrança — venc: {bills[0].get('due_date')} · R$ {bills[0].get('amount')}")
    else:
        err(f"Erro ao listar cobranças [{resp.status_code}]: {resp.text}")

# Buscar contrato por nome do cliente e por placa
if "client" in state:
    resp = api("GET", f"/contracts?search={client_name[:8]}")
    if resp.status_code == 200:
        ok(f"Busca de contrato por nome do cliente OK — {len(resp.json())} resultado(s)")
    else:
        err(f"Busca de contrato por nome falhou [{resp.status_code}]")

if "vehicle" in state:
    resp = api("GET", f"/contracts?search={plate[:5]}")
    if resp.status_code == 200:
        ok(f"Busca de contrato por placa OK — {len(resp.json())} resultado(s)")
    else:
        err(f"Busca de contrato por placa falhou [{resp.status_code}]")

# ─── 7 · Registrar pagamento ──────────────────────────────────────────────────
header("7 · Registrar pagamento de uma cobrança")

if "billing" in state:
    resp = api("POST", f"/billings/{state['billing']['id']}/pay", json={
        "payment_date": date.today().isoformat(),
        "payment_method": "pix",
        "paid_amount": state["billing"].get("amount"),
    })
    if resp.status_code == 200:
        paid = resp.json()
        ok(f"Pagamento registrado — status: {paid.get('status')} · recibo: {paid.get('receipt_number') or 'gerado'}")
        state["paid_billing"] = paid
    else:
        err(f"Erro ao registrar pagamento [{resp.status_code}]: {resp.text}")

# ─── 8 · Criar Ordem de Serviço ───────────────────────────────────────────────
header("8 · Criar e atualizar ordem de serviço")

if all(k in state for k in ("client", "vehicle", "tracker")):
    resp = api("POST", "/service-orders", json={
        "client_id": state["client"]["id"],
        "vehicle_id": state["vehicle"]["id"],
        "tracker_id": state["tracker"]["id"],
        "type": "instalacao",
        "status": "aberta",
        "scheduled_at": (date.today() + timedelta(days=1)).isoformat() + "T09:00:00",
    })
    if resp.status_code in (200, 201):
        state["os"] = resp.json()
        ok(f"OS criada — #{state['os']['number']} · tipo: instalação")
    else:
        err(f"Erro ao criar OS [{resp.status_code}]: {resp.text}")

    if "os" in state:
        resp = api("PUT", f"/service-orders/{state['os']['id']}", json={"status": "em_andamento"})
        if resp.status_code == 200:
            ok(f"OS movida para 'em_andamento'")
        else:
            err(f"Erro ao atualizar OS [{resp.status_code}]")

        resp = api("PUT", f"/service-orders/{state['os']['id']}", json={"status": "concluida"})
        if resp.status_code == 200:
            ok(f"OS concluída com sucesso")
        else:
            err(f"Erro ao concluir OS [{resp.status_code}]")

        # Consultar histórico da OS
        resp = api("GET", f"/service-orders/{state['os']['id']}/logs")
        if resp.status_code == 200:
            logs = resp.json()
            ok(f"Histórico da OS — {len(logs)} evento(s) registrado(s)")
        else:
            err(f"Erro ao consultar histórico da OS [{resp.status_code}]")
else:
    warn("Pulando OS — cliente/veículo/rastreador não disponíveis")

# ─── 9 · Histórico do rastreador ──────────────────────────────────────────────
header("9 · Histórico do rastreador")

if "tracker" in state:
    resp = api("GET", f"/trackers/{state['tracker']['id']}/history")
    if resp.status_code == 200:
        hist = resp.json()
        ok(f"Histórico do rastreador — {len(hist)} evento(s)")
        for h in hist[:3]:
            info(f"  {h.get('action')} · {h.get('notes','')}")
    else:
        err(f"Erro ao consultar histórico [{resp.status_code}]")

# ─── 10 · Dashboard ───────────────────────────────────────────────────────────
header("10 · Dashboard")

resp = api("GET", "/dashboard")
if resp.status_code == 200:
    d = resp.json()
    ok(f"Dashboard OK — clientes: {d.get('total_clients','?')} · instalados: {d.get('installed_trackers','?')} · receita mês: {d.get('monthly_revenue','?')}")
else:
    err(f"Dashboard falhou [{resp.status_code}]")

# ─── 11 · Documentos ──────────────────────────────────────────────────────────
header("11 · PDF da timeline do cliente")

if "client" in state:
    resp = api("GET", f"/clients/{state['client']['id']}/timeline-pdf")
    if resp.status_code == 200 and "pdf" in resp.headers.get("content-type", ""):
        ok(f"PDF da timeline gerado — {len(resp.content)} bytes")
    else:
        warn(f"PDF timeline — status {resp.status_code} (verifique se o backend está configurado)")

# ─── Resumo final ─────────────────────────────────────────────────────────────
header("Resumo final")

print(f"""
  Entidades criadas:
    Plano      : #{state.get('plan', {}).get('id', '—')} — {state.get('plan', {}).get('name', '—')}
    Cliente    : #{state.get('client', {}).get('id', '—')} — {state.get('client', {}).get('name', '—')}
    Veículo    : #{state.get('vehicle', {}).get('id', '—')} — {state.get('vehicle', {}).get('plate', '—')}
    Rastreador : #{state.get('tracker', {}).get('id', '—')} — {state.get('tracker', {}).get('imei', '—')}
    Contrato   : #{state.get('contract', {}).get('id', '—')}
    OS         : #{state.get('os', {}).get('id', '—')} — {state.get('os', {}).get('number', '—')}
""")

if FAILURES:
    print(f"  {R}{BOLD}{len(FAILURES)} falha(s) encontrada(s):{RESET}")
    for f in FAILURES:
        print(f"  {R}  • {f}{RESET}")
    print()
    sys.exit(1)
else:
    print(f"  {G}{BOLD}Todos os testes passaram com sucesso!{RESET}")
    print()
