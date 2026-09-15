import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from './confirm-dialog';

describe('ConfirmDialog', () => {
  it('não renderiza nada quando open=false', () => {
    render(
      <ConfirmDialog open={false} onClose={() => {}} onConfirm={() => {}} title="Título" description="Descrição" />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('mostra título, descrição e o rótulo de confirmação — nunca um texto genérico', () => {
    render(
      <ConfirmDialog
        open
        onClose={() => {}}
        onConfirm={() => {}}
        title="Desvincular rastreador?"
        description="Desvincular o rastreador IMEI ****1234 do veículo ABC-1234?"
        confirmLabel="Desvincular"
      />,
    );
    expect(screen.getByText('Desvincular rastreador?')).toBeInTheDocument();
    expect(screen.getByText('Desvincular o rastreador IMEI ****1234 do veículo ABC-1234?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Desvincular' })).toBeInTheDocument();
  });

  it('chama onConfirm ao clicar em confirmar e onClose ao cancelar', async () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <ConfirmDialog open onClose={onClose} onConfirm={onConfirm} title="T" description="D" confirmLabel="Confirmar" />,
    );

    await user.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Confirmar' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('desabilita os botões e mostra "Aguarde…" enquanto loading=true', () => {
    render(
      <ConfirmDialog open onClose={() => {}} onConfirm={() => {}} title="T" description="D" confirmLabel="Confirmar" loading />,
    );
    expect(screen.getByRole('button', { name: 'Aguarde…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeDisabled();
  });
});
