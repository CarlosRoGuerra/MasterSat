import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { apiFetch } from '@/lib/api';
import { ChangeClientModal } from './change-client-modal';

vi.mock('@/lib/api', async () => ({
  ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: vi.fn(),
}));

const mockApiFetch = vi.mocked(apiFetch);
const vehicle = { id: 7, client_id: 1, plate: 'OXD0A94' };
const clients = [
  { id: 1, name: 'Cleovir', cpf_cnpj: '11111111111' },
  { id: 2, name: 'Ruberval', cpf_cnpj: '22222222222' },
  { id: 3, name: 'Pagador', cpf_cnpj: '33333333333' },
];

function props() {
  return { open: true, vehicle, clients, token: 'local-token', canChangeOwner: true, onSaved: vi.fn().mockResolvedValue(undefined), onClose: vi.fn() };
}

beforeEach(() => mockApiFetch.mockReset());

describe('Trocar cliente / pagador', () => {
  it('transfere o cliente pelo sistema com o novo cliente como pagador', async () => {
    const options = props();
    const saved = { ...vehicle, client_id: 2 };
    mockApiFetch.mockResolvedValueOnce(saved);
    const user = userEvent.setup();
    render(<ChangeClientModal {...options} />);
    const owner = screen.getByRole('group', { name: 'Cliente do veículo' });
    await user.click(within(owner).getByRole('button', { name: 'Remover seleção' }));
    await user.type(within(owner).getByPlaceholderText('Selecionar cliente'), 'Ruberval');
    await user.click(within(owner).getByRole('button', { name: /Ruberval/ }));
    await user.click(screen.getByRole('button', { name: 'Confirmar troca' }));
    await waitFor(() => expect(options.onClose).toHaveBeenCalledOnce());
    expect(mockApiFetch).toHaveBeenCalledOnce();
    expect(mockApiFetch.mock.calls[0][0]).toBe('/vehicles/7/change-client');
    expect(JSON.parse(String(mockApiFetch.mock.calls[0][1]?.body))).toEqual({ client_id: 2, interveniente_client_id: null, expected_client_id: 1 });
    expect(options.onSaved).toHaveBeenCalledWith(saved);
  });

  it('permite ao financeiro trocar somente o pagador', async () => {
    const options = { ...props(), canChangeOwner: false };
    mockApiFetch.mockResolvedValueOnce(vehicle);
    const user = userEvent.setup();
    render(<ChangeClientModal {...options} />);
    const owner = screen.getByRole('group', { name: 'Cliente do veículo' });
    expect(within(owner).queryByRole('button', { name: 'Remover seleção' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'Outro cliente como interveniente (pagador)' }));
    expect(screen.getByRole('button', { name: 'Confirmar troca' })).toBeDisabled();
    await user.type(screen.getByPlaceholderText('Selecionar pagador'), 'Pagador');
    await user.click(screen.getByRole('button', { name: /Pagador.*333/ }));
    await user.click(screen.getByRole('button', { name: 'Confirmar troca' }));
    await waitFor(() => expect(options.onClose).toHaveBeenCalledOnce());
    expect(JSON.parse(String(mockApiFetch.mock.calls[0][1]?.body))).toEqual({ client_id: 1, interveniente_client_id: 3, expected_client_id: 1 });
  });

  it('preserva a seleção e mostra o erro quando o backend recusa a troca', async () => {
    const options = props();
    mockApiFetch.mockRejectedValueOnce(new Error('O cliente do veículo mudou. Atualize a tela.'));
    const user = userEvent.setup();
    render(<ChangeClientModal {...options} />);
    await user.click(screen.getByRole('button', { name: 'Confirmar troca' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('O cliente do veículo mudou');
    expect(options.onClose).not.toHaveBeenCalled();
    expect(options.onSaved).not.toHaveBeenCalled();
    expect(screen.getByText('Cleovir')).toBeInTheDocument();
  });

  it('exige escolher o pagador quando há contratos com pagadores diferentes', async () => {
    const user = userEvent.setup();
    render(<ChangeClientModal {...props()} initialIntervenienteId="mixed" />);
    expect(screen.getByRole('button', { name: 'Confirmar troca' })).toBeDisabled();
    await user.click(screen.getByRole('radio', { name: 'O próprio cliente' }));
    expect(screen.getByRole('button', { name: 'Confirmar troca' })).toBeEnabled();
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it('carrega o interveniente atual sem consultar outra API', () => {
    render(<ChangeClientModal {...props()} initialIntervenienteId={3} />);
    expect(screen.getByRole('radio', { name: 'Outro cliente como interveniente (pagador)' })).toBeChecked();
    expect(screen.getByText('Pagador')).toBeInTheDocument();
    expect(mockApiFetch).not.toHaveBeenCalled();
  });
});
