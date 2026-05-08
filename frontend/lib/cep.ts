import { onlyDigits } from '@/lib/format';
import { API_URL } from '@/lib/api';

export type CepAddress = {
  zip_code: string;
  address_line: string;
  neighborhood: string;
  city: string;
  state: string;
  address_complement: string;
};

export async function fetchAddressByCep(rawCep: string): Promise<CepAddress | null> {
  const cep = onlyDigits(rawCep);
  if (cep.length !== 8) return null;

  const base = API_URL.replace(/\/+$/, '');
  const response = await fetch(`${base}/utils/cep/${cep}`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });

  if (!response.ok) {
    let message = 'Não foi possível consultar o CEP no momento.';
    try {
      const data = await response.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {}
    throw new Error(message);
  }

  return response.json();
}
