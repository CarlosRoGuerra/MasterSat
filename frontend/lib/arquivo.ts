/**
 * Entrega ao usuário um arquivo que já veio da API.
 *
 * O detalhe que morde: `URL.revokeObjectURL` logo depois de `window.open`
 * destrói o blob antes de a aba nova terminar de carregá-lo, e o navegador
 * acusa "Verifique a conexão com a Internet" com o arquivo nomeado pelo UUID
 * do blob. Revogar precisa esperar — daí o timeout.
 */
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
