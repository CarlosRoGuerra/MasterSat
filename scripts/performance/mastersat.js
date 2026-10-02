// k6 run -e BASE_URL=http://localhost:8000/api/v1 -e EMAIL=... -e PASSWORD=... scripts/performance/mastersat.js
// Execute somente em homologação. O login ocorre uma vez para respeitar 5/min por IP.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errors = new Rate('request_errors');
const base = (__ENV.BASE_URL || 'http://localhost:8000/api/v1').replace(/\/$/, '');

export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '2m', target: 10 },
    { duration: '1m', target: 25 },
    { duration: '2m', target: 25 },
    { duration: '1m', target: 50 },
    { duration: '2m', target: 50 },
    { duration: '1m', target: 0 },
  ],
  thresholds: { request_errors: ['rate<0.01'] },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  if (__ENV.ACCESS_TOKEN) return { token: __ENV.ACCESS_TOKEN };
  if (!__ENV.EMAIL || !__ENV.PASSWORD) throw new Error('Informe ACCESS_TOKEN ou EMAIL e PASSWORD');
  const response = http.post(`${base}/auth/login`, JSON.stringify({
    email: __ENV.EMAIL, password: __ENV.PASSWORD,
  }), { headers: { 'Content-Type': 'application/json' }, tags: { name: 'login' } });
  if (response.status !== 200) throw new Error(`Login falhou: HTTP ${response.status}`);
  return { token: response.json('access_token') };
}

function get(path, name, token) {
  const response = http.get(`${base}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { name },
  });
  const ok = check(response, { [`${name} HTTP 200`]: (r) => r.status === 200 });
  errors.add(!ok);
}

export default function (data) {
  get('/dashboard/', 'dashboard', data.token);
  get('/clients/?skip=0&limit=20&sort=name&direction=asc', 'clients_list', data.token);
  get('/clients/?search=a&skip=0&limit=20', 'clients_search', data.token);
  get('/vehicles/?skip=0&limit=20', 'vehicles_list', data.token);
  get('/billings/summary', 'finance_summary', data.token);
  sleep(1);
}
