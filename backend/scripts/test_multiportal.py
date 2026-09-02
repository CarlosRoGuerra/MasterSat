"""
Teste de conectividade e operações básicas — Multiportal Web Service
Executar dentro de backend/ (lê credenciais de backend/.env):
    pip install zeep requests
    python scripts/test_multiportal.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

try:
    import requests
    from zeep import Client, Settings
    from zeep.helpers import serialize_object
    from zeep.transports import Transport
except ImportError:
    print("❌  Dependências ausentes. Execute: pip install zeep requests")
    sys.exit(1)

from app.core.config import settings as app_settings  # noqa: E402

# ─── Credenciais ──────────────────────────────────────────────────────────────
# MP_ID/MP_SENHA vêm de backend/.env (MULTIPORTAL_ID/MULTIPORTAL_PASSWORD) —
# nunca hardcode credencial real aqui, este arquivo é versionado no git.
WSDL_URL  = "http://ws1.1gps.com.br:80/services/IntegracaoAdmService?wsdl"
MP_ID     = app_settings.multiportal_id
MP_SENHA  = app_settings.multiportal_password
TIMEOUT   = 30

if not MP_ID or not MP_SENHA:
    print("❌  MULTIPORTAL_ID / MULTIPORTAL_PASSWORD não configurados em backend/.env")
    sys.exit(1)

SUCCESS_CODES    = {"0", "200"}
IDEMPOTENT_CODES = {"20", "21", "22", "40", "56", "59"}
SOFT_OK          = SUCCESS_CODES | IDEMPOTENT_CODES

# ─── Helpers visuais ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✔  {msg}{RESET}")
def err(msg):   print(f"  {RED}✘  {msg}{RESET}")
def warn(msg):  print(f"  {YELLOW}⚠  {msg}{RESET}")
def info(msg):  print(f"  {CYAN}ℹ  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{'─'*60}\n  {msg}\n{'─'*60}{RESET}")

def result_line(op, code, desc, success):
    icon = f"{GREEN}✔{RESET}" if success else f"{RED}✘{RESET}"
    print(f"  {icon}  [{code}] {op} — {desc or 'sem descrição'}")

def tid():
    # idTransacao é Java Long (max 19 dígitos). Usamos apenas até segundos (14 dígitos).
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

# ─── Diagnóstico de rede ──────────────────────────────────────────────────────
header("0 · Diagnóstico de rede")
import socket

HOST = "ws1.1gps.com.br"
PORT = 80

# 1) Resolução DNS
print(f"  Resolvendo DNS: {HOST} ...")
try:
    ip = socket.gethostbyname(HOST)
    ok(f"DNS ok — {HOST} → {ip}")
except socket.gaierror as e:
    err(f"Falha de DNS: {e}")
    warn("O hostname não pôde ser resolvido. Causas possíveis:")
    print("       1. Servidor Multiportal offline ou endereço DDNS expirado.")
    print("       2. Bloqueio de firewall / proxy na sua rede.")
    print("       3. URL desatualizada — consulte a Multiportal pelo e-mail suporte@multiportal.com.br")
    print()
    warn("Testando conectividade geral com a internet...")
    try:
        socket.gethostbyname("google.com")
        ok("Internet ok — o problema é específico no hostname Multiportal.")
    except socket.gaierror:
        err("Sem acesso à internet — verifique sua conexão de rede.")
    print()
    warn("Tentando conexão direta por IP (caso o DNS esteja falhando localmente)...")
    # Tenta alguns IPs conhecidos que o dynalias pode ter apontado
    # O usuário pode preencher manualmente se souber o IP
    FALLBACK_IP = None  # ex: "177.10.0.1"
    if FALLBACK_IP:
        try:
            s = socket.create_connection((FALLBACK_IP, PORT), timeout=5)
            s.close()
            ok(f"Porta {PORT} aberta em {FALLBACK_IP} — tente trocar o hostname pelo IP no WSDL_URL.")
        except Exception as e2:
            err(f"IP {FALLBACK_IP}:{PORT} também inacessível: {e2}")
    else:
        info("Se você souber o IP do servidor, defina FALLBACK_IP no script para testar.")
    sys.exit(1)

# 2) Conectividade TCP na porta
print(f"  Testando TCP {HOST}:{PORT} ...")
try:
    s = socket.create_connection((HOST, PORT), timeout=10)
    s.close()
    ok(f"Porta {PORT} acessível em {HOST}")
except Exception as e:
    err(f"Porta {PORT} inacessível: {e}")
    warn("O hostname resolve mas a porta está fechada ou filtrada por firewall.")
    sys.exit(1)

# ─── Conexão ao WSDL ──────────────────────────────────────────────────────────
header("1 · Conexão ao WSDL")
try:
    session = requests.Session()
    transport = Transport(session=session, timeout=TIMEOUT)
    client = Client(WSDL_URL, transport=transport, settings=Settings(strict=False, xml_huge_tree=True))
    ok(f"WSDL carregado: {WSDL_URL}")
except Exception as exc:
    err(f"Não foi possível conectar ao WSDL: {exc}")
    sys.exit(1)

# ─── listarFabricantes ────────────────────────────────────────────────────────
header("2 · listarFabricantes (valida credenciais)")
try:
    raw = client.service.listarFabricantes(id=MP_ID, senha=MP_SENHA)
    payload = serialize_object(raw) or []

    if isinstance(payload, dict):
        items = payload.get("elements") or payload.get("Dominio") or payload.get("return") or []
    else:
        items = payload

    if not items:
        warn("Resposta vazia — credenciais podem estar incorretas ou o serviço não retornou fabricantes.")
    else:
        ok(f"{len(items)} fabricante(s) recebidos:")
        for item in items:
            if isinstance(item, dict):
                print(f"       ID {item.get('codigo','?'):>3}  →  {item.get('descricao','?')}")
except Exception as exc:
    err(f"Erro ao listar fabricantes: {exc}")

# ─── sincronizaCliente (dados fictícios) ──────────────────────────────────────
header("3 · sincronizaCliente — sem endereço (estratégia final)")
# Servidor aceita clientes sem listaEnderecos (retorna 200).
# lat/long 0.0 são rejeitados com código 1111 e o sistema não armazena GPS.

try:
    raw = client.service.sincronizaCliente(
        id=MP_ID, senha=MP_SENHA, idTransacao=tid(), codigoOperacao=4,
        cliente={
            "codigoIntegracao": 9999,
            "tipoCliente": "F",
            "documento": "00000000191",
            "categoriaCliente": "C",
            "nome": "TESTE INTEGRACAO ERP",
            "email": "teste.erp@mastersat.local",
            "listaContatos": [{
                "codigoIntegracao": 99991,
                "nome": "TESTE INTEGRACAO",
                "tipoContato": 2,   # Celular (tipos 4 e 5 rejeitados neste servidor)
                "relacao": 7,
                "valor": "11999990001",
            }],
        },
    )
    p = serialize_object(raw) or {}
    code = str(p.get("statusCode", ""))
    desc = p.get("statusDescription", "")
    result_line("sincronizaCliente", code, desc, code in SOFT_OK)
    if code in IDEMPOTENT_CODES:
        info("Idempotente — cliente já existe (normal).")
except Exception as exc:
    err(f"Erro: {exc}")

# ─── sincronizaVeiculo (dados fictícios) ──────────────────────────────────────
header("4 · sincronizaVeiculo — dados de teste")

veiculo_payload = {
    "codigoIntegracao": 9999,
    "chassi": "9BWZZZ377VT004251",    # chassi fictício padrão
    "placa": "TST0001",
    "tipoVeiculo": 1,                  # passeio
    "marca": "TESTE",
    "modelo": "MODELO TESTE",
    "anoModelo": 2024,
    "anoFabricacao": 2023,
    "cor": "PRETO",
    "apelido": "TST0001",
}

try:
    raw = client.service.sincronizaVeiculo(
        id=MP_ID,
        senha=MP_SENHA,
        idTransacao=tid(),
        codigoOperacao=1,
        veiculo=veiculo_payload,
    )
    p = serialize_object(raw) or {}
    code = str(p.get("statusCode", ""))
    desc = p.get("statusDescription", "")
    success = code in SOFT_OK
    result_line("sincronizaVeiculo", code, desc, success)
    if code in IDEMPOTENT_CODES:
        info("Veículo já cadastrado — comportamento esperado em reteste.")
except Exception as exc:
    err(f"Erro na chamada: {exc}")

# ─── consultaVinculoEquipamento (por chassi fictício) ─────────────────────────
header("5 · consultaVinculoEquipamento — consulta por chassi de teste")

try:
    raw = client.service.consultaVinculoEquipamento(
        id=MP_ID,
        senha=MP_SENHA,
        idTransacao=tid(),
        codigoOperacao=2,              # pesquisa por veículo (chassi)
        chave="9BWZZZ377VT004251",
    )
    p = serialize_object(raw) or {}
    code = str(p.get("statusCode", ""))
    desc = p.get("statusDescription", "")
    elements = p.get("elements") or []
    success = code in SOFT_OK
    result_line("consultaVinculoEquipamento", code, desc, success)
    if elements:
        info(f"Vínculos encontrados: {len(elements)}")
        for elem in elements[:3]:
            print(f"       {elem}")
    else:
        info("Nenhum vínculo retornado para o chassi de teste (esperado).")
except Exception as exc:
    err(f"Erro na chamada: {exc}")

# ─── sincronizaChip (sem ICCID real — deve retornar erro esperado) ────────────
header("6 · sincronizaChip — teste com ICCID fictício (erro esperado)")

try:
    raw = client.service.sincronizaChip(
        id=MP_ID,
        senha=MP_SENHA,
        idTransacao=tid(),
        statusChip=1,
        serialChip="89000000000000000000",   # ICCID fictício
    )
    p = serialize_object(raw) or {}
    code = str(p.get("statusCode", ""))
    desc = p.get("statusDescription", "")
    success = code in SOFT_OK
    result_line("sincronizaChip", code, desc, success)
    if not success:
        info("Erro esperado — ICCID fictício não existe na plataforma.")
except Exception as exc:
    err(f"Erro na chamada: {exc}")

# ─── Resumo ───────────────────────────────────────────────────────────────────
header("Resumo")
print(f"""
  WSDL     : {WSDL_URL}
  ID       : {MP_ID}
  Timeout  : {TIMEOUT}s

  Códigos de sucesso real   : {sorted(SUCCESS_CODES)}
  Códigos idempotentes (ok) : {sorted(IDEMPOTENT_CODES)}

  Se sincronizaCliente e sincronizaVeiculo retornaram 0, 200, 20 ou 21,
  a integração está funcional.

  Se retornaram -1 → credenciais incorretas.
  Se retornaram 99 → falha de conexão ou timeout no servidor.
""")
