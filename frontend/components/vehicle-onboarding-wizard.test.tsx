import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VehicleOnboardingWizard } from './vehicle-onboarding-wizard';

const api = vi.hoisted(() => ({ fetch: vi.fn(), list: vi.fn() }));
vi.mock('@/lib/api', async () => ({ ...await vi.importActual<typeof import('@/lib/api')>('@/lib/api'),
  apiFetch: api.fetch, apiFetchList: api.list }));

beforeEach(() => {
  vi.resetAllMocks();
  api.list.mockResolvedValue([{ id: 9, imei: '866557080235742', status: 'em_estoque' }]);
  api.fetch.mockImplementation(async (path: string) => {
    if (path === '/plans' || path === '/service-products') return [];
    if (path === '/vehicles') return { id: 7, plate: 'SYX6E75' };
    if (path === '/trackers/9/link-vehicle') return {};
    throw new Error(`Requisição inesperada: ${path}`);
  });
});

const props = () => ({ open: true, token: 'test-token', initialClientId: 1,
  clients: [{ id: 1, name: 'Cliente de teste', cpf_cnpj: '00000000000' }],
  onComplete: vi.fn(), onClose: vi.fn() });

async function openEquipmentStep() {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('ABC1D23'), 'SYX6E75');
  await user.click(screen.getByRole('button', { name: 'Cadastrar e avançar →' }));
  await screen.findByText('Vincular equipamento de rastreamento');
  return user;
}

describe('Local de instalação no cadastro de veículo', () => {
  it.each(['  Atrás do painel, lado do motorista  ', ''])('envia o local junto com o vínculo: %s', async location => {
    render(<VehicleOnboardingWizard {...props()} />);
    const user = await openEquipmentStep();
    const input = screen.getByRole('textbox', { name: 'Local de instalação' });
    expect(input).toHaveAttribute('maxlength', '120');
    fireEvent.change(input, { target: { value: location } });
    await user.click(screen.getByPlaceholderText('Buscar por IMEI, marca ou modelo…'));
    await user.click(await screen.findByRole('button', { name: '866557080235742' }));
    await user.click(screen.getByRole('button', { name: 'Vincular e avançar →' }));
    await screen.findByText('Selecionar plano e forma de pagamento');
    const call = api.fetch.mock.calls.find(([path]) => path === '/trackers/9/link-vehicle')!;
    expect(call[1].method).toBe('POST');
    expect(JSON.parse(call[1].body)).toMatchObject({ vehicle_id: 7, install_location: location.trim() || null });
    expect(api.fetch.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(0);
  });

  it('mantém o local informado se a vinculação falhar', async () => {
    render(<VehicleOnboardingWizard {...props()} />);
    const user = await openEquipmentStep();
    await user.type(screen.getByRole('textbox', { name: 'Local de instalação' }), 'Porta-luvas');
    await user.click(screen.getByPlaceholderText('Buscar por IMEI, marca ou modelo…'));
    await user.click(await screen.findByRole('button', { name: '866557080235742' }));
    const original = api.fetch.getMockImplementation()!;
    api.fetch.mockImplementation((path: string) => path.endsWith('/link-vehicle')
      ? Promise.reject(new Error('Equipamento indisponível')) : original(path));
    await user.click(screen.getByRole('button', { name: 'Vincular e avançar →' }));
    expect(await screen.findByText('Equipamento indisponível')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Local de instalação' })).toHaveValue('Porta-luvas');
  });

  it('limpa o local quando o assistente é fechado e reaberto', async () => {
    const handlers = props();
    const { rerender } = render(<VehicleOnboardingWizard {...handlers} />);
    const user = await openEquipmentStep();
    await user.type(screen.getByRole('textbox', { name: 'Local de instalação' }), 'Painel');
    rerender(<VehicleOnboardingWizard {...handlers} open={false} />);
    rerender(<VehicleOnboardingWizard {...handlers} />);
    await openEquipmentStep();
    expect(screen.getByRole('textbox', { name: 'Local de instalação' })).toHaveValue('');
  });
});
