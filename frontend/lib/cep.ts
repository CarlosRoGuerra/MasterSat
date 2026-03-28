import { onlyDigits } from '@/lib/format';

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

  const response = await fetch(`https://viacep.com.br/ws/${cep}/json/`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error('Não foi possível consultar o CEP no momento.');
  }

  const data = await response.json();
  if (data?.erro) {
    throw new Error('CEP não encontrado.');
  }

  return {
    zip_code: data.cep || rawCep,
    address_line: data.logradouro || '',
    neighborhood: data.bairro || '',
    city: data.localidade || '',
    state: data.uf || '',
    address_complement: data.complemento || '',
  };
}
