import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, apiFetchAll } from './api';
import { loadChargeLinks } from './financeiro-charge-links';

vi.mock('./api', () => ({ apiFetch: vi.fn(), apiFetchAll: vi.fn() }));

describe('vínculos do lançamento financeiro', () => {
  beforeEach(() => vi.clearAllMocks());

  it('mantém veículos e rastreadores do cliente mesmo quando os contratos falham', async () => {
    const vehicle = { id: 42, client_id: 7, plate: 'ABC1234', model: 'Strada' };
    const tracker = { id: 9, client_id: 7, vehicle_id: 42, imei: '123456789012345' };
    vi.mocked(apiFetchAll)
      .mockResolvedValueOnce([vehicle])
      .mockResolvedValueOnce([tracker]);
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('Falha nos contratos'));

    const links = await loadChargeLinks<{ id: number }>('7', 'token');

    expect(links).toEqual({
      clientId: '7', vehicles: [vehicle], trackers: [tracker], contracts: undefined, errors: ['contratos'],
    });
    expect(apiFetchAll).toHaveBeenCalledWith('/vehicles?client_id=7', 'token');
    expect(apiFetchAll).toHaveBeenCalledWith('/trackers?client_id=7', 'token');
    expect(apiFetch).toHaveBeenCalledWith('/contracts?client_id=7&limit=300', {}, 'token');
  });
});
