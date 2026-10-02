import { keepPreviousData, useQuery } from '@tanstack/react-query';

import { apiFetch, apiFetchAll, apiFetchList, type Page } from '@/lib/api';
import type { Client, VehicleDetailed, VehicleSummary } from './types';

type ClientFilters = { search: string; status: string; type: string };
type ClientListParams = ClientFilters & {
  page: number;
  pageSize: number;
  sort: string;
  direction: 'asc' | 'desc';
};
type ClientSummary = { total: number; active: number; delinquent: number; company: number };

export const clientsKeys = {
  all: ['clients'] as const,
  list: (params: ClientListParams) => [...clientsKeys.all, 'list', params] as const,
  summary: (filters: ClientFilters) => [...clientsKeys.all, 'summary', filters] as const,
  vehicles: (clientId: number) => [...clientsKeys.all, clientId, 'vehicles'] as const,
  vehiclesDetailed: (clientId: number) => [...clientsKeys.all, clientId, 'vehicles-detailed'] as const,
};

function filterQuery(filters: ClientFilters) {
  const query = new URLSearchParams();
  if (filters.search.trim()) query.set('search', filters.search.trim());
  if (filters.status) query.set('status', filters.status);
  if (filters.type) query.set('type', filters.type);
  return query;
}

export function useClientsQuery(token: string | null, params: ClientListParams) {
  return useQuery({
    queryKey: clientsKeys.list(params),
    queryFn: () => {
      const query = filterQuery(params);
      query.set('skip', String((params.page - 1) * params.pageSize));
      query.set('limit', String(params.pageSize));
      query.set('sort', params.sort);
      query.set('direction', params.direction);
      return apiFetch<Page<Client>>(`/clients?${query.toString()}`, {}, token!);
    },
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    enabled: !!token,
  });
}

export function useClientSummaryQuery(token: string | null, filters: ClientFilters) {
  return useQuery({
    queryKey: clientsKeys.summary(filters),
    queryFn: () => apiFetch<ClientSummary>(`/clients/summary?${filterQuery(filters).toString()}`, {}, token!),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    enabled: !!token,
  });
}

export function useClientVehiclesSummaryQuery(token: string | null, clientId: number | null) {
  return useQuery({
    queryKey: clientsKeys.vehicles(clientId ?? -1),
    queryFn: () => apiFetchAll<VehicleSummary>(`/vehicles?client_id=${clientId}`, token!, 500),
    staleTime: 30_000,
    enabled: !!token && clientId != null,
  });
}

type RawVehicle = { id: number; plate: string; type?: string | null; brand?: string | null; model?: string | null; status: string };
type RawTracker = { id: number; vehicle_id?: number | null; imei: string; brand?: string | null; model?: string | null; active_plan_name?: string | null };

export function useClientVehiclesDetailedQuery(token: string | null, clientId: number | null) {
  return useQuery({
    queryKey: clientsKeys.vehiclesDetailed(clientId ?? -1),
    queryFn: async (): Promise<VehicleDetailed[]> => {
      const [vehs, trackers] = await Promise.all([
        apiFetchList<RawVehicle>(`/vehicles?client_id=${clientId}&limit=100`, {}, token!).catch(() => []),
        apiFetchList<RawTracker>(`/trackers?client_id=${clientId}&limit=100`, {}, token!).catch(() => []),
      ]);
      const trackerByVehicle = new Map(trackers.map((tracker) => [tracker.vehicle_id, tracker]));
      return vehs.map((v) => {
        const t = trackerByVehicle.get(v.id);
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
