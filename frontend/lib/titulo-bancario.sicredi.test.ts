import { describe, expect, it } from 'vitest';

import { descreverTitulo, ehSicredi } from './titulo-bancario';

describe('título bancário do Sicredi', () => {
  it('identifica o canal Sicredi', () => {
    expect(ehSicredi({ estado: 'registrado', canal: 'sicredi_api' })).toBe(true);
    expect(ehSicredi({ estado: 'registrado', canal: 'ailos_api' })).toBe(false);
    expect(ehSicredi(null)).toBe(false);
  });

  it('mostra o banco e a baixa enviada aguardando confirmação', () => {
    const d = descreverTitulo({
      estado: 'registrado', canal: 'sicredi_api', nosso_numero: '262000017', baixa_status: 'solicitada',
    });
    expect(d.rotulo).toBe('Boleto registrado');
    expect(d.detalhe).toContain('Sicredi');
    expect(d.detalhe).toContain('nosso número 262000017');
    expect(d.detalhe).toContain('aguardando confirmação');
  });
});
