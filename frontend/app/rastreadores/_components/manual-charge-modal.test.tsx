import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ManualChargeModal } from './manual-charge-modal';

const api = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch: api.fetch }));

const target = { trackerId: 5, vehicleId: 7, clientId: 1, contractId: 20,
  clientName: 'Cliente de teste', vehiclePlate: 'SYX6E75', identifier: '866557080235742' };
const props = () => ({ target, token: 'test-token', onClose: vi.fn(), onCreated: vi.fn() });

beforeEach(() => { vi.resetAllMocks(); api.fetch.mockResolvedValue({ id: 101 }); });

async function fillForm(amount = '120,50') {
  const user = userEvent.setup();
  await user.type(screen.getByRole('textbox', { name: /^Descrição/ }), '  Instalação  ');
  await user.type(screen.getByRole('textbox', { name: /^Valor/ }), amount);
  fireEvent.change(screen.getByLabelText(/^Data do serviço/), { target: { value: '2026-10-29' } });
  return user;
}

describe('Cobrança manual do rastreador', () => {
  it('registra o adicional para o fechamento com os vínculos e a data do serviço', async () => {
    const handlers = props();
    render(<ManualChargeModal {...handlers} />);
    const user = await fillForm();
    await user.type(screen.getByRole('textbox', { name: 'Observações' }), '  Acertado com o cliente  ');
    await user.click(screen.getByRole('button', { name: 'Adicionar ao fechamento' }));
    expect(api.fetch).toHaveBeenCalledTimes(1);
    const [path, options, token] = api.fetch.mock.calls[0];
    expect(path).toBe('/client-charge-items/');
    expect(token).toBe('test-token');
    expect(JSON.parse(options.body)).toEqual({ client_id: 1, vehicle_id: 7, tracker_id: 5, contract_id: 20,
      title: 'Instalação', quantity: 1, unit_price: 120.50, installment_count: 1,
      start_date: '2026-10-29', description: 'Acertado com o cliente' });
    expect(handlers.onCreated).toHaveBeenCalledExactlyOnceWith(101);
  });

  it.each(['0', '-10', 'NaN', '0,001'])('recusa valor inválido %s antes de gravar', async amount => {
    render(<ManualChargeModal {...props()} />);
    const user = await fillForm(amount);
    await user.click(screen.getByRole('button', { name: 'Adicionar ao fechamento' }));
    expect(screen.getByRole('alert')).toHaveTextContent('um valor maior que zero');
    expect(api.fetch).not.toHaveBeenCalled();
  });

  it('mantém os dados para correção quando a API recusa', async () => {
    api.fetch.mockRejectedValue(new Error('O veículo selecionado não pertence ao cliente informado.'));
    const handlers = props();
    render(<ManualChargeModal {...handlers} />);
    const user = await fillForm();
    await user.click(screen.getByRole('button', { name: 'Adicionar ao fechamento' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('O veículo selecionado não pertence');
    expect(screen.getByRole('textbox', { name: /^Valor/ })).toHaveValue('120,50');
    expect(handlers.onCreated).not.toHaveBeenCalled();
  });

  it('bloqueia novo envio e fechamento enquanto aguarda o servidor', async () => {
    let finish!: (value: { id: number }) => void;
    api.fetch.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const handlers = props();
    render(<ManualChargeModal {...handlers} />);
    const user = await fillForm();
    await user.click(screen.getByRole('button', { name: 'Adicionar ao fechamento' }));
    expect(screen.getByRole('button', { name: 'Criando…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('button', { name: 'Criando…' }).closest('form')!);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Fechar' }));
    expect(handlers.onClose).not.toHaveBeenCalled();
    expect(api.fetch).toHaveBeenCalledTimes(1);
    finish({ id: 101 });
    await waitFor(() => expect(handlers.onCreated).toHaveBeenCalledWith(101));
  });

  it('cancelar fecha sem criar cobrança', async () => {
    const handlers = props();
    render(<ManualChargeModal {...handlers} />);
    await userEvent.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(handlers.onClose).toHaveBeenCalledTimes(1);
    expect(api.fetch).not.toHaveBeenCalled();
  });
});
