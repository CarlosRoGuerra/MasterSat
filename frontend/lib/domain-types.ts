/**
 * Tipos de domínio derivados do OpenAPI do backend (lib/api-types.generated.ts,
 * gerado por `npm run generate:api-types` — ver esse script para como
 * regenerar após uma mudança de schema no backend).
 *
 * Nem todo campo do backend chega tipado com precisão pelo OpenAPI: campos
 * Pydantic declarados como `str`/`int` "soltos" (sem Enum/Literal) — como
 * `Client.type` ou `Plan.billing_interval_months` — viram `string`/`number`
 * no schema, mesmo quando na prática só assumem um conjunto fixo de valores.
 * Só os campos que o backend já modela como Enum (ClientStatus, UserRole,
 * OrderType, etc.) têm união literal aqui; o resto continua tipado à mão nas
 * páginas que precisam da restrição.
 */
import type { components } from './api-types.generated';

export type ClientStatus = components['schemas']['ClientStatus'];
export type VehicleStatus = components['schemas']['VehicleStatus'];
export type TrackerStatus = components['schemas']['TrackerStatus'];
export type BillingStatus = components['schemas']['BillingStatus'];
export type OrderType = components['schemas']['OrderType'];
export type OrderStatus = components['schemas']['OrderStatus'];
export type DocumentReviewStatus = components['schemas']['DocumentReviewStatus'];
export type UserRole = components['schemas']['UserRole'];

/** Usuário autenticado — resposta de GET /auth/me. */
export type AuthUser = components['schemas']['app__schemas__auth__UserOut'];

type ClientFull = components['schemas']['ClientOut'];
type VehicleFull = components['schemas']['VehicleOut'];
type TrackerFull = components['schemas']['TrackerOut'];
type UserFull = components['schemas']['app__schemas__user__UserOut'];

/**
 * Recorte de cliente usado em autocompletes, selects e preenchimento
 * automático de endereço (ex.: veículo herdando o endereço do cliente).
 * Fundia 5 formas divergentes do mesmo conceito espalhadas em
 * financeiro/ordens-servico/veiculos/rastreadores/fechamento — cada página
 * usa só o subconjunto de campos que precisa, mas todas vêm do mesmo tipo.
 */
export type ClientOption = Pick<
  ClientFull,
  | 'id'
  | 'name'
  | 'cpf_cnpj'
  | 'billing_day'
  | 'zip_code'
  | 'address_line'
  | 'address_number'
  | 'address_complement'
  | 'neighborhood'
  | 'city'
  | 'state'
>;

export type VehicleOption = Pick<VehicleFull, 'id' | 'client_id' | 'plate' | 'model'>;

export type TrackerOption = Pick<
  TrackerFull,
  | 'id'
  | 'imei'
  | 'client_id'
  | 'vehicle_id'
  | 'status'
  | 'brand'
  | 'model'
  | 'install_date'
  | 'active_plan_id'
  | 'active_plan_name'
>;

export type UserOption = Pick<UserFull, 'id' | 'name' | 'role'>;
