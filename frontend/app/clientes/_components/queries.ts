import { useQuery } from '@tanstack/react-query';

import { apiFetch } from '@/lib/api';
import type { Client, VehicleDetailed, VehicleSummary } from './types';

/**
 * Chaves de query do domínio "clientes" — usadas tanto pelos hooks abaixo
 * quanto por quem precisa invalidar o cache após uma mutação (criar/editar/
 * excluir cliente, anexar contrato, etc.), em vez de rechamar a função de
 * carga manualmente.
 */
export const clientsKeys = {
  all: ['clients'] as const,
  list: (filters: { search: string; status: string; type: string }) =>
    [...clientsKeys.all, 'list', filters] as const,
  vehiclesDetailed: (clientId: number) => [...clientsKeys.all, clientId, 'vehicles-detailed'] as const,
};

export const vehicleSummariesKeys = {
  all: ['vehicles', 'summaries'] as const,
};

export function useClientsQuery(token: string | null, filters: { search: string; status: string; type: string }) {
  return useQuery({
    queryKey: clientsKeys.list(filters),
    queryFn: () => {
      const query = new URLSearchParams();
      if (filters.search.trim()) query.set('search', filters.search.trim());
      if (filters.status) query.set('status', filters.status);
      if (filters.type) query.set('type', filters.type);
      query.set('limit', '200');
      return apiFetch<Client[]>(`/clients?${query.toString()}`, {}, token!);
    },
    enabled: !!token,
  });
}

export function useVehicleSummariesQuery(token: string | null) {
  return useQuery({
    queryKey: vehicleSummariesKeys.all,
    queryFn: () => apiFetch<VehicleSummary[]>('/vehicles?limit=500', {}, token!),
    enabled: !!token,
  });
}

type RawVehicle = { id: number; plate: string; type?: string | null; brand?: string | null; model?: string | null; status: string };
type RawTracker = { id: number; vehicle_id?: number | null; imei: string; brand?: string | null; model?: string | null; active_plan_name?: string | null };

export function useClientVehiclesDetailedQuery(token: string | null, clientId: number | null) {
  return useQuery({
    queryKey: clientsKeys.vehiclesDetailed(clientId ?? -1),
    queryFn: async (): Promise<VehicleDetailed[]> => {
      const [vehs, trackers] = await Promise.all([
        apiFetch<RawVehicle[]>(`/vehicles?client_id=${clientId}&limit=100`, {}, token!).catch(() => []),
        apiFetch<RawTracker[]>(`/trackers?client_id=${clientId}&limit=100`, {}, token!).catch(() => []),
      ]);
      return vehs.map((v) => {
        const t = trackers.find((tr) => tr.vehicle_id === v.id);
        return {
          ...v,
          tracker_imei: t?.imei ?? null,
          tracker_brand: t?.brand ?? null,
          tracker_model: t?.model ?? null,
          tracker_plan: t?.active_plan_name ?? null,
        };
      });
    },
    enabled: !!token && clientId != null,
  });
}
