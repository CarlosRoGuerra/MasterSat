import type { SearchResultItem } from './domain-types';

/**
 * Rota + querystring de cada resultado da Busca Global. O sistema não tem
 * rota de detalhe (`/veiculos/[id]` etc.) — cada listagem abre seu próprio
 * modal via estado local (ver `openDetails` em veiculos/page.tsx,
 * ordens-servico/page.tsx, etc.). Por isso a navegação aqui é sempre
 * "listagem + querystring", e a própria página abre o modal certo ao
 * carregar (ver o efeito de deep-link em cada uma dessas páginas).
 *
 * Contrato não tem tela própria — só é visível hoje pela "Ficha de
 * adesão / contrato" do cliente (`ContractSheetModal`, aberta a partir de
 * clientes/page.tsx). Documento aponta pro registro DONO dele (cliente,
 * veículo ou OS) — não existe tela própria de "detalhe de documento".
 *
 * O parâmetro só precisa dos campos de navegação (não de title/subtitle/
 * status) — isso permite reaproveitar a função a partir de outra origem que
 * não é um SearchResultItem completo (ex.: TimelineLink da Linha do Tempo
 * do Cliente, ver client-historico-tab.tsx).
 */
type NavigableItem = Pick<SearchResultItem, 'entity' | 'id' | 'client_id' | 'vehicle_id' | 'service_order_id'>;

export function buildSearchResultHref(item: NavigableItem): string {
  switch (item.entity) {
    case 'client':
      return `/clientes?focus=${item.id}`;
    case 'vehicle':
      return `/veiculos?focus=${item.id}`;
    case 'tracker':
      return `/rastreadores?focus=${item.id}`;
    case 'service_order':
      return `/ordens-servico?focus=${item.id}`;
    case 'contract':
      return item.client_id ? `/clientes?focus=${item.client_id}&panel=contratos` : '/clientes';
    case 'document': {
      if (item.service_order_id) return `/ordens-servico?focus=${item.service_order_id}&tab=documentos`;
      if (item.vehicle_id) return `/veiculos?focus=${item.vehicle_id}&tab=documentos`;
      if (item.client_id) return `/clientes?focus=${item.client_id}&panel=documentos`;
      return '/clientes';
    }
    default:
      return '/dashboard';
  }
}
