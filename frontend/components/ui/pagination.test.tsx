import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Pagination, usePagination } from './pagination';

describe('usePagination', () => {
  it('fatia os itens de acordo com o tamanho de página', () => {
    const items = Array.from({ length: 45 }, (_, i) => i + 1);
    const { result } = renderHook(() => usePagination(items, 20));

    expect(result.current.totalPages).toBe(3);
    expect(result.current.slice).toEqual(items.slice(0, 20));
    expect(result.current.start).toBe(1);
    expect(result.current.end).toBe(20);
  });

  it('avança de página com setPage', () => {
    const items = Array.from({ length: 45 }, (_, i) => i + 1);
    const { result } = renderHook(() => usePagination(items, 20));

    act(() => result.current.setPage(2));

    expect(result.current.page).toBe(2);
    expect(result.current.slice).toEqual(items.slice(20, 40));
    expect(result.current.start).toBe(21);
    expect(result.current.end).toBe(40);
  });

  it('nunca ultrapassa o total de páginas, mesmo se setPage pedir mais', () => {
    const items = Array.from({ length: 10 }, (_, i) => i + 1);
    const { result } = renderHook(() => usePagination(items, 20));

    act(() => result.current.setPage(5));

    expect(result.current.page).toBe(1); // só existe 1 página com 10 itens / pageSize 20
  });

  it('reset() volta para a página 1', () => {
    const items = Array.from({ length: 45 }, (_, i) => i + 1);
    const { result } = renderHook(() => usePagination(items, 20));

    act(() => result.current.setPage(3));
    expect(result.current.page).toBe(3);

    act(() => result.current.reset());
    expect(result.current.page).toBe(1);
  });

  it('lista vazia não quebra: 1 página, start em 0', () => {
    const { result } = renderHook(() => usePagination([] as number[], 20));

    expect(result.current.totalPages).toBe(1);
    expect(result.current.start).toBe(0);
    expect(result.current.end).toBe(0);
  });
});

describe('Pagination (componente)', () => {
  it('não renderiza nada quando cabe tudo em 1 página pequena', () => {
    const { container } = render(
      <Pagination page={1} totalPages={1} total={5} start={1} end={5} onPage={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('mostra o resumo "início–fim de total"', () => {
    render(<Pagination page={2} totalPages={3} total={45} start={21} end={40} onPage={vi.fn()} />);
    expect(screen.getByText('21–40 de 45')).toBeInTheDocument();
  });

  it('desabilita "página anterior" na primeira página e "próxima" na última', () => {
    render(<Pagination page={1} totalPages={3} total={45} start={1} end={20} onPage={vi.fn()} />);
    expect(screen.getByLabelText('Página anterior')).toBeDisabled();
    expect(screen.getByLabelText('Próxima página')).not.toBeDisabled();
  });

  it('chama onPage com o número clicado', async () => {
    const user = userEvent.setup();
    const onPage = vi.fn();
    render(<Pagination page={1} totalPages={3} total={45} start={1} end={20} onPage={onPage} />);

    await user.click(screen.getByText('2'));

    expect(onPage).toHaveBeenCalledWith(2);
  });

  it('usa reticências quando há muitas páginas', () => {
    render(<Pagination page={5} totalPages={10} total={200} start={81} end={100} onPage={vi.fn()} />);
    expect(screen.getAllByText('…').length).toBeGreaterThan(0);
  });
});
