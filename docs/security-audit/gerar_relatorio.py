"""
Gera o relatório de auditoria de segurança em PDF (MasterSat).

Uso (a partir da raiz do repositório):
    docs/security-audit/.venv/Scripts/python docs/security-audit/gerar_relatorio.py
    # ou, com o venv ativado:
    python docs/security-audit/gerar_relatorio.py

Regenera docs/security-audit/relatorio-auditoria-seguranca.pdf a partir dos
achados hardcoded neste script (ACHADOS/PONTOS_FORTES abaixo) — não lê nada
do banco nem do código em tempo de execução, então editar um achado é editar
este arquivo e rodar de novo.

Dependências: reportlab, matplotlib (instaladas no venv local, nunca global).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, KeepTogether, ListFlowable, ListItem, PageBreak, NextPageTemplate,
    HRFlowable,
)
from reportlab.platypus.flowables import Flowable

# ─── Paleta ───────────────────────────────────────────────────────────────
COR_CRITICA = colors.HexColor('#B91C1C')
COR_ALTA = colors.HexColor('#EA580C')
COR_MEDIA = colors.HexColor('#D97706')
COR_BAIXA = colors.HexColor('#2563EB')
COR_FORTE = colors.HexColor('#059669')
COR_INFO = colors.HexColor('#64748B')
COR_MARCA = colors.HexColor('#1E3A5F')
COR_TEXTO = colors.HexColor('#1F2937')
COR_TEXTO_CLARO = colors.HexColor('#475569')
COR_FUNDO_ALT = colors.HexColor('#F1F5F9')
COR_BORDA = colors.HexColor('#CBD5E1')

SEVERIDADE_COR = {
    'crítica': COR_CRITICA,
    'alta': COR_ALTA,
    'média': COR_MEDIA,
    'baixa': COR_BAIXA,
    'informativa': COR_INFO,
}

HOJE = date.today().strftime('%d/%m/%Y')


# ─── Modelo de dados ────────────────────────────────────────────────────────

@dataclass
class Achado:
    severidade: str  # crítica | alta | média | baixa | informativa
    categoria: str
    arquivo_linha: str
    descricao: str
    por_que: str
    impacto: str
    correcao: str
    criterios: list[str]
    labels_extra: list[str] = field(default_factory=list)


@dataclass
class PontoForte:
    categoria: str
    descricao: str
    evidencia: str


# ─── Conteúdo da auditoria ──────────────────────────────────────────────────
# (Resultado da revisão manual, arquivo por arquivo, de TODO o backend
# (30 routers em backend/app/api/v1/endpoints/), do frontend Next.js, dos
# docker-compose/.env.example, do histórico git e dos scripts de operação em
# backend/scripts/ — ver metodologia na capa.)

ACHADOS: list[Achado] = [
    Achado(
        severidade='baixa',
        categoria='Chaves expostas (hardcode)',
        arquivo_linha='backend/scripts/ailos_login_direto.py:76',
        descricao=(
            'A flag --senha do script de autorização headless do cooperado Ailos tem um '
            'valor padrão fixo no código-fonte versionado: '
            "parser.add_argument('--senha', default='aaaaa11111@', ...)."
        ),
        por_que=(
            'É uma credencial (ainda que de homologação/sandbox da Ailos, não da produção '
            'MasterSat) commitada em texto plano no git. Qualquer pessoa com acesso ao '
            'repositório — inclusive em forks, clones antigos ou um histórico exposto por '
            'engano — vê a senha. Se o operador rodar o script sem passar --senha '
            '(esquecimento comum em scripts de manutenção), a tentativa de login usa esse '
            'valor fixo contra a conta informada em --cooperativa/--conta. Some-se a isso o '
            'próprio aviso no docstring do arquivo: 3 senhas erradas BLOQUEIAM a conta '
            'cooperado na Ailos — um valor padrão errado por engano tem custo operacional '
            'real, não só de segurança.'
        ),
        impacto=(
            'Baixo isoladamente (ambiente de homologação/sandbox, exige acesso de shell ao '
            'servidor e conhecimento de --cooperativa/--conta), mas é o tipo de default que '
            '"vira segredo real" se alguém reaproveitar o mesmo padrão de senha em produção '
            'ou se o valor coincidir com uma senha real por reuso de padrão do cooperado.'
        ),
        correcao=(
            'Remover o valor default; exigir --senha explicitamente (ou ler de variável de '
            'ambiente, ex.: AILOS_HOMOLOG_SENHA, seguindo o mesmo padrão já usado pelo resto '
            'do projeto — nunca no código). Se o objetivo era só documentar o formato '
            'esperado, usar um placeholder óbvio (ex.: "PREENCHA_A_SENHA_AQUI") em vez de um '
            'valor funcional.'
        ),
        criterios=[
            'O script não tem mais um valor funcional de senha no código-fonte.',
            '--senha se torna obrigatório OU passa a ler de variável de ambiente dedicada.',
            'git grep por padrões de senha em backend/scripts/ não encontra mais literais.',
        ],
    ),
]

PONTOS_FORTES: list[PontoForte] = [
    PontoForte(
        categoria='Isolamento (equivalente a RLS)',
        descricao=(
            'O sistema é de uso interno de uma única organização (não multi-tenant/SaaS): '
            'não existem múltiplos clientes/organizações isolados dividindo o mesmo banco. '
            'O único papel externo (\"cliente\") tem o login desativado no servidor — não '
            'apenas escondido na UI — então não há hoje um limite de posse entre contas de '
            'clientes finais a ser furado.'
        ),
        evidencia='backend/app/api/v1/endpoints/auth.py:152-153 (login recusa role=cliente com 403)',
    ),
    PontoForte(
        categoria='Autorização por papel (equivalente ao Category 2)',
        descricao=(
            'Todos os 30 routers do backend foram lidos integralmente. Cada endpoint '
            'sensível usa Depends(require_roles(...)) com o conjunto de papéis mínimo '
            'necessário (ex.: /users é ADMIN-only; /contracts e /billings excluem '
            'OPERATIONAL; /audit-logs é ADMIN-only; /settings/email é ADMIN-only). Não foi '
            'encontrado nenhum endpoint de escrita ou de dado sensível sem dependência de '
            'papel, e os gates de UI no frontend (isAdmin, telas de Usuários/Configurações) '
            'têm sempre o espelho correspondente no backend — a UI nunca é a única barreira.'
        ),
        evidencia=(
            'backend/app/api/v1/endpoints/{users,settings,audit_logs,contracts,billings}.py — '
            'require_roles em toda rota; frontend/lib/route-roles.ts + middleware.ts '
            'documentam explicitamente que a barreira real é o backend'
        ),
    ),
    PontoForte(
        categoria='IDOR',
        descricao=(
            'Percorridos sistematicamente todos os handlers que recebem um ID (path/query/'
            'body). Recursos aninhados (documentos de cliente/veículo/OS, materiais de OS, '
            'itens de cobrança) sempre filtram por reference_type+reference_id / '
            'service_order_id junto do ID solicitado — nunca só pelo ID isolado — então um '
            'ID de outro dono devolve 404, não o registro alheio. Vínculos cruzados '
            '(veículo↔cliente, rastreador↔veículo, contrato↔cliente) são revalidados a cada '
            'escrita (ex.: validate_links em contracts.py e client_charge_items.py).'
        ),
        evidencia=(
            'backend/app/api/v1/endpoints/clients.py:373-383, service_orders.py:441-449, '
            'contracts.py:103-117'
        ),
    ),
    PontoForte(
        categoria='Chaves expostas — validação de startup',
        descricao=(
            'config.py recusa subir em produção (enforce_security/ENVIRONMENT=production) '
            'se SECRET_KEY for fraca/padrão/curta, senha do Postgres ou MinIO ainda for a '
            'padrão, faltar AILOS_TOKEN_ENCRYPTION_KEY com credenciais Ailos configuradas, ou '
            'o Multiportal estiver ligado sobre HTTP sem aceite explícito do risco. Nenhum '
            'segredo real foi encontrado versionado: apenas .env.example (só placeholders) '
            'está no git; backend/.env, os tokens da Ailos e certificados .pfx estão '
            'no .gitignore. O histórico do git também não contém nenhum .env nem chave '
            'commitada.'
        ),
        evidencia='backend/app/core/config.py:215-258; .gitignore:8-23; .env.example',
    ),
    PontoForte(
        categoria='XSS',
        descricao=(
            'Nenhum uso de dangerouslySetInnerHTML, innerHTML, document.write, eval ou new '
            'Function em todo o frontend Next.js/React. Os poucos links (tag &lt;a&gt;) '
            'renderizados a partir de dado dinâmico usam sempre URLs geradas pelo próprio backend '
            '(documentos, boletos, NFS-e) ou, no caso de ErrorBanner, uma regex que só aceita '
            'o prefixo http(s):// — bloqueando href com esquema javascript:. Exportações '
            'CSV/XLSX neutralizam fórmulas (=, +, -, @) para evitar CSV/Excel injection. '
            'E-mails são enviados só como texto puro (MIMEText/set_content), nunca como HTML '
            'com dado do usuário interpolado.'
        ),
        evidencia=(
            'frontend/components/ui/error-banner.tsx:10-26; '
            'backend/app/api/v1/endpoints/exports.py:39-47; '
            'backend/app/services/email_smtp.py:134-136'
        ),
    ),
    PontoForte(
        categoria='Links públicos sem login',
        descricao=(
            'Os dois pontos que expõem dados sem JWT (boleto do cliente via link do '
            'WhatsApp/e-mail e o webhook de integração via API key) usam token HMAC-SHA256 '
            'derivado do SECRET_KEY com comparação em tempo constante '
            '(hmac.compare_digest), e o X-API-Key também usa secrets.compare_digest — sem '
            'vetor de força bruta ou timing attack óbvio.'
        ),
        evidencia='backend/app/api/v1/endpoints/boletos.py:53-63,522; backend/app/api/deps.py:64',
    ),
]

CATEGORIAS_LABELS = [
    'Isolamento/RLS\nequivalente',
    'Permissão só\nno navegador',
    'IDOR',
    'Chaves\nexpostas',
    'XSS',
]

# ─── Gráficos ────────────────────────────────────────────────────────────────

def _grafico_rosca() -> io.BytesIO:
    def _hex(c):
        return '#{:02x}{:02x}{:02x}'.format(int(c.red * 255), int(c.green * 255), int(c.blue * 255))

    contagem = {'crítica': 0, 'alta': 0, 'média': 0, 'baixa': 0}
    for a in ACHADOS:
        contagem[a.severidade] = contagem.get(a.severidade, 0) + 1
    labels, valores = [], []
    for sev in ('crítica', 'alta', 'média', 'baixa'):
        if contagem[sev] > 0:
            labels.append(f'{sev.capitalize()} ({contagem[sev]})')
            valores.append(contagem[sev])
    cores = [_hex(SEVERIDADE_COR[s]) for s in ('crítica', 'alta', 'média', 'baixa') if contagem[s] > 0]

    fig, ax = plt.subplots(figsize=(4.2, 3.4), dpi=200)
    if not valores:
        ax.text(0.5, 0.5, 'Nenhum achado\nacionável', ha='center', va='center', fontsize=12, color='#475569')
        ax.axis('off')
    else:
        wedges, _texts = ax.pie(
            valores, colors=cores, startangle=90, counterclock=False,
            wedgeprops={'width': 0.42, 'edgecolor': 'white', 'linewidth': 2},
        )
        ax.text(0, 0.08, str(sum(valores)), ha='center', va='center', fontsize=26, fontweight='bold', color='#1F2937')
        ax.text(0, -0.18, 'achado(s)', ha='center', va='center', fontsize=10, color='#64748B')
        ax.legend(wedges, labels, loc='center left', bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=9)
        ax.axis('equal')
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', transparent=True, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _grafico_barras() -> io.BytesIO:
    achados_por_cat = {c: 0 for c in [
        'Isolamento/RLS\nequivalente', 'Permissão só\nno navegador', 'IDOR', 'Chaves\nexpostas', 'XSS',
    ]}
    mapa_categoria = {
        'Chaves expostas (hardcode)': 'Chaves\nexpostas',
    }
    for a in ACHADOS:
        cat = mapa_categoria.get(a.categoria, a.categoria)
        if cat in achados_por_cat:
            achados_por_cat[cat] += 1

    fortes_por_cat = {c: 0 for c in achados_por_cat}
    mapa_forte = {
        'Isolamento (equivalente a RLS)': 'Isolamento/RLS\nequivalente',
        'Autorização por papel (equivalente ao Category 2)': 'Permissão só\nno navegador',
        'IDOR': 'IDOR',
        'Chaves expostas — validação de startup': 'Chaves\nexpostas',
        'XSS': 'XSS',
        'Links públicos sem login': None,
    }
    for p in PONTOS_FORTES:
        cat = mapa_forte.get(p.categoria)
        if cat:
            fortes_por_cat[cat] += 1

    labels = list(achados_por_cat.keys())
    vals_achados = [achados_por_cat[c] for c in labels]
    vals_fortes = [fortes_por_cat[c] for c in labels]

    fig, ax = plt.subplots(figsize=(7.4, 3.5), dpi=200)
    x = range(len(labels))
    largura = 0.36
    cor_achado = '#B91C1C'
    cor_forte = '#059669'
    b1 = ax.bar([i - largura / 2 for i in x], vals_achados, width=largura, label='Achados', color=cor_achado)
    b2 = ax.bar([i + largura / 2 for i in x], vals_fortes, width=largura, label='Pontos fortes verificados', color=cor_forte)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel('Quantidade', fontsize=9)
    max_y = max(max(vals_achados, default=0), max(vals_fortes, default=0), 1)
    ax.set_yticks(range(0, max_y + 2))
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.legend(frameon=False, fontsize=8.5, loc='upper right')
    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.annotate(f'{int(h)}', (rect.get_x() + rect.get_width() / 2, h),
                        textcoords='offset points', xytext=(0, 3), ha='center', fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', transparent=True, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


# ─── Layout / cabeçalho / rodapé ────────────────────────────────────────────

TITULO_RELATORIO = 'Relatório de Auditoria de Segurança — MasterSat'


def _header_footer(canvas, doc):
    canvas.saveState()
    largura, altura = A4
    # Cabeçalho
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(COR_TEXTO_CLARO)
    canvas.drawString(2 * cm, altura - 1.3 * cm, TITULO_RELATORIO)
    canvas.drawRightString(largura - 2 * cm, altura - 1.3 * cm, HOJE)
    canvas.setStrokeColor(COR_BORDA)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, altura - 1.45 * cm, largura - 2 * cm, altura - 1.45 * cm)
    # Rodapé
    canvas.line(2 * cm, 1.5 * cm, largura - 2 * cm, 1.5 * cm)
    canvas.setFont('Helvetica', 8)
    canvas.drawString(2 * cm, 1.1 * cm, 'MasterSat — Auditoria de Segurança')
    canvas.drawRightString(largura - 2 * cm, 1.1 * cm, f'Página {doc.page}')
    canvas.restoreState()


def _capa(canvas, doc):
    canvas.saveState()
    largura, altura = A4
    canvas.setFillColor(COR_MARCA)
    canvas.rect(0, altura - 7.5 * cm, largura, 7.5 * cm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont('Helvetica-Bold', 25)
    canvas.drawString(2.2 * cm, altura - 3.6 * cm, 'Relatório de Auditoria')
    canvas.drawString(2.2 * cm, altura - 4.5 * cm, 'de Segurança')
    canvas.setFont('Helvetica', 15)
    canvas.drawString(2.2 * cm, altura - 5.6 * cm, 'MasterSat — Sistema de Gestão de Rastreamento Veicular')
    canvas.setFont('Helvetica', 10)
    canvas.setFillColor(colors.HexColor('#CBD5E1'))
    canvas.drawString(2.2 * cm, altura - 6.4 * cm, f'Emitido em {HOJE}')
    canvas.restoreState()


# ─── Documento ──────────────────────────────────────────────────────────────

def build_pdf(path: str) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('H1MS', parent=styles['Heading1'], textColor=COR_MARCA, fontSize=17, spaceAfter=10, spaceBefore=4))
    styles.add(ParagraphStyle('H2MS', parent=styles['Heading2'], textColor=COR_MARCA, fontSize=13, spaceAfter=8, spaceBefore=14))
    styles.add(ParagraphStyle('H3MS', parent=styles['Heading3'], textColor=COR_TEXTO, fontSize=10.5, spaceAfter=4, spaceBefore=10, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle('CorpoMS', parent=styles['BodyText'], fontSize=9.3, leading=13.5, textColor=COR_TEXTO, spaceAfter=6))
    styles.add(ParagraphStyle('CorpoPequeno', parent=styles['BodyText'], fontSize=8.4, leading=12, textColor=COR_TEXTO_CLARO))
    styles.add(ParagraphStyle('Capa', parent=styles['Normal'], fontSize=11, textColor=COR_TEXTO, leading=15))
    styles.add(ParagraphStyle('CelulaTabela', parent=styles['BodyText'], fontSize=8, leading=10.5, textColor=COR_TEXTO))
    styles.add(ParagraphStyle('Mono', parent=styles['BodyText'], fontName='Courier', fontSize=8, leading=11, textColor=COR_TEXTO, backColor=colors.HexColor('#F8FAFC')))
    styles.add(ParagraphStyle('IssueTitulo', parent=styles['Heading3'], fontSize=11, textColor=COR_MARCA, spaceBefore=14, spaceAfter=4))

    doc = BaseDocTemplate(
        path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title=TITULO_RELATORIO, author='Auditoria de Segurança MasterSat',
    )
    frame_capa = Frame(0, 0, A4[0], A4[1], id='capa', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    frame_normal = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
    doc.addPageTemplates([
        PageTemplate(id='Capa', frames=[frame_capa], onPage=_capa),
        PageTemplate(id='Normal', frames=[frame_normal], onPage=_header_footer),
    ])

    story: list = []

    # ── CAPA ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 9 * cm))
    story.append(Paragraph('<b>Escopo auditado</b>', styles['H2MS']))
    story.append(Paragraph(
        'Código-fonte completo do repositório MasterSat: backend (FastAPI + SQLAlchemy + '
        'PostgreSQL/SQLite, autenticação JWT via jose/passlib), frontend (Next.js/React + '
        'TypeScript), arquivos de deploy (docker-compose.yml/.prod.yml/.caddy.yml, '
        'Dockerfiles, nginx), scripts operacionais em backend/scripts/ e o histórico do git '
        '(commits de todos os branches locais). Não há pipeline de CI (.github) configurado '
        'neste repositório.',
        styles['Capa'],
    ))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph('<b>Nota metodológica — mapeamento por categoria</b>', styles['H2MS']))
    metodologia = [
        ('1. Banco sem tranca (isolamento)',
         'Projeto não usa Supabase/RLS. Mecanismo real de isolamento = papéis de usuário '
         '(admin/operacional/financeiro/cliente) validados por Depends(require_roles(...)) '
         'em app/api/deps.py. O papel "cliente" (que seria o limite de posse entre contas '
         'externas) está com o login desativado no servidor — verificado em auth.py.'),
        ('2. Permissão definida no navegador',
         'Cruzados os gates de papel do frontend (lib/route-roles.ts, componentes com '
         'checagem de role) contra o require_roles() do endpoint correspondente, rota a '
         'rota, nos 30 routers do backend.'),
        ('3. IDOR',
         'Todos os handlers que recebem um ID por path/query/body foram lidos '
         'integralmente (não por amostragem) em app/api/v1/endpoints/*.py, verificando '
         'existência + relação de posse (reference_type/reference_id, contract_id, '
         'client_id) antes de ler/alterar/excluir.'),
        ('4. Chaves expostas',
         'Revisados app/core/config.py (defaults e validação de startup), os três '
         'docker-compose*.yml, .env.example, backend/scripts/*.py e o histórico git '
         '(git log -p sobre *.env*) em busca de segredos reais ou defaults inseguros sem '
         'trava de produção.'),
        ('5. Inputs sem tratamento (XSS)',
         'Frontend: busca por dangerouslySetInnerHTML/innerHTML/eval/new Function e por '
         'href dinâmico. Backend: verificado se e-mails/templates renderizam HTML com dado '
         'do usuário sem escape, e se exportações CSV/XLSX neutralizam fórmulas.'),
    ]
    for titulo, texto in metodologia:
        story.append(Paragraph(f'<b>{titulo}</b> — {texto}', styles['CorpoPequeno']))
        story.append(Spacer(1, 3))

    story.append(NextPageTemplate('Normal'))
    story.append(PageBreak())

    # ── RESUMO EXECUTIVO ──────────────────────────────────────────────────
    story.append(Paragraph('Resumo executivo', styles['H1MS']))

    contagem = {'crítica': 0, 'alta': 0, 'média': 0, 'baixa': 0}
    for a in ACHADOS:
        contagem[a.severidade] += 1
    total = sum(contagem.values())

    resumo_txt = (
        f'A auditoria cobriu sistematicamente os 30 routers do backend, todo o frontend '
        f'Next.js, os três arquivos docker-compose, o .env.example, o histórico do git e os '
        f'{"13"} scripts operacionais versionados em backend/scripts/. Foi encontrado '
        f'<b>{total} achado</b> acionável, de severidade <b>baixa</b>, na categoria "Chaves '
        f'expostas". Nas categorias 1 (isolamento), 2 (permissão só no navegador), 3 (IDOR) '
        f'e 5 (XSS) não foi encontrado nenhum ponto explorável após cobertura completa — '
        f'o motivo mais provável é que o projeto já passou por rodadas anteriores de '
        f'hardening documentadas nos próprios comentários do código (ver seção de pontos '
        f'fortes, com evidência de arquivo:linha para cada afirmação).'
    )
    story.append(Paragraph(resumo_txt, styles['CorpoMS']))
    story.append(Spacer(1, 6))

    tabela_resumo_dados = [['Severidade', 'Qtd.']]
    cores_linhas = []
    for sev in ('crítica', 'alta', 'média', 'baixa'):
        tabela_resumo_dados.append([sev.capitalize(), str(contagem[sev])])
    tabela_resumo_dados.append(['Total', str(total)])
    tabela_resumo = Table(tabela_resumo_dados, colWidths=[3.2 * cm, 1.8 * cm])
    estilo_resumo = [
        ('BACKGROUND', (0, 0), (-1, 0), COR_MARCA),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('GRID', (0, 0), (-1, -1), 0.4, COR_BORDA),
        ('BACKGROUND', (0, -1), (-1, -1), COR_FUNDO_ALT),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for i, sev in enumerate(('crítica', 'alta', 'média', 'baixa'), start=1):
        estilo_resumo.append(('TEXTCOLOR', (0, i), (0, i), SEVERIDADE_COR[sev]))
    tabela_resumo.setStyle(TableStyle(estilo_resumo))

    img_rosca = Image(_grafico_rosca(), width=8.6 * cm, height=7 * cm)
    tabela_e_grafico = Table([[tabela_resumo, img_rosca]], colWidths=[5.2 * cm, 9.5 * cm])
    tabela_e_grafico.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    story.append(tabela_e_grafico)

    story.append(Spacer(1, 10))
    story.append(Paragraph('Achados e pontos fortes verificados, por categoria', styles['H3MS']))
    story.append(Image(_grafico_barras(), width=15.5 * cm, height=7.3 * cm))

    story.append(PageBreak())

    # ── PONTOS FORTES / PONTOS FRACOS ─────────────────────────────────────
    story.append(Paragraph('Pontos fortes (verificados, com evidência)', styles['H1MS']))
    for p in PONTOS_FORTES:
        bloco = [
            Paragraph(f'<font color="#059669">&#9679;</font> <b>{p.categoria}</b>', styles['H3MS']),
            Paragraph(p.descricao, styles['CorpoMS']),
            Paragraph(f'<b>Evidência:</b> <font face="Courier" size=8>{p.evidencia}</font>', styles['CorpoPequeno']),
            Spacer(1, 4),
        ]
        story.append(KeepTogether(bloco))

    story.append(Spacer(1, 8))
    story.append(Paragraph('Pontos fracos (risco central)', styles['H1MS']))
    if ACHADOS:
        story.append(Paragraph(
            'Um único ponto de atenção, de severidade baixa: um script de manutenção da '
            'integração Ailos versionado no git carrega um valor padrão de senha de '
            'homologação embutido no código (ver detalhamento e issue sugerida adiante). '
            'Não há, hoje, um vetor de acesso não autorizado a dados de clientes, '
            'faturamento ou configuração — a cobertura de autorização (papéis) e de posse '
            '(IDOR) no backend é consistente em toda a superfície de API.',
            styles['CorpoMS'],
        ))
    else:
        story.append(Paragraph('Nenhum ponto fraco explorável identificado.', styles['CorpoMS']))

    story.append(PageBreak())

    # ── TABELA DE ACHADOS DETALHADOS ──────────────────────────────────────
    story.append(Paragraph('Achados detalhados', styles['H1MS']))
    if not ACHADOS:
        story.append(Paragraph('Nenhum achado acionável nesta auditoria.', styles['CorpoMS']))
    else:
        cabecalho = [
            Paragraph('<b>Severidade</b>', styles['CelulaTabela']),
            Paragraph('<b>Categoria</b>', styles['CelulaTabela']),
            Paragraph('<b>Arquivo:linha</b>', styles['CelulaTabela']),
            Paragraph('<b>Descrição</b>', styles['CelulaTabela']),
        ]
        linhas = [cabecalho]
        for a in ACHADOS:
            chip = Table([[a.severidade.upper()]], colWidths=[1.8 * cm])
            chip.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), SEVERIDADE_COR[a.severidade]),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 6.8),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('ROUNDEDCORNERS', [3, 3, 3, 3]),
            ]))
            linhas.append([
                chip,
                Paragraph(a.categoria, styles['CelulaTabela']),
                Paragraph(a.arquivo_linha, styles['CelulaTabela']),
                Paragraph(a.descricao, styles['CelulaTabela']),
            ])
        tabela_achados = Table(linhas, colWidths=[2.1 * cm, 3.3 * cm, 4.3 * cm, 6.8 * cm], repeatRows=1)
        tabela_achados.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), COR_MARCA),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.4, COR_BORDA),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, COR_FUNDO_ALT]),
        ]))
        story.append(tabela_achados)

        story.append(Spacer(1, 14))
        story.append(Paragraph('Detalhamento por achado', styles['H2MS']))
        for i, a in enumerate(ACHADOS, start=1):
            bloco = [
                Paragraph(f'{i}. {a.categoria} — <font color="{"#2563EB"}">{a.arquivo_linha}</font>', styles['H3MS']),
                Paragraph(f'<b>Descrição:</b> {a.descricao}', styles['CorpoMS']),
                Paragraph(f'<b>Por que é explorável:</b> {a.por_que}', styles['CorpoMS']),
                Paragraph(f'<b>Impacto:</b> {a.impacto}', styles['CorpoMS']),
                Paragraph(f'<b>Correção sugerida:</b> {a.correcao}', styles['CorpoMS']),
                Spacer(1, 6),
            ]
            story.append(KeepTogether(bloco))

    story.append(PageBreak())

    # ── RECOMENDAÇÕES PRIORIZADAS ──────────────────────────────────────────
    story.append(Paragraph('Recomendações priorizadas', styles['H1MS']))
    recomendacoes = [
        ('P1', 'Remover o valor padrão de senha em backend/scripts/ailos_login_direto.py:76 '
               '(único achado acionável desta auditoria).'),
        ('P2', 'Manter a disciplina já em vigor: todo novo endpoint precisa nascer com '
               'Depends(require_roles(...)) explícito — o projeto não tem um middleware '
               'global de autorização, então a proteção depende de cada rota declarar o '
               'próprio require_roles. Considerar um teste automatizado que falhe o build '
               'se algum router novo não tiver ao menos uma dependência de autenticação.'),
        ('P3', 'Repetir esta auditoria (ou a seção de achados) a cada lote grande de '
               'features novas — em particular ao finalizar a migração SGR Hinova '
               '(backend/app/services/sgr_migration/), que hoje é só leitura (GET) e um '
               'importador local gated por --apply, mas passará a escrever em produção.'),
    ]
    for prioridade, texto in recomendacoes:
        story.append(Paragraph(f'<b>{prioridade}.</b> {texto}', styles['CorpoMS']))

    story.append(PageBreak())

    # ── ISSUES PARA O GITHUB ───────────────────────────────────────────────
    story.append(Paragraph('Issues para o GitHub', styles['H1MS']))
    story.append(Paragraph(
        'Texto completo pronto para copiar e colar como issue no GitHub, um bloco por '
        'achado acionável.',
        styles['CorpoMS'],
    ))
    story.append(Spacer(1, 6))

    for i, a in enumerate(ACHADOS, start=1):
        criterios_md = '\n'.join(f'- [ ] {c}' for c in a.criterios)
        labels = f'security, severidade:{a.severidade}'
        corpo_md = (
            f"[Segurança] Senha padrão de homologação Ailos hardcoded em script de manutenção\n\n"
            f"**Labels sugeridas:** {labels}\n\n"
            f"## Problema\n"
            f"{a.descricao}\n\n"
            f"## Por que é explorável\n"
            f"{a.por_que}\n\n"
            f"## Evidência\n"
            f"`{a.arquivo_linha}`\n\n"
            f"```python\n"
            f"parser.add_argument('--senha', default='aaaaa11111@',\n"
            f"                    help='Senha de homologação (default: aaaaa11111@)')\n"
            f"```\n\n"
            f"## Impacto\n"
            f"{a.impacto}\n\n"
            f"## Sugestão de correção\n"
            f"{a.correcao}\n\n"
            f"## Critérios de aceite\n"
            f"{criterios_md}\n"
        )
        story.append(Paragraph(f'--- ISSUE {i} ---', styles['IssueTitulo']))
        story.append(Paragraph(corpo_md.replace('\n', '<br/>').replace('  ', '&nbsp;&nbsp;'), styles['Mono']))
        story.append(Paragraph(f'--- FIM ISSUE {i} ---', styles['CorpoPequeno']))
        story.append(Spacer(1, 10))

    doc.build(story)


if __name__ == '__main__':
    import os
    saida = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'relatorio-auditoria-seguranca.pdf')
    build_pdf(saida)
    print(f'PDF gerado em: {saida}')
