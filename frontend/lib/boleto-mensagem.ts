// Envio de boleto ao cliente (WhatsApp/e-mail) com template das Configurações.
// Compartilhado entre a tela de Clientes e o menu do Financeiro.
import { apiFetch } from '@/lib/api';

export type CobrancaEnvio = {
  id: number;
  amount: number;
  due_date: string;
  period_label?: string | null;
};

export type ClienteEnvio = {
  name?: string | null;
  phone?: string | null;
  email?: string | null;
};

type BoletoInfo = { linha_digitavel: string; public_pdf_url?: string; boleto_registrado?: boolean };
type Templates = { msg_boleto: string; msg_boleto_assunto: string };

export const AVISO_NAO_REGISTRADO =
  'Este boleto ainda NÃO foi registrado na Ailos e não pode ser pago no banco.\n\n' +
  'Gere o boleto (Ailos) no Financeiro antes de enviar ao cliente.';

let _templates: Templates | null = null;

async function _getTemplates(token: string): Promise<Templates> {
  if (_templates) return _templates;
  _templates = await apiFetch<Templates>('/settings/mensagens', {}, token);
  return _templates;
}

export function renderTemplate(tpl: string, vars: Record<string, string>) {
  return tpl.replace(/\{(\w+)\}/g, (_m, k: string) => vars[k] ?? '');
}

function _vars(info: BoletoInfo, b: CobrancaEnvio, cliente: ClienteEnvio): Record<string, string> {
  const venc = new Date(b.due_date + 'T12:00:00').toLocaleDateString('pt-BR');
  return {
    NOME: (cliente.name || '').toUpperCase(),
    VALOR: b.amount.toLocaleString('pt-BR', { minimumFractionDigits: 2 }),
    VENCIMENTO: venc,
    REFERENTE: b.period_label || venc.slice(3),
    CODIGO_BARRAS: (info.linha_digitavel || '').replace(/\D/g, ''),
    LINK_BOLETO: info.public_pdf_url || '',
  };
}

async function _prepara(b: CobrancaEnvio, cliente: ClienteEnvio, token: string) {
  const info = await apiFetch<BoletoInfo>(`/boletos/${b.id}`, {}, token);
  if (info.boleto_registrado === false) throw new Error(AVISO_NAO_REGISTRADO);
  const tpl = await _getTemplates(token);
  return { info, tpl, vars: _vars(info, b, cliente) };
}

/** Abre o WhatsApp (wa.me) com a mensagem do template preenchida. */
export async function enviarBoletoWhats(b: CobrancaEnvio, cliente: ClienteEnvio, token: string) {
  const fone = (cliente.phone || '').replace(/\D/g, '');
  if (!fone) throw new Error('Cliente sem telefone cadastrado.');
  const { tpl, vars } = await _prepara(b, cliente, token);
  window.open(`https://wa.me/55${fone}?text=${encodeURIComponent(renderTemplate(tpl.msg_boleto, vars))}`, '_blank');
}

/** Abre o cliente de e-mail (mailto) com assunto e corpo preenchidos. */
export async function enviarBoletoEmail(b: CobrancaEnvio, cliente: ClienteEnvio, token: string) {
  if (!cliente.email) throw new Error('Cliente sem e-mail cadastrado.');
  const { tpl, vars } = await _prepara(b, cliente, token);
  const assunto = encodeURIComponent(renderTemplate(tpl.msg_boleto_assunto, vars));
  const corpo = encodeURIComponent(renderTemplate(tpl.msg_boleto, vars));
  window.location.href = `mailto:${cliente.email}?subject=${assunto}&body=${corpo}`;
}
