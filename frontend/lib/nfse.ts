export type EstadoNfse = { status: string; erro_tipo?: string | null };

export function podeEmitirNfse(nota: EstadoNfse | null): boolean {
  return nota === null || (nota.status === 'erro' && ['local', 'rejeicao'].includes(nota.erro_tipo ?? ''));
}

export function precisaConsultarNfse(nota: EstadoNfse): boolean {
  return ['pending', 'processing', 'desconhecido'].includes(nota.status)
    || (nota.status === 'erro' && !podeEmitirNfse(nota));
}
