export type MetricIcon = 'truck' | 'target' | 'timer' | 'fuel';

export type HomeMetric = {
  id: string;
  icon: MetricIcon;
  label: string;
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  /** Variação percentual no período (sinal indica a direção). */
  changePct: number;
  /** Direção da variação considerada favorável para esta métrica. */
  goodDirection: 'up' | 'down';
  description: string;
};

export type OverviewPoint = {
  month: string;
  deliveries: number;
  activeVehicles: number;
};

export function getHomeMetrics(): HomeMetric[] {
  return [
    {
      id: 'tracked-vehicles',
      icon: 'truck',
      label: 'Veículos monitorados',
      value: 1284,
      changePct: 6.4,
      goodDirection: 'up',
      description: 'Equipamentos ativos na frota nos últimos 30 dias',
    },
    {
      id: 'on-time-deliveries',
      icon: 'target',
      label: 'Entregas no prazo',
      value: 98.6,
      decimals: 1,
      suffix: '%',
      changePct: 1.2,
      goodDirection: 'up',
      description: 'Taxa de cumprimento das janelas de entrega',
    },
    {
      id: 'response-time',
      icon: 'timer',
      label: 'Tempo médio de resposta',
      value: 3.2,
      decimals: 1,
      suffix: ' min',
      changePct: -18.5,
      goodDirection: 'down',
      description: 'Tempo entre alerta crítico e ação operacional',
    },
    {
      id: 'fuel-savings',
      icon: 'fuel',
      label: 'Economia em combustível',
      value: 184500,
      prefix: 'R$ ',
      changePct: 12.3,
      goodDirection: 'up',
      description: 'Redução de custo mensal com roteirização inteligente',
    },
  ];
}

export function getOverviewSeries(): OverviewPoint[] {
  return [
    { month: 'Jul', deliveries: 8200, activeVehicles: 980 },
    { month: 'Ago', deliveries: 8650, activeVehicles: 1010 },
    { month: 'Set', deliveries: 9100, activeVehicles: 1045 },
    { month: 'Out', deliveries: 9460, activeVehicles: 1080 },
    { month: 'Nov', deliveries: 9890, activeVehicles: 1120 },
    { month: 'Dez', deliveries: 10240, activeVehicles: 1150 },
    { month: 'Jan', deliveries: 9780, activeVehicles: 1165 },
    { month: 'Fev', deliveries: 10120, activeVehicles: 1190 },
    { month: 'Mar', deliveries: 10580, activeVehicles: 1210 },
    { month: 'Abr', deliveries: 11020, activeVehicles: 1238 },
    { month: 'Mai', deliveries: 11460, activeVehicles: 1262 },
    { month: 'Jun', deliveries: 11890, activeVehicles: 1284 },
  ];
}

export function formatMetricValue(value: number, decimals = 0): string {
  return new Intl.NumberFormat('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}
