import { describe, expect, it } from 'vitest';
import { podeEmitirNfse, precisaConsultarNfse } from './nfse';

describe('ações fiscais após resposta incerta', () => {
  it.each(['pending', 'processing', 'desconhecido'])('orienta consulta e bloqueia nova emissão em %s', (status) => {
    expect(podeEmitirNfse({ status })).toBe(false);
    expect(precisaConsultarNfse({ status })).toBe(true);
  });

  it('mantém erro legado sem classificação na reconciliação', () => {
    expect(podeEmitirNfse({ status: 'erro' })).toBe(false);
    expect(precisaConsultarNfse({ status: 'erro' })).toBe(true);
  });

  it.each(['local', 'rejeicao'])('permite nova tentativa explícita após erro %s', (erro_tipo) => {
    expect(podeEmitirNfse({ status: 'erro', erro_tipo })).toBe(true);
  });

  it('preserva autorizada e permite primeira emissão sem nota', () => {
    expect(podeEmitirNfse({ status: 'emitida' })).toBe(false);
    expect(podeEmitirNfse(null)).toBe(true);
  });
});
