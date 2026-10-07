import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CarneCanalChoice } from './carne-canal-choice';

describe('Onde gerar o carnê', () => {
  it('oferece Ailos e somente no sistema, marcando a escolha atual', async () => {
    const onChange = vi.fn();
    render(<CarneCanalChoice value="ailos" onChange={onChange} />);
    expect(screen.getByRole('radio', { name: /Registrar na Ailos/ })).toHaveAttribute('aria-checked', 'true');
    const sistema = screen.getByRole('radio', { name: /Somente no sistema/ });
    expect(sistema).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(sistema);
    expect(onChange).toHaveBeenCalledWith('sistema');
  });
});
