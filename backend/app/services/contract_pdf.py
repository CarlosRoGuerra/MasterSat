"""
Gera o PDF do Contrato MasterSat seguindo fielmente o modelo CONTRATO.pdf:

  Página 1 — TERMO DE ADESÃO (formulário com dados da contratada, do
             contratante, condições de pagamento, emergência, placas e taxas).
  Páginas seguintes — CONTRATO (cláusulas 1 a 13, texto fixo) + assinaturas.

Os campos do TERMO são preenchidos com os dados de Contract/Client/Plan/Vehicle
disponíveis; campos sem fonte no cadastro ficam em branco (como no modelo).
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Dados FIXOS da contratada (modelo CONTRATO.pdf) ──────────────────────────
CONTRATADA_RAZAO = 'MASTERSAT COMERCIO E SERVIÇOS DE RASTREAMENTO LTDA'
CONTRATADA_CNPJ = '14.228.344/0001-67'
CONTRATADA_IE = '257125515'
CONTRATADA_ENDERECO = 'RUA MARITIMA, 424 COMASA JOINVILLE SC'
CODIGO_URL = '33801'

# Taxas fixas do termo
TAXA_APOIO_RECUPERACAO = 'R$ 500,00'
TAXA_EQUIPAMENTO = 'R$ 499,99'
TAXA_DESLOCAMENTO = 'R$ 1,00/Km'
TAXA_NO_SHOW = 'R$ 70,00'

_MESES = ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']

# Texto integral do CONTRATO (cláusulas fixas), na ordem do modelo.
CLAUSULAS: list[tuple[str, list[str]]] = [
    ('1. OBJETO', [
        '1.1. Fornecer ao cliente um serviço de informação sobre localização geográfica dos seus ativos. As características do serviço e sua disponibilização através de acesso a aplicativos e/ou sistemas informáticos na nuvem varia de acordo com contratação estabelecida e descrita no TERMO DE ADESÃO (parte integrante deste contrato). Os equipamentos de rastreamento e acessórios são fornecidos em regime de comodato pela MASTERSAT.',
    ]),
    ('2. APOIO À RECUPERAÇÃO', [
        '2.1. Em caso de furto ou roubo, a MASTERSAT em hipótese alguma se responsabilizará pelos eventuais danos ou perdas materiais que possam decorrer desta ocorrência.',
        '2.2. O CONTRATANTE deverá apresentar em até 05 (cinco) dias o B.O. (Boletim de Ocorrência) à CENTRAL DE ATENDIMENTO MASTERSAT após a formalização do incidente, sob pena de incidir multa estabelecida no TERMO DE ADESÃO.',
        '2.3. Caso o equipamento de rastreamento ou acessórios – fornecidos em comodato – estejam comprometidos, será cobrado ao CONTRATANTE o valor contido no TERMO DE ADESÃO, acrescido de impostos (IPI), procedendo a MASTERSAT a instalação de novo equipamento ao custo presente no TERMO DE ADESÃO referente à mão de obra.',
        '2.4. Ocorrendo apenas a localização do equipamento de rastreamento, sem a recuperação do ATIVO, o CONTRATANTE terá até 30 (trinta) dias para enviar, a suas custas, o equipamento para a MASTERSAT para fins de verificação de funcionamento.',
        '2.5. Caso a recuperação não ocorra em até 30 (trinta) dias da ocorrência, o presente Contrato será rescindido entre as partes, sujeito à multa contratual acrescida do valor de um novo equipamento.',
        '2.6. Por motivos de segurança, a MASTERSAT expressamente não recomenda a participação pessoal do CONTRATANTE no SERVIÇO DE APOIO À RECUPERAÇÃO.',
    ]),
    ('3. PRAZO E VIGÊNCIA E FIDELIDADE', [
        '3.1. O presente Contrato, após aceitação cadastral da MASTERSAT, tem a vigência a termo certo e determinado previsto no TERMO DE ADESÃO, contados a partir da data de instalação, sendo automaticamente renovado por prazo indeterminado.',
        '3.2. O CONTRATANTE deverá comunicar exclusivamente à MASTERSAT por escrito, a intenção de cancelamento de contrato com antecedência mínima de 30 (trinta) dias anteriormente ao término deste Contrato.',
        '3.3. Caso haja rescisão dentro do prazo de FIDELIDADE (constado no termo de Adesão), o CONTRATANTE pagará a MASTERSAT uma multa compensatória no valor equivalente a 50% (cinquenta por cento) do valor contratual vincendo mais o valor de desinstalação, contados a partir da data de instalação de cada equipamento de rastreamento.',
        '3.4. Os valores contratuais assim como atividades complementares (ex. deslocamentos, visitas técnicas) serão atualizados a cada 12 (doze) meses pelo IGPM-FGV, ou outro índice que vier a substituí-lo.',
        '3.5. É devido pelo CONTRATANTE, no momento da rescisão contratual ou final de contrato, a taxa de desinstalação presente no TERMO DE ADESÃO.',
    ]),
    ('4. PAGAMENTO', [
        '4.1. Após a assinatura do contrato e devida aprovação cadastral por parte da MASTERSAT, mensalmente será debitado o valor da mensalidade em caso de pagamento realizado via cartão de crédito, ou débito recorrente em cartão de crédito. Em caso de pagamento via boleto bancário, o valor da mensalidade será gerada com antecedência antes da data de vencimento escolhida pelo CONTRATANTE no TERMO DE ADESÃO conforme legislação aplicável.',
        '4.2. O agendamento da instalação é confirmado após confirmação do pagamento da ADESÃO e, no caso do plano sem adesão, o agendamento da instalação é confirmado após a confirmação do pagamento da primeira mensalidade.',
        '4.3. O atraso no pagamento das mensalidades pelo CONTRATANTE acarretará na incidência de multa de 2% (dois por cento) e juros de 1% (um por cento) ao mês ou fração, calculado sobre o valor em atraso devidamente atualizado pelo IGPM-FGV, ou outro índice que vier a substituí-lo.',
        '4.4. Em havendo o não pagamento por um período superior a 10 (dez) dias, a MASTERSAT poderá tomar todas as providências cabíveis pela recuperação de seu crédito, inclusive a promoção de negativação do usuário perante os órgãos de proteção do crédito, assim como o bloqueio do acesso à plataforma.',
        '4.5. Sempre que o CONTRATANTE tenha atraso de pagamento superior a 30 dias, a MASTERSAT poderá utilizar o serviço de empresa terceirizada para cobrança de dívida ou serviços de advogado para cobrança extrajudicial.',
        '4.6. A CONTRATANTE está ciente que em caso de inadimplência lhe serão cobradas despesas adicionais, visando nomeadamente retrabalho de natureza administrativa ou taxas financeiras adicionais, como é o exemplo do custo de desbloqueio da plataforma; os sobrecustos em taxas de natureza financeira/administrativa, e o custo de despromoção de negativação presentes no TERMO DE ADESÃO; ou ainda os custos de serviços terceirizados de cobrança por empresa terceirizada ou sociedade de advogados (em até 30% – trinta por cento – do valor total em dívida).',
        '4.7. Caso o atraso do pagamento seja superior a 30 (trinta) dias, poderá a MASTERSAT aplicar a rescisão imediata do presente Contrato, conforme valores expostos no item 3.3 deste contrato.',
    ]),
    ('5. OBRIGAÇÕES DO CONTRATANTE', [
        '5.1. Responsabilizar-se pelo transporte ou disponibilização do seu ativo (veículo ou outro equipamento a ser rastreado) até ao PONTO DE SERVIÇO mais próximo, ou em alternativa suportar o valor de deslocamento do prestador de serviço da MASTERSAT às suas instalações ao valor presente no TERMO DE ADESÃO (ida e volta).',
        '5.2. Suportar os custos decorrentes de intervenções técnicas presentes no TERMO DE ADESÃO ou danos nos equipamentos de rastreamento, que não sejam imputáveis à MASTERSAT.',
        '5.3. Em caso de necessidade de cancelamento de agendamento técnico, o CONTRATANTE deverá notificar a CENTRAL DE ATENDIMENTO MASTERSAT com um mínimo de 72 (setenta e duas) horas de antecedência, caso contrário, será entendido como não comparecimento – "NO SHOW" – e estará sujeito à multa presente no TERMO DE ADESÃO.',
        '5.4. O CONTRATANTE é responsável pela deslocação em até 30 dias do ATIVO ao prestador de serviços autorizado para que a intervenção de manutenção agendada possa ser efetuada.',
        '5.5. A obrigatoriedade de testar a comunicação do rastreador é do CONTRATANTE e, em caso de qualquer falha identificada, deverá comunicar imediatamente à MASTERSAT para que as providências sejam tomadas.',
        '5.6. O CONTRATANTE é responsável pelo bom estado do equipamento de rastreamento e acessórios e obriga-se a devolver o mesmo em bom estado no término do contrato, salvo desgaste natural decorrente do uso. Caso o equipamento não seja devolvido em até 30 dias, o cliente autoriza o débito automático ao cartão de crédito, débito em conta ou emissão de boleto no valor presente no TERMO DE ADESÃO, acrescido de impostos (IPI).',
    ]),
    ('6. EXCLUSÃO DE RESPONSABILIDADE', [
        '6.1. A MASTERSAT não se responsabiliza por danos infligidos ao equipamento de rastreamento ou acessórios, decorrentes de ações técnicas realizadas por terceiro não credenciado.',
        '6.2. A MASTERSAT só realiza ações de ativação de bloqueio quando este acessório estiver instalado, por solicitação direta do CONTRATANTE à CENTRAL DE ATENDIMENTO MASTERSAT e, em caso algum, se responsabiliza por consequências diretas ou indiretas ocasionadas em virtude do bloqueio enviado.',
        '6.3. O CONTRATANTE tem presente que após bloqueio, caso opte por este serviço, em caso de falha de comunicação e/ou conexão GPRS, a reposição do normal funcionamento do rastreador poderá obrigar a VISITA TÉCNICA, a custas do CONTRATANTE.',
        '6.4. Em caso de adesão ao serviço de bloqueio, o CONTRATANTE está ciente de que a MASTERSAT não se responsabiliza por eventual perda de garantia do veículo nem por tempos de paragem motivados por bloqueio.',
        '6.5. Eventuais pedidos de indenização decorrentes de eventuais danos ou prejuízos diretos ou indiretos alegadamente ocorridos em função do uso do serviço de informação ou software disponibilizado pela MASTERSAT ao CONTRATANTE restringem-se ao valor do contrato. Não será possível recuperar quaisquer outros danos, inclusive danos consequenciais, especiais, indiretos, incidentais, lucros cessantes, ou outros.',
    ]),
    ('7. TROCA DE TITULARIDADE', [
        '7.1. A MASTERSAT prevê a possibilidade de troca de titularidade do contrato, que na prática consiste na possibilidade de cancelamento do presente contrato sem multa de rescisão contratual sempre que ocorra em simultâneo a confirmação, assinatura e aceitação de cadastro de novo CONTRATO DE SERVIÇO nas condições previamente contratadas ou superiores. O CONTRATANTE é responsável, pelos pagamentos mensais do contrato em vigor, até o novo titular assumir. A cobrança e obrigatoriedade de pagamentos continua sem alteração até que o outro Contrato seja formalizado e aceite pela MASTERSAT e confirmado o primeiro pagamento realizado.',
    ]),
    ('8. TROCA DE ATIVO NO CONTRATO', [
        '8.1. Em caso de troca de ativo, o CONTRATANTE obriga-se a pagar o serviço de desinstalação e instalação de novo equipamento de rastreamento, quando aplicável, ao custo presente no TERMO DE ADESÃO.',
    ]),
    ('9. CONFIDENCIALIDADE E SEGURANÇA DE INFORMAÇÃO', [
        '9.1. O CONTRATANTE reconhece e declara ser o único responsável pelo sigilo e correta utilização da senha e do sistema por seus representantes, devendo aplicar medidas de segurança e tomar as precauções necessárias para evitar a divulgação de tais informações a pessoas não autorizadas. Na hipótese de desligamento de pessoas detentoras de acesso ao serviço, o CONTRATANTE deve comunicar de imediato o fato à MASTERSAT, solicitando o seu descadastramento.',
        '9.2. O CONTRATANTE obriga-se a impedir a cópia, distribuição, edição, alienação, cessão, transferência ou adaptação, ou acesso ilícito do software, aplicativo e da plataforma online, de propriedade da MASTERSAT, que venham a ser disponibilizados por força deste instrumento, sendo toda e qualquer informação considerada confidencial para qualquer uso externo.',
    ]),
    ('10. RESCISÃO', [
        '10.1. São motivos justos para a rescisão contratual:',
        '10.1.1. A desídia no cumprimento das obrigações decorrentes do contrato;',
        '10.1.2. A prática de atos que importem em descrédito comercial ou de marca;',
        '10.1.3. A condenação definitiva por crime considerado infamante que comprometa a imagem institucional e de marca;',
        '10.1.4. Força maior ou caso fortuito, ou seja, acontecimentos que fogem da vontade do indivíduo, que escapam de sua diligência, estranhos a sua vontade, eventos inevitáveis, ainda que sejam previsíveis, tratando-se de fatos superiores às forças ou controle das partes tais como os fatos da natureza, como terremotos, enchentes, furacões e ciclones extratropicais;',
        '10.1.5. Permitir, colaborar, facilitar, ou de qualquer modo contribuir, por ação ou omissão, na consecução de fraude na celebração do CADASTRO;',
        '10.1.6. Praticar atos de agressão física ou injúria grave; incluindo a apropriação indevida de valores;',
        '10.1.7. Decretação de falência, declaração do estado de insolvência ou pedido de recuperação judicial;',
        '10.1.8. Por caducidade, após decorridos 30 (trinta) dias sem recuperação do ATIVO e com comprovação de B.O. – Boletim de Ocorrência, contados da comunicação da sua perda;',
        '10.1.9. Por impossibilidade da MASTERSAT em proceder ao agendamento e/ou instalação por falta de retorno ou não disponibilização dos ativos pelo CONTRATANTE, decorridos 30 (trinta) dias da assinatura do presente contrato, sendo que neste caso a rescisão poderá ser parcial, necessitando o CONTRATANTE de celebrar novo contrato caso necessite adicionar ATIVOS para além dos que já foram instalados.',
        '10.2. Em qualquer hipótese, a rescisão do presente contrato não exime a parte da responsabilidade das perdas e danos que der causa.',
    ]),
    ('11. DISPOSIÇÕES GERAIS', [
        '11.1. Se a MASTERSAT estiver direta ou indiretamente impedida ou limitada de cumprir todas as obrigações decorrentes do presente Contrato por motivo que esteja fora do seu controle, os direitos e as obrigações da MASTERSAT previstas no presente contrato serão suspensas durante o período de duração desse evento, não sendo a MASTERSAT responsável durante o período da suspensão de vigência do Contrato, a saber:',
        '11.1.1. Caso o equipamento rastreador esteja fora da área de cobertura GSM/GPRS ou de serviço de comunicação equivalente (ex. satélite ou WiFi);',
        '11.1.2. Caso o serviço de localização GPS esteja inacessível ou indisponível;',
        '11.1.3. Quando, mesmo em área de cobertura, houver interferências eletromagnéticas, solares e outras que impossibilitem o envio ou recepção do sinal;',
        '11.1.4. Em caso de mau funcionamento da bateria ou sistema elétrico do ATIVO;',
        '11.1.5. Caso o Equipamento esteja danificado ou tenha sido solicitada manutenção, devido a mau uso do equipamento pelo CONTRATANTE;',
        '11.1.6. No caso de impedimento ou atraso na realização de ações técnicas de manutenção, seja por paralisação de serviços públicos que afetem a normal execução dos Serviços; causas naturais como tempestades, sabotagens, tumultos, greves e incêndios que impeçam ou dificultem de qualquer forma a normal execução dos Serviços;',
        '11.1.7. Em situações de eventual indisponibilidade do sistema, ou quando da realização de manutenções pela MASTERSAT ou de ações técnicas realizadas pelos seus fornecedores de serviços de computação.',
        '11.2. Fica vedada a participação do CONTRATANTE ou de outros, sem ser equipe MASTERSAT, no processo de instalação.',
        '11.3. No início e final do procedimento de instalação, o CONTRATANTE deve se fazer presente ao local de instalação para checagem do veículo.',
        '11.4. A MASTERSAT tem até 10 dias para reativar a plataforma do CONTRATANTE em caso de bloqueio de acesso, após confirmação de resolução de condição que originou o bloqueio.',
        '11.5. Se a opção escolhida foi o débito automático em conta ou o débito em cartão, CONTRATANTE desde já está CIENTE que o presente contrato autoriza a MASTERSAT a debitar em sua CONTA BANCÁRIA a partir desta data, por tempo indeterminado, enquanto o contrato estiver em vigor, as mensalidades firmadas em contrato.',
        '11.6. O CONTRATANTE compromete-se a manter saldo suficiente para o referido débito, ficando o BANCO isento de qualquer responsabilidade decorrente da não liquidação do compromisso por insuficiência de provisão na data de vencimento. Em caso de dúvidas, ou informações adicionais sobre vencimentos e valores, o CONTRATANTE deve solicitar esclarecimentos diretamente à MASTERSAT.',
        '11.7. Caso a opção escolhida para pagamento seja Boleto Bancário, o CONTRATANTE fica obrigado a solicitar o documento à MASTERSAT caso este não tenha sido enviado ao mesmo ou tenha apresentado falha no recebimento. Não será isentada a cobrança de juros/multa em atrasos no pagamento decorrentes desta situação.',
        '11.8. O presente Contrato, assim como se encontra redigido, revoga e substitui todo e qualquer contrato ou ajuste anterior, escrito ou verbal, tácito ou expresso, existente entre as partes. Qualquer alteração deste contrato somente terá validade se observar a forma escrita.',
        '11.9. Fica vedado a qualquer das Partes ceder ou transferir, no todo ou em parte, o presente Contrato sem a expressa anuência da outra Parte.',
        '11.10. Ressalvadas as condições expressas no contrato, a MASTERSAT poderá introduzir modificações, aditivos e anexos a este Contrato, mediante registro no Cartório de Títulos e Documentos e comunicação ao CONTRATANTE, por meio de mensagem nas correspondências (ou correio eletrônico) a ele encaminhadas. O não exercício do direito de denunciar a adesão, no prazo de 15 (quinze) dias úteis a partir da comunicação ou divulgação, ou em alternativa a prestação dos Serviços por solicitação do CONTRATANTE implica, de pleno direito, na aceitação e adesão irrestrita do CONTRATANTE às novas condições contratuais a estabelecer.',
        '11.11. Em havendo qualquer impasse existente entre as partes, o conflito será resolvido no Foro da Comarca de Joinville/SC, com a exclusão de qualquer outro, por mais privilegiado que seja.',
    ]),
    ('12. TÍTULO EXECUTIVO', [
        '12.1. O presente Contrato tem a qualidade de título executivo extrajudicial, nos termos do artigo 784, III do Código Processual Civil.',
    ]),
    ('13. ENTRADA EM VIGOR', [
        '13.1. O presente Contrato entra em vigor na data da assinatura do CONTRATANTE e após confirmação da sua aceitação pela MASTERSAT.',
    ]),
]


def _data_extenso(d: date) -> str:
    return f'JOINVILLE {d.day:02d} DE {_MESES[d.month - 1]} DE {d.year}'


def _fmt_moeda(value) -> str:
    if value is None:
        return ''
    return f'R$ {float(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _styles():
    ss = getSampleStyleSheet()
    base = ss['Normal']
    return {
        'titulo': ParagraphStyle('titulo', parent=base, fontName='Helvetica-Bold', fontSize=13, alignment=TA_CENTER, spaceAfter=6),
        'secao': ParagraphStyle('secao', parent=base, fontName='Helvetica-Bold', fontSize=9, textColor=colors.white, alignment=TA_CENTER),
        'label': ParagraphStyle('label', parent=base, fontName='Helvetica-Bold', fontSize=7.5),
        'valor': ParagraphStyle('valor', parent=base, fontName='Helvetica', fontSize=8),
        'clausula_h': ParagraphStyle('clausula_h', parent=base, fontName='Helvetica-Bold', fontSize=9, spaceBefore=8, spaceAfter=2),
        'clausula': ParagraphStyle('clausula', parent=base, fontName='Helvetica', fontSize=7.6, alignment=TA_JUSTIFY, leading=9.6, spaceAfter=2),
        'assinatura': ParagraphStyle('assinatura', parent=base, fontName='Helvetica', fontSize=8, alignment=TA_CENTER),
        'small': ParagraphStyle('small', parent=base, fontName='Helvetica', fontSize=7),
    }


def _barra_secao(texto: str, st, largura: float) -> Table:
    """Barra de cabeçalho de seção (fundo escuro, texto branco)."""
    t = Table([[Paragraph(texto, st['secao'])]], colWidths=[largura])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#1f2a44')),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def _grade(linhas: list[list], st, larguras: list[float]) -> Table:
    """Tabela de campos com borda fina (label: valor)."""
    t = Table(linhas, colWidths=larguras)
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#9aa3b2')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    return t


def gerar_contrato_pdf(contract, client, plan=None, vehicle=None) -> bytes:
    """Gera o PDF do contrato (TERMO DE ADESÃO + cláusulas) para um Contract."""
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.4 * cm, rightMargin=1.4 * cm,
        topMargin=1.2 * cm, bottomMargin=1.2 * cm,
        title=f'Contrato MasterSat #{getattr(contract, "id", "")}',
    )
    W = doc.width  # largura útil

    def P(txt, style='valor'):
        return Paragraph(txt if txt not in (None, '') else '&nbsp;', st[style])

    def L(label, valor=''):
        return [Paragraph(label, st['label']), Paragraph(str(valor) if valor not in (None, '') else '', st['valor'])]

    cli_nome = getattr(client, 'name', '') or ''
    cli_doc = getattr(client, 'cpf_cnpj', '') or ''
    cli_log = getattr(client, 'address_line', '') or ''
    cli_num = getattr(client, 'address_number', '') or ''
    cli_bairro = getattr(client, 'neighborhood', '') or ''
    cli_cep = getattr(client, 'zip_code', '') or ''
    cli_cidade = getattr(client, 'city', '') or ''
    cli_uf = getattr(client, 'state', '') or ''
    cli_tel = getattr(client, 'phone', '') or ''
    cli_email = getattr(client, 'email', '') or ''
    placa = getattr(vehicle, 'plate', '') if vehicle else ''
    valor_mensal = _fmt_moeda(getattr(plan, 'price', None)) if plan else ''

    cli_rg = getattr(client, 'rg_ie', '') or ''
    _nasc = getattr(client, 'birth_date', None)
    cli_nasc = _nasc.strftime('%d/%m/%Y') if _nasc else ''
    _emergencias = getattr(client, 'emergency_contacts', None) or []

    def _emerg(i):
        try:
            e = _emergencias[i]
        except (IndexError, TypeError):
            return '', '', ''
        if isinstance(e, dict):
            return e.get('name', '') or '', e.get('phone', '') or '', e.get('mobile', '') or ''
        return getattr(e, 'name', '') or '', getattr(e, 'phone', '') or '', getattr(e, 'mobile', '') or ''

    e1n, e1t, e1c = _emerg(0)
    e2n, e2t, e2c = _emerg(1)
    taxa_inst = _fmt_moeda(getattr(contract, 'installation_fee', None))
    taxa_desinst = _fmt_moeda(getattr(contract, 'uninstall_fee', None))

    # Vigência: preenche o início quando informado; senão deixa em branco para
    # o cliente completar à mão. O prazo em meses sai da diferença início→fim.
    _ini = getattr(contract, 'start_date', None)
    _fim = getattr(contract, 'end_date', None)
    inicio_vig = _ini.strftime('%d / %m / %Y') if _ini else '____ / ____ / ________'
    if _ini and _fim:
        _meses = (_fim.year - _ini.year) * 12 + (_fim.month - _ini.month)
        prazo_vig = str(_meses) if _meses > 0 else '12'
    else:
        prazo_vig = '12'

    elems: list = []

    # ═══ TERMO DE ADESÃO ═══
    elems.append(Paragraph('TERMO DE ADESÃO', st['titulo']))

    # 1. DADOS DA CONTRATADA
    elems.append(_barra_secao('1. DADOS DA CONTRATADA', st, W))
    elems.append(_grade([
        [Paragraph(f'<b>RAZÃO SOCIAL:</b> {CONTRATADA_RAZAO}', st['valor']),
         Paragraph(f'<b>CNPJ:</b> {CONTRATADA_CNPJ}', st['valor']),
         Paragraph(f'<b>INSC. ESTADUAL:</b> {CONTRATADA_IE}', st['valor'])],
        [Paragraph(f'<b>END.:</b> {CONTRATADA_ENDERECO}', st['valor']), '', ''],
    ], st, [W * 0.5, W * 0.27, W * 0.23]))

    # 2. DADOS DO CONTRATANTE
    elems.append(Spacer(1, 4))
    elems.append(_barra_secao('2. DADOS DO CONTRATANTE', st, W))
    elems.append(_grade([
        L('NOME / RAZÃO SOCIAL:', cli_nome),
        L('CPF / CNPJ:', cli_doc),
    ], st, [W * 0.22, W * 0.78]))
    # RG e data de nascimento dividem a mesma linha (compacta o termo)
    elems.append(_grade([
        [Paragraph('RG / INSC. ESTADUAL:', st['label']), P(cli_rg),
         Paragraph('DATA DE NASCIMENTO:', st['label']), P(cli_nasc)],
    ], st, [W * 0.22, W * 0.38, W * 0.20, W * 0.20]))
    elems.append(_grade([
        [Paragraph('LOGRADOURO:', st['label']), P(f'{cli_log} {cli_num}'.strip()),
         Paragraph('BAIRRO:', st['label']), P(cli_bairro)],
    ], st, [W * 0.14, W * 0.46, W * 0.10, W * 0.30]))
    elems.append(_grade([
        [Paragraph('CEP:', st['label']), P(cli_cep), Paragraph('CIDADE:', st['label']), P(cli_cidade),
         Paragraph('UF:', st['label']), P(cli_uf)],
    ], st, [W * 0.08, W * 0.22, W * 0.10, W * 0.40, W * 0.06, W * 0.14]))
    elems.append(_grade([
        [Paragraph('TEL. FIXO:', st['label']), P(''), Paragraph('TEL. CELULAR:', st['label']), P(cli_tel),
         Paragraph('TEL. COMERCIAL:', st['label']), P('')],
    ], st, [W * 0.13, W * 0.20, W * 0.15, W * 0.18, W * 0.16, W * 0.18]))
    elems.append(_grade([L('E-MAIL:', cli_email)], st, [W * 0.12, W * 0.88]))

    # 3. CONDIÇÕES DE PAGAMENTO DA ADESÃO/INSTALAÇÃO
    elems.append(Spacer(1, 4))
    elems.append(_barra_secao('3. CONDIÇÕES DE PAGAMENTO DA ADESÃO/INSTALAÇÃO', st, W))
    elems.append(_grade([[Paragraph('FORMA DE PAGAMENTO:', st['label']),
                          Paragraph('[  ] DINHEIRO&nbsp;&nbsp;&nbsp;&nbsp;[  ] CARTÃO&nbsp;&nbsp;&nbsp;&nbsp;[  ] PIX', st['valor'])]],
                        st, [W * 0.3, W * 0.7]))

    # 4. FORMA DE PAGAMENTO DO SERVIÇO DE RASTREAMENTO
    elems.append(Spacer(1, 4))
    elems.append(_barra_secao('4. FORMA DE PAGAMENTO DO SERVIÇO DE RASTREAMENTO', st, W))
    elems.append(_grade([[Paragraph(
        '[  ] CARNÊ&nbsp;&nbsp;&nbsp;[  ] CARTÃO DE CRÉDITO&nbsp;&nbsp;&nbsp;[  ] BOLETO VIA E-MAIL&nbsp;&nbsp;&nbsp;'
        '[  ] BOLETO VIA WHATSAPP [ ______________________ ]', st['valor'])]], st, [W]))
    # Data manual no MESMO campo do rótulo; prazo padrão 12 meses; renovação e
    # "sem fidelidade" como caixinhas para marcar à caneta (campo de meses saiu)
    elems.append(_grade([
        [Paragraph(f'<b>INÍCIO VIGÊNCIA CONTRATO:</b> {inicio_vig}', st['valor']),
         Paragraph(f'<b>PRAZO (MESES):</b> {prazo_vig}', st['valor']),
         Paragraph('[  ] RENOVAÇÃO ANUAL AUTOMÁTICA', st['label']),
         Paragraph('[  ] SEM FIDELIDADE', st['label'])],
    ], st, [W * 0.34, W * 0.15, W * 0.29, W * 0.22]))
    elems.append(_grade([
        [Paragraph('DIA VENCIMENTO:', st['label']), P(str(getattr(contract, 'billing_day', '') or '')),
         Paragraph('VALOR RASTREAMENTO MENSAL POR VEÍCULO:', st['label']), P(valor_mensal)],
    ], st, [W * 0.16, W * 0.12, W * 0.42, W * 0.30]))
    elems.append(_grade([
        [Paragraph('TAXA INSTALAÇÃO POR VEÍCULO:', st['label']), P(taxa_inst),
         Paragraph('TAXA DESINSTALAÇÃO POR VEÍCULO:', st['label']), P(taxa_desinst)],
    ], st, [W * 0.28, W * 0.20, W * 0.30, W * 0.22]))

    # 5. EMERGÊNCIA
    elems.append(Spacer(1, 4))
    elems.append(_barra_secao('5. EM CASO DE EMERGÊNCIA AVISAR - PESSOAS AUTORIZADAS', st, W))
    elems.append(_grade([
        [Paragraph('CONTATO:', st['label']), P(e1n), Paragraph('CELULAR:', st['label']), P(e1c or e1t)],
        [Paragraph('CONTATO:', st['label']), P(e2n), Paragraph('CELULAR:', st['label']), P(e2c or e2t)],
    ], st, [W * 0.10, W * 0.52, W * 0.10, W * 0.28]))
    # Credenciais de acesso à plataforma — DUAS linhas completas e idênticas
    # (código URL, usuário e senha), para anotar dois acessos à caneta.
    elems.append(_grade([
        [Paragraph('CÓDIGO URL:', st['label']), P(CODIGO_URL),
         Paragraph('USUÁRIO:', st['label']), P(''),
         Paragraph('SENHA:', st['label']), P('')],
        [Paragraph('CÓDIGO URL:', st['label']), P(CODIGO_URL),
         Paragraph('USUÁRIO:', st['label']), P(''),
         Paragraph('SENHA:', st['label']), P('')],
    ], st, [W * 0.12, W * 0.10, W * 0.10, W * 0.34, W * 0.08, W * 0.26]))

    # 6. PLACAS
    elems.append(Spacer(1, 4))
    elems.append(_barra_secao('6. PLACAS', st, W))
    elems.append(_grade([[P(placa or '')]], st, [W]))

    # Taxas fixas
    elems.append(Spacer(1, 4))
    elems.append(_grade([
        [Paragraph('Apoio à recuperação (Ponta resposta.)', st['label']), P(TAXA_APOIO_RECUPERACAO),
         Paragraph('Equipamento (não devolvido ou danificado)', st['label']), P(TAXA_EQUIPAMENTO)],
        [Paragraph('Deslocamento técnico', st['label']), P(TAXA_DESLOCAMENTO),
         Paragraph('No-show do contratante', st['label']), P(TAXA_NO_SHOW)],
    ], st, [W * 0.34, W * 0.16, W * 0.34, W * 0.16]))

    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        'CONFIRMO NESTE TERMO DE ADESÃO ACIMA QUE ACEITO AS CONDIÇÕES EXPOSTAS NO CONTRATO DE PRESTAÇÃO DE SERVIÇOS '
        'MASTERSAT RECEBIDOS NESTA DATA.', st['small']))
    elems.append(Spacer(1, 10))
    elems.append(Paragraph(_data_extenso(date.today()), st['valor']))
    elems.append(Spacer(1, 22))
    elems.append(Table([[Paragraph('_______________________________<br/>CONTRATANTE/COMODATÁRIO', st['assinatura']),
                         Paragraph('_______________________________<br/>CONTRATADA/COMODANTE', st['assinatura'])]],
                       colWidths=[W / 2, W / 2]))
    # Observação em destaque no FINAL da 1ª página (pedido do cliente) — 1x só
    elems.append(Spacer(1, 14))
    elems.append(_barra_secao('OBSERVAÇÃO: SOMENTE A MASTERSAT ESTÁ AUTORIZADA A DESINSTALAR O RASTREADOR.', st, W))

    # ═══ CONTRATO (cláusulas) ═══
    elems.append(PageBreak())
    elems.append(Paragraph('CONTRATO', st['titulo']))
    elems.append(Paragraph(
        f'A MASTERSAT – {CONTRATADA_RAZAO}, inscrita no CNPJ sob nº {CONTRATADA_CNPJ}, doravante denominada '
        'simplesmente "MASTERSAT", e o CLIENTE, denominado "CONTRATANTE", têm entre si justa e contratada a prestação '
        'de serviços de rastreamento de ATIVOS, que se regerá pelas seguintes cláusulas e condições:', st['clausula']))

    for titulo, paragrafos in CLAUSULAS:
        elems.append(Paragraph(titulo, st['clausula_h']))
        for par in paragrafos:
            elems.append(Paragraph(par, st['clausula']))

    # Assinaturas afastadas da cláusula 13. Os traços ficam numa linha própria
    # da tabela para SEMPRE saírem na mesma altura (a razão social da MasterSat
    # quebra em 2 linhas e desalinhava os traços quando tudo era uma célula só).
    elems.append(Spacer(1, 48))
    assinaturas = Table([
        [Paragraph('_______________________________', st['assinatura']),
         Paragraph('_______________________________', st['assinatura'])],
        [Paragraph(f'{cli_nome}<br/>CPF/CNPJ {cli_doc}', st['assinatura']),
         Paragraph(f'{CONTRATADA_RAZAO}<br/>CNPJ {CONTRATADA_CNPJ}', st['assinatura'])],
    ], colWidths=[W / 2, W / 2])
    assinaturas.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    elems.append(assinaturas)
    elems.append(Spacer(1, 30))
    elems.append(Paragraph('Testemunha', st['titulo']))
    elems.append(Spacer(1, 16))
    elems.append(Table([[Paragraph('_______________________________<br/>NOME:<br/>CPF:', st['assinatura']),
                         Paragraph('_______________________________<br/>NOME:<br/>CPF:', st['assinatura'])]],
                       colWidths=[W / 2, W / 2]))

    doc.build(elems)
    return buf.getvalue()
