import { describe, expect, it } from 'vitest';

import { descreverTitulo, podeConsultarDesfecho } from './titulo-bancario';

describe('descreverTitulo', () => {
  it('sem título', () => {
    expect(descreverTitulo(null).rotulo).toBe('Sem boleto no banco');
  });

  it('desfecho desconhecido é destacado e permite consulta', () => {
    const t = { estado: 'desfecho_desconhecido' as const };
    expect(descreverTitulo(t)).toMatchObject({ tom: 'danger' });
    expect(podeConsultarDesfecho(t)).toBe(true);
    expect(podeConsultarDesfecho({ estado: 'registrado' })).toBe(false);
  });

  it('baixa pendente e pendência aparecem no detalhe', () => {
    const d = descreverTitulo({ estado: 'registrado', nosso_numero: '123', baixa_status: 'pendente', pendencia: 'pagamento_divergente' });
    expect(d.tom).toBe('warning');
    expect(d.detalhe).toContain('nosso número 123');
    expect(d.detalhe).toContain('baixa PENDENTE');
    expect(d.detalhe).toContain('valor diferente');
  });
});
