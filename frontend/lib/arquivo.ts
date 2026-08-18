/**
 * Entrega ao usuário um arquivo que já veio da API.
 *
 * O detalhe que morde: `URL.revokeObjectURL` logo depois de `window.open`
 * destrói o blob antes de a aba nova terminar de carregá-lo, e o navegador
 * acusa "Verifique a conexão com a Internet" com o arquivo nomeado pelo UUID
 * do blob. Revogar precisa esperar — daí o timeout.
 */
/**
 * Nome de arquivo no padrão pedido pelo cliente: nome do cliente + data referente
 * (ex.: "EUNICE SOUSA SIMAS 28-08-2026"). Sem extensão — o chamador adiciona.
 * Remove os caracteres proibidos em nome de arquivo para não quebrar o download.
 */
export function nomeArquivoCliente(cliente?: string | null, dataISO?: string | null): string {
  const nome = (cliente || 'cliente')
    .replace(/[\\/:*?"<>|]+/g, '')       // remove caracteres proibidos em nome de arquivo
    .replace(/\s+/g, ' ').trim() || 'cliente';
  let data = '';
  if (dataISO) {
    const d = new Date(dataISO.slice(0, 10) + 'T12:00:00');
    if (!Number.isNaN(d.getTime())) {
      data = ` ${String(d.getDate()).padStart(2, '0')}-${String(d.getMonth() + 1).padStart(2, '0')}-${d.getFullYear()}`;
    }
  }
  return `${nome}${data}`;
}

export function entregarArquivo(
  blob: Blob,
  nomeArquivo: string,
  { emNovaAba = false }: { emNovaAba?: boolean } = {},
): void {
  const url = URL.createObjectURL(blob);

  const baixar = () => {
    const a = document.createElement('a');
    a.href = url;
    a.download = nomeArquivo;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // window.open devolve null quando o bloqueador de pop-up barra a aba; sem o
  // fallback o clique não faria nada e o operador ficaria sem retorno nenhum.
  if (!emNovaAba || !window.open(url, '_blank')) baixar();

  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
