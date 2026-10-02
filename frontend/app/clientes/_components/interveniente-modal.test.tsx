import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { IntervenienteModal } from './interveniente-modal';

describe('IntervenienteModal', () => {
  it('mostra quem paga os contratos do titular mesmo sem vínculos na direção inversa', () => {
    render(
      <IntervenienteModal
        open
        clientId={1}
        clientName="FORTMAIS"
        loading={false}
        ownedContracts={[{
          id: 10,
          client_id: 1,
          vehicle_plate: 'ITJ9361',
          interveniente_client_id: 2,
          interveniente_name: 'ACQUAFORT',
          status: 'ativo',
        }]}
        responsibleContracts={[]}
        searchClients={vi.fn().mockResolvedValue([])}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('ITJ9361')).toBeInTheDocument();
    expect(screen.getByText('ACQUAFORT')).toBeInTheDocument();
    expect(screen.getByText(/Nenhum contrato de outro cliente/)).toBeInTheDocument();
  });

  it('permite definir interveniente apenas no contrato selecionado', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const searchClients = vi.fn().mockResolvedValue([
      { id: 2, name: 'ACQUAFORT COMERCIO', cpf_cnpj: '12345678000190' },
    ]);
    render(
      <IntervenienteModal
        open
        clientId={1}
        clientName="FORTMAIS"
        loading={false}
        ownedContracts={[
          { id: 10, client_id: 1, vehicle_plate: 'ITJ9361', status: 'ativo' },
          { id: 11, client_id: 1, vehicle_plate: 'ASV9F47', status: 'ativo' },
        ]}
        responsibleContracts={[]}
        searchClients={searchClients}
        onSave={onSave}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getAllByRole('button', { name: 'Alterar' })[0]);
    await user.type(screen.getByPlaceholderText(/Buscar por nome, nome fantasia/), 'ACQUAFORT');
    await user.click(await screen.findByRole('button', { name: /ACQUAFORT COMERCIO/ }));
    await user.click(screen.getByRole('button', { name: 'Salvar interveniente' }));

    expect(onSave).toHaveBeenCalledWith(10, 2);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
