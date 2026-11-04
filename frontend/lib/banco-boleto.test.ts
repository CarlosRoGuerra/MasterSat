import { describe, expect, it } from 'vitest';
import { caminhoPdfCarne, chaveCarne, cobrancaCompativelComBanco } from './banco-boleto';

describe('identidade dos carnês por banco', () => {
  it('preserva o caminho legado e distingue IDs iguais dos bancos', () => {
    expect(caminhoPdfCarne(7)).toBe('/boletos/carne/7/pdf');
    expect(caminhoPdfCarne(7, 'sicredi')).toBe('/boletos/carne/7/pdf?banco=sicredi');
    expect(caminhoPdfCarne(7, 'sicredi', true)).toBe('/boletos/carne/7/pdf?banco=sicredi&parcial=true');
    expect(chaveCarne(7)).not.toBe(chaveCarne(7, 'sicredi'));
  });

  it('preserva boletos já emitidos e libera cobranças sem registro para ambos', () => {
    const ailos = { boleto_ailos: true };
    const sicredi = { boleto_ailos: true, titulo_bancario: { canal: 'sicredi_api' } };
    expect(cobrancaCompativelComBanco(ailos, 'ailos')).toBe(true);
    expect(cobrancaCompativelComBanco(ailos, 'sicredi')).toBe(false);
    expect(cobrancaCompativelComBanco(sicredi, 'ailos')).toBe(false);
    expect(cobrancaCompativelComBanco(sicredi, 'sicredi')).toBe(true);
    expect(cobrancaCompativelComBanco({}, 'ailos')).toBe(true);
    expect(cobrancaCompativelComBanco({}, 'sicredi')).toBe(true);
  });
});
