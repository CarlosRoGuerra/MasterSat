import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FormField } from './form-field';

describe('FormField', () => {
  it('associa o label ao campo via htmlFor/id gerado automaticamente', () => {
    render(
      <FormField label="Nome">
        <input type="text" />
      </FormField>,
    );
    const input = screen.getByLabelText('Nome');
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe('INPUT');
  });

  it('marca campo obrigatório com "*" mantendo o label associado ao campo', () => {
    render(
      <FormField label="E-mail" required>
        <input type="email" />
      </FormField>,
    );
    // O texto acessível do label vira "E-mail*" (span do "*" concatenado) — busca por regex.
    expect(screen.getByLabelText(/E-mail/)).toBeInTheDocument();
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  it('não sobrescreve um id explícito já definido no filho', () => {
    render(
      <FormField label="CEP">
        <input type="text" id="cep-fixo" />
      </FormField>,
    );
    const input = screen.getByLabelText('CEP');
    expect(input).toHaveAttribute('id', 'cep-fixo');
  });

  it('respeita um id passado explicitamente ao FormField', () => {
    render(
      <FormField label="Cidade" id="cidade-custom">
        <input type="text" />
      </FormField>,
    );
    const input = screen.getByLabelText('Cidade');
    expect(input).toHaveAttribute('id', 'cidade-custom');
  });

  it('múltiplos FormField na mesma tela geram ids distintos (sem colisão)', () => {
    render(
      <>
        <FormField label="Nome">
          <input type="text" />
        </FormField>
        <FormField label="Sobrenome">
          <input type="text" />
        </FormField>
      </>,
    );
    const nome = screen.getByLabelText('Nome') as HTMLInputElement;
    const sobrenome = screen.getByLabelText('Sobrenome') as HTMLInputElement;
    expect(nome.id).not.toBe('');
    expect(sobrenome.id).not.toBe('');
    expect(nome.id).not.toBe(sobrenome.id);
  });

  it('em estado de erro, marca aria-invalid e associa aria-describedby à mensagem', () => {
    render(
      <FormField label="Dia de vencimento" error="Deve ser entre 1 e 31">
        <input type="text" />
      </FormField>,
    );
    const input = screen.getByLabelText('Dia de vencimento');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    const errorMessage = screen.getByText('Deve ser entre 1 e 31');
    expect(errorMessage).toHaveAttribute('id', describedBy);
  });

  it('sem erro, não define aria-invalid nem aria-describedby', () => {
    render(
      <FormField label="Observações">
        <input type="text" />
      </FormField>,
    );
    const input = screen.getByLabelText('Observações');
    expect(input).not.toHaveAttribute('aria-invalid');
    expect(input).not.toHaveAttribute('aria-describedby');
  });

  it('esconde a dica (hint) quando há erro, preservando o comportamento existente', () => {
    render(
      <FormField label="Telefone" hint="Formato (99) 99999-9999" error="Telefone inválido">
        <input type="text" />
      </FormField>,
    );
    expect(screen.queryByText('Formato (99) 99999-9999')).not.toBeInTheDocument();
    expect(screen.getByText('Telefone inválido')).toBeInTheDocument();
  });

  it('funciona com componentes customizados (ex.: Select) como filho', () => {
    render(
      <FormField label="Status">
        <select>
          <option value="ativo">Ativo</option>
        </select>
      </FormField>,
    );
    expect(screen.getByLabelText('Status')).toBeInTheDocument();
  });
});
