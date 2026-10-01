import { apiFetch, apiFetchAll } from './api';
import type { TrackerOption, VehicleOption } from './domain-types';

export type ChargeLinks<TContract> = {
  clientId: string;
  vehicles?: VehicleOption[];
  trackers?: TrackerOption[];
  contracts?: TContract[];
  errors: string[];
};

export async function loadChargeLinks<TContract>(clientId: string, token: string): Promise<ChargeLinks<TContract>> {
  const [vehicles, trackers, contracts] = await Promise.allSettled([
    apiFetchAll<VehicleOption>(`/vehicles?client_id=${clientId}`, token),
    apiFetchAll<TrackerOption>(`/trackers?client_id=${clientId}`, token),
    apiFetch<TContract[]>(`/contracts?client_id=${clientId}&limit=300`, {}, token),
  ]);

  return {
    clientId,
    vehicles: vehicles.status === 'fulfilled' ? vehicles.value : undefined,
    trackers: trackers.status === 'fulfilled' ? trackers.value : undefined,
    contracts: contracts.status === 'fulfilled' ? contracts.value : undefined,
    errors: [
      vehicles.status === 'rejected' ? 'veículos' : null,
      trackers.status === 'rejected' ? 'rastreadores' : null,
      contracts.status === 'rejected' ? 'contratos' : null,
    ].filter((label): label is string => label !== null),
  };
}
