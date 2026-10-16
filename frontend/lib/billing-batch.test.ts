import { describe, expect, it } from 'vitest';
import { canSelectBilling, toggleVisibleBillings } from './billing-batch';

const page = [{ id: 1, status: 'pendente' }, { id: 2, status: 'cancelada' }, { id: 3, status: 'paga' }];
describe('seleção de boletos por filtro ou página', () => {
  it('inclui canceladas para exclusão e protege pagas', () => {
    expect(page.map(canSelectBilling)).toEqual([true, true, false]);
    expect(toggleVisibleBillings([], page, true)).toEqual([1, 2]);
  });
  it('preserva a seleção fora do filtro ou da página e não duplica IDs', () => {
    expect(toggleVisibleBillings([99, 1], page, true)).toEqual([99, 1, 2]);
    expect(toggleVisibleBillings([99, 1, 2], page, false)).toEqual([99]);
  });
});
