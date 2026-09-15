import { describe, expect, it } from 'vitest';
import { ASSISTANT_ACTIONS, buildActionHref, filterActionsByRole } from './assistant-actions';

describe('filterActionsByRole', () => {
  it('mantém só ações cuja lista de roles inclui a role do usuário', () => {
    const financeiro = filterActionsByRole(ASSISTANT_ACTIONS, 'financeiro');
    expect(financeiro.some((a) => a.id === 'novo-cliente')).toBe(false); // admin/operacional só
    expect(financeiro.some((a) => a.id === 'abrir-financeiro')).toBe(true);
  });

  it('nunca é mais permissivo que o backend (roles espelham require_roles)', () => {
    const veiculo = ASSISTANT_ACTIONS.find((a) => a.id === 'novo-veiculo')!;
    expect(veiculo.roles.sort()).toEqual(['admin', 'operacional'].sort());
  });

  it('sem role definida (usuário ainda não carregado), retorna tudo — quem barra de verdade é o backend', () => {
    expect(filterActionsByRole(ASSISTANT_ACTIONS, undefined)).toHaveLength(ASSISTANT_ACTIONS.length);
  });
});

describe('buildActionHref', () => {
  it('ação de navegação pura só usa a rota, sem querystring', () => {
    const abrirFinanceiro = ASSISTANT_ACTIONS.find((a) => a.id === 'abrir-financeiro')!;
    expect(buildActionHref(abrirFinanceiro, { pathname: '/dashboard' })).toBe('/financeiro');
  });

  it('ação "criar" sem contexto usa só assistantAction=new', () => {
    const novoCliente = ASSISTANT_ACTIONS.find((a) => a.id === 'novo-cliente')!;
    expect(buildActionHref(novoCliente, { pathname: '/dashboard' })).toBe('/clientes?assistantAction=new');
  });

  it('ação "criar" com contexto de cliente focado inclui prefillClientId', () => {
    const novoVeiculo = ASSISTANT_ACTIONS.find((a) => a.id === 'novo-veiculo')!;
    expect(buildActionHref(novoVeiculo, { pathname: '/clientes', focusClientId: 42 })).toBe(
      '/veiculos?assistantAction=new&prefillClientId=42',
    );
  });

  it('sem contexto de cliente focado, não inclui prefillClientId', () => {
    const novoVeiculo = ASSISTANT_ACTIONS.find((a) => a.id === 'novo-veiculo')!;
    expect(buildActionHref(novoVeiculo, { pathname: '/dashboard' })).toBe('/veiculos?assistantAction=new');
  });
});
