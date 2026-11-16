import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api';
import { iniciarAutorizacaoPix, ClientPixResultadoModal } from './client-pix-cadastro';
import { ClientFormPixBlock, deveSolicitarPix, PIX_OPCOES_INICIAIS, type PixCadastroContexto, type PixCadastroOpcoes } from './client-form-pix-block';
import { ClientFormModal } from './client-form-modal';
import { initialForm } from './helpers';
import type { ClientFormState } from './types';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

const ligado: PixCadastroContexto = { conexao: { configurado: true, escrita_habilitada: true, smtp_configurado: true },
  recorrencia: null, sugestaoServidor: null };
const opcoes = { data_inicial: '2099-12-20', enviar_convite: true, solicitar: true };

// Corpo em bloco: devolver o mock faria o Vitest chamá-lo como limpeza do teste.
beforeEach(() => { vi.mocked(apiFetch).mockReset(); });
afterEach(() => vi.useRealTimers());

describe('continuação do cadastro com Pix Automático', () => {
  it('gera a autorização só depois de salvo e não anuncia pagamento', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ recorrencia: { status: 'CRIADA', ativa: true, pix_copia_cola: 'QR',
      convite_status: 'enviado', convite_destino: 'maria@test.local' } });
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: true, contexto: ligado, opcoes });
    expect(r.tipo).toBe('gerada');
    expect(r.titulo).toBe('Cadastro salvo — autorização Pix gerada.');
    expect(r.mensagem).toMatch(/ainda não é uma autorização/);
    expect(r.mensagem).toMatch(/maria@test.local/);
    const [path, init] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe('/sicredi-pix/clientes/7/autorizacao');
    expect(JSON.parse(String(init?.body))).toEqual({ data_inicial: '2099-12-20', enviar_convite: true });
  });

  it('falha bancária mantém o cadastro e informa pendência sem repetir', async () => {
    vi.mocked(apiFetch).mockRejectedValue(new Error('Falha de comunicação com a API Pix Sicredi.'));
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: true, contexto: ligado, opcoes });
    expect(r).toMatchObject({ tipo: 'pendente', titulo: 'Cadastro salvo — autorização Pix pendente.' });
    expect(r.mensagem).toMatch(/cadastro continua salvo/);
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('resultado desconhecido fica pendente para consulta automática', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ recorrencia: { status: 'DESFECHO_DESCONHECIDO', ativa: true, pix_copia_cola: null } });
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: true, contexto: ligado, opcoes });
    expect(r.tipo).toBe('pendente');
    expect(r.mensagem).toMatch(/não cria outra autorização/);
  });

  it('integração desligada não chama o banco e diz que não iniciou', async () => {
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: true, opcoes,
      contexto: { ...ligado, conexao: { configurado: true, escrita_habilitada: false } } });
    expect(r.tipo).toBe('pendente');
    expect(r.mensagem).toMatch(/não foi iniciada/);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('operacional salva e a autorização fica com o Financeiro', async () => {
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: false, contexto: null, opcoes });
    expect(r.tipo).toBe('pendente');
    expect(r.mensagem).toMatch(/Financeiro/);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('cliente já autorizado não recria a autorização', async () => {
    const r = await iniciarAutorizacaoPix({ clientId: 7, token: 't', podeGerar: true, opcoes,
      contexto: { ...ligado, recorrencia: { status: 'APROVADA', ativa: true, data_inicial: '2026-11-20' } } });
    expect(r.tipo).toBe('existente');
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('modal de continuação mostra o título e o QR do componente Pix sem nova navegação', async () => {
    vi.mocked(apiFetch).mockResolvedValue({ conexao: { configurado: true, escrita_habilitada: true, ambiente: 'homologacao' },
      recorrencia: { id: 1, status: 'CRIADA', ativa: true, data_inicial: '2099-12-20', pix_copia_cola: 'QR-OFICIAL',
        qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', convite_status: 'enviado', convite_destino: 'maria@test.local',
        convite_tentativas: 1 }, cobrancas: [], pendentes_adesao: 0, smtp_configurado: true, emails_responsavel: ['maria@test.local'] });
    render(<ClientPixResultadoModal token="t" canFinance onClose={vi.fn()}
      client={{ id: 7, name: 'Maria', billing_day: 20, phone: '47999990000' }}
      resultado={{ tipo: 'gerada', titulo: 'Cadastro salvo — autorização Pix gerada.', mensagem: 'ok' }} />);
    expect(screen.getByText('Cadastro salvo — autorização Pix gerada.')).toBeInTheDocument();
    expect(await screen.findByLabelText('Código de autorização')).toHaveValue('QR-OFICIAL');
    expect(screen.getByText(/Enviado por e-mail/)).toBeInTheDocument();
    const whats = screen.getByRole('link', { name: /WhatsApp \(envio manual\)/ });
    expect(whats.getAttribute('href')).toMatch(/^https:\/\/wa\.me\/5547999990000\?text=/);
    expect(decodeURIComponent(whats.getAttribute('href')!)).toContain('QR-OFICIAL');
  });
});

describe('quadro Pix no formulário', () => {
  function Bloco({ canFinance = true, novaSelecao = true, contexto = ligado, email = 'maria@test.local' }:
    { canFinance?: boolean; novaSelecao?: boolean; contexto?: PixCadastroContexto | null; email?: string }) {
    const [op, setOp] = useState<PixCadastroOpcoes>(PIX_OPCOES_INICIAIS);
    return <ClientFormPixBlock token={null} clientId={null} novaSelecao={novaSelecao} canFinance={canFinance}
      nome="Maria" email={email} billingDay="20" sugestao="2099-12-20" opcoes={op} onOpcoesChange={setOp}
      contexto={contexto} onContextoChange={vi.fn()} />;
  }

  it('mostra vencimento, início sugerido, responsável, canal e explicação', () => {
    render(<Bloco />);
    expect(screen.getByText('Pix Automático — Sicredi')).toBeInTheDocument();
    expect(screen.getByText('O cliente precisará autorizar a cobrança no aplicativo do banco.')).toBeInTheDocument();
    expect(screen.getByText('Dia 20')).toBeInTheDocument();
    expect(screen.getByText('Maria')).toBeInTheDocument();
    expect(screen.getByLabelText('Início sugerido da autorização')).toHaveValue('2099-12-20');
    expect(screen.getByLabelText('Envio da autorização ao cliente')).toHaveValue('email');
    expect(screen.getByLabelText('Solicitar a autorização ao Sicredi ao salvar')).toBeChecked();
  });

  it('sem e-mail cadastrado não oferece envio por e-mail', () => {
    render(<Bloco email="" />);
    expect(screen.getByLabelText('Envio da autorização ao cliente')).toHaveValue('manual');
  });

  it('operacional vê que a autorização é do Financeiro e não escolhe datas', () => {
    render(<Bloco canFinance={false} contexto={null} />);
    expect(screen.getByText(/gerada por um usuário do Financeiro/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Início sugerido da autorização')).not.toBeInTheDocument();
  });

  it('integração desligada é informada antes de salvar', () => {
    render(<Bloco contexto={{ ...ligado, conexao: { configurado: true, escrita_habilitada: false } }} />);
    expect(screen.getByText(/autorização automática não será iniciada/)).toBeInTheDocument();
  });

  it('cliente que já era Pix com autorização encerrada não pede outra sem marcar', () => {
    const recusada = { ...ligado, recorrencia: { status: 'REJEITADA', ativa: false, data_inicial: '2026-11-20' } };
    expect(deveSolicitarPix(PIX_OPCOES_INICIAIS, false, recusada)).toBe(false);
    expect(deveSolicitarPix(PIX_OPCOES_INICIAIS, true, recusada)).toBe(true);
    // Já era Pix e nunca teve autorização: edição comum não pede ao banco sem marcar.
    expect(deveSolicitarPix(PIX_OPCOES_INICIAIS, false, ligado)).toBe(false);
    expect(deveSolicitarPix({ ...PIX_OPCOES_INICIAIS, solicitar: true }, false, ligado)).toBe(true);
    // Situação ainda não carregada (ou com erro): nunca pede, mesmo marcado.
    expect(deveSolicitarPix({ ...PIX_OPCOES_INICIAIS, solicitar: true }, false, null)).toBe(false);
    expect(deveSolicitarPix({ ...PIX_OPCOES_INICIAIS, solicitar: true }, false, { ...ligado, erro: 'falhou' })).toBe(false);
    render(<Bloco novaSelecao={false} contexto={recusada} />);
    expect(screen.getByText('Recusada')).toBeInTheDocument();
    const box = screen.getByLabelText('Solicitar uma nova autorização ao Sicredi ao salvar');
    expect(box).not.toBeChecked();
    expect(screen.queryByLabelText('Início sugerido da autorização')).not.toBeInTheDocument();
    fireEvent.click(box);
    expect(screen.getByLabelText('Início sugerido da autorização')).toBeInTheDocument();
  });

  it('edição de cliente Pix com situação ainda carregando não deixa marcar o pedido', () => {
    render(<Bloco novaSelecao={false} contexto={null} />);
    const box = screen.getByLabelText('Solicitar a autorização ao Sicredi ao salvar');
    expect(box).not.toBeChecked();
    expect(box).toBeDisabled();
  });

  it('autorização ativa: salvar não cria outra', () => {
    render(<Bloco novaSelecao={false} contexto={{ ...ligado, recorrencia: { status: 'APROVADA', ativa: true, data_inicial: '2026-11-20' } }} />);
    expect(screen.getByText(/Salvar o cadastro não cria outra/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('o quadro só aparece no formulário quando a forma é Pix Automático', async () => {
    function Form() {
      const [form, setForm] = useState<ClientFormState>({ ...initialForm, forma_cobranca: 'boleto_mensal' });
      return <ClientFormModal open isEditing={false} error="" saving={false} canEdit form={form}
        onFieldChange={(field, value) => setForm((p) => ({ ...p, [field]: value }))} onFormPatch={setForm}
        onAddContact={vi.fn()} onRemoveContact={vi.fn()} onUpdateContact={vi.fn()} onZipBlur={vi.fn()}
        onBuscarCep={vi.fn()} lookingUpCep={false} docCategory="outro" onDocCategoryChange={vi.fn()}
        onDocFilesChange={vi.fn()} onClose={vi.fn()} onCancel={vi.fn()} onSubmit={vi.fn()}
        pixBlock={<div>QUADRO PIX</div>} />;
    }
    render(<Form />);
    expect(screen.queryByText('QUADRO PIX')).not.toBeInTheDocument();
    expect(screen.getByText('Usada nos filtros do relatório de cobranças e do fechamento.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Forma de cobrança'), { target: { value: 'pix_automatico' } });
    await waitFor(() => expect(screen.getByText('QUADRO PIX')).toBeInTheDocument());
    expect(screen.getByText(/Ao salvar, a autorização é solicitada ao Sicredi/)).toBeInTheDocument();
  });
});
