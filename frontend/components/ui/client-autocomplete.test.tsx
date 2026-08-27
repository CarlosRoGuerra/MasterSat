import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ClientAutocomplete } from './client-autocomplete';

const clients = [
  { id: 1, name: 'João da Silva', cpf_cnpj: '12345678901' },
  { id: 2, name: 'Maria Souza', cpf_cnpj: '98765432000199' },
];

describe('ClientAutocomplete', () => {
  it('mostra o cliente selecionado como chip, sem abrir a lista', () => {
    render(<ClientAutocomplete clients={clients} value={1} onChange={vi.fn()} />);
    expect(screen.getByText('João da Silva')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Buscar por nome/)).not.toBeInTheDocument();
  });

  it('filtra por nome e chama onChange ao selecionar', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ClientAutocomplete clients={clients} value="" onChange={onChange} />);

    const input = screen.getByPlaceholderText(/Buscar por nome/);
    await user.type(input, 'maria');

    // getByRole calcula o nome acessível através dos nós filhos — necessário
    // porque highlight() quebra o texto em <mark>Maria</mark> + " Souza".
    const option = screen.getByRole('button', { name: /Maria Souza/ });
    expect(option).toBeInTheDocument();
    expect(screen.queryByText('João da Silva')).not.toBeInTheDocument();

    await user.click(option);
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('filtra por CPF/CNPJ (ignorando pontuação)', async () => {
    const user = userEvent.setup();
    render(<ClientAutocomplete clients={clients} value="" onChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText(/Buscar por nome/), '123.456.789-01');

    expect(screen.getByText('João da Silva')).toBeInTheDocument();
  });

  it('mostra mensagem de "nenhum cliente encontrado" quando o filtro não bate com ninguém', async () => {
    const user = userEvent.setup();
    render(<ClientAutocomplete clients={clients} value="" onChange={vi.fn()} />);

    await user.type(screen.getByPlaceholderText(/Buscar por nome/), 'zzz');

    expect(screen.getByText('Nenhum cliente encontrado para "zzz"')).toBeInTheDocument();
  });

  it('limpar seleção chama onChange com string vazia', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ClientAutocomplete clients={clients} value={1} onChange={onChange} />);

    await user.click(screen.getByLabelText('Remover seleção'));
    expect(onChange).toHaveBeenCalledWith('');
  });
});
