import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Modal } from './modal';

describe('Modal', () => {
  it('não renderiza nada quando open=false', () => {
    const { container } = render(
      <Modal open={false} onClose={vi.fn()} title="Título">
        conteúdo
      </Modal>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renderiza título, descrição e conteúdo quando aberta', () => {
    render(
      <Modal open onClose={vi.fn()} title="Editar cliente" description="Atualize os dados">
        <p>corpo da modal</p>
      </Modal>,
    );
    expect(screen.getByRole('dialog', { name: 'Editar cliente' })).toBeInTheDocument();
    expect(screen.getByText('Atualize os dados')).toBeInTheDocument();
    expect(screen.getByText('corpo da modal')).toBeInTheDocument();
  });

  it('chama onClose ao clicar no botão de fechar', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Título">
        conteúdo
      </Modal>,
    );

    await user.click(screen.getByLabelText('Fechar'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('chama onClose ao pressionar Escape', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Título">
        conteúdo
      </Modal>,
    );

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('chama onClose ao clicar no backdrop, mas não ao clicar dentro do diálogo', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Título">
        <button type="button">ação interna</button>
      </Modal>,
    );

    await user.click(screen.getByText('ação interna'));
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByRole('dialog').parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('bloqueia o scroll do body enquanto aberta e restaura ao fechar', () => {
    const { rerender } = render(
      <Modal open onClose={vi.fn()} title="Título">
        conteúdo
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('hidden');

    rerender(
      <Modal open={false} onClose={vi.fn()} title="Título">
        conteúdo
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('');
  });
});
