import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ClientFormModal } from './client-form-modal';
import { initialForm } from './helpers';
import type { ClientFormState } from './types';

it('seleciona Pix Automático na mesma forma de cobrança e mostra agrupamento por ciclo', () => {
  const submit = vi.fn();
  function Form() {
    const [form, setForm] = useState<ClientFormState>({ ...initialForm, boleto_format: 'individual', boleto_fee: 'sim' });
    return <ClientFormModal open isEditing={false} error="" saving={false} canEdit form={form}
      onFieldChange={(field, value) => setForm((previous) => ({ ...previous, [field]: value }))} onFormPatch={setForm}
      onAddContact={vi.fn()} onRemoveContact={vi.fn()} onUpdateContact={vi.fn()} onZipBlur={vi.fn()}
      onBuscarCep={vi.fn()} lookingUpCep={false} docCategory="outro" onDocCategoryChange={vi.fn()}
      onDocFilesChange={vi.fn()} onClose={vi.fn()} onCancel={vi.fn()} onSubmit={submit} />;
  }
  render(<Form />);
  const select = screen.getByLabelText('Forma de cobrança');
  expect(screen.getByRole('option', { name: 'Cartão de crédito' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Boleto mensal' })).toBeInTheDocument();
  fireEvent.change(select, { target: { value: 'pix_automatico' } });
  expect(select).toHaveValue('pix_automatico');
  expect(screen.getByLabelText(/Agrupamento da cobrança/)).toBeDisabled();
  expect(screen.getByLabelText(/Agrupamento da cobrança/)).toHaveValue('unico');
  expect(screen.getByLabelText('Taxa de Emissão do Boleto')).toHaveValue('nao');
  expect(submit).not.toHaveBeenCalled();
});
