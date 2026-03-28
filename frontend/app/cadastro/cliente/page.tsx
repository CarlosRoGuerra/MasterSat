'use client';

import { ChangeEvent, FormEvent, useMemo, useState } from 'react';

import { AuthShell } from '@/components/auth-shell';
import { Button } from '@/components/ui/button';
import { apiFetch } from '@/lib/api';
import { redirectByRole, saveSession } from '@/lib/auth';
import { fetchAddressByCep } from '@/lib/cep';
import { formatCpfCnpj, formatPhone, formatZipCode, onlyDigits, validatePassword } from '@/lib/format';

type RegisterResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: {
    id: number;
    name: string;
    email: string;
    role: 'cliente';
    client_id?: number | null;
  };
  message: string;
};

type RegisterForm = {
  type: 'pf' | 'pj';
  name: string;
  cpf_cnpj: string;
  email: string;
  extra_emails: string;
  phone: string;
  zip_code: string;
  address_line: string;
  address_number: string;
  address_complement: string;
  neighborhood: string;
  city: string;
  state: string;
  password: string;
  password_confirmation: string;
};

const initialForm: RegisterForm = {
  type: 'pf',
  name: '',
  cpf_cnpj: '',
  email: '',
  extra_emails: '',
  phone: '',
  zip_code: '',
  address_line: '',
  address_number: '',
  address_complement: '',
  neighborhood: '',
  city: '',
  state: '',
  password: '',
  password_confirmation: '',
};

function parseExtraEmails(value: string) {
  return value
    .split(/[,;\n]/)
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
}

export default function ClientRegisterPage() {
  const [form, setForm] = useState<RegisterForm>(initialForm);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);
  const [lookingUpCep, setLookingUpCep] = useState(false);

  const passwordChecks = useMemo(() => validatePassword(form.password), [form.password]);

  function updateField(field: keyof RegisterForm, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function fillAddressFromCep(rawCep: string) {
    const cep = onlyDigits(rawCep);
    if (cep.length !== 8) return;
    setLookingUpCep(true);
    setError('');
    try {
      const result = await fetchAddressByCep(cep);
      if (!result) return;
      setForm((current) => ({
        ...current,
        zip_code: formatZipCode(result.zip_code),
        address_line: current.address_line || result.address_line,
        address_complement: current.address_complement || result.address_complement,
        neighborhood: current.neighborhood || result.neighborhood,
        city: current.city || result.city,
        state: current.state || result.state,
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível consultar o CEP.');
    } finally {
      setLookingUpCep(false);
    }
  }

  function handleFormattedChange(field: keyof RegisterForm) {
    return (event: ChangeEvent<HTMLInputElement>) => {
      let value = event.target.value;
      if (field === 'cpf_cnpj') value = formatCpfCnpj(value);
      if (field === 'phone') value = formatPhone(value);
      if (field === 'zip_code') value = formatZipCode(value);
      if (field === 'state') value = value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 2);
      updateField(field, value);
    };
  }

  function validateForm() {
    if (form.name.trim().length < 3) return 'Informe o nome completo ou razão social.';
    const docDigits = onlyDigits(form.cpf_cnpj);
    if (![11, 14].includes(docDigits.length)) return 'Informe um CPF ou CNPJ válido.';
    if (!form.email.includes('@')) return 'Informe um e-mail principal válido.';
    if (form.type === 'pj' && form.extra_emails.trim()) {
      const extras = parseExtraEmails(form.extra_emails);
      if (extras.some((email) => !email.includes('@') || !email.split('@')[1]?.includes('.'))) {
        return 'Revise os e-mails adicionais da empresa.';
      }
    }
    if (![10, 11].includes(onlyDigits(form.phone).length)) return 'Informe um telefone válido.';
    if (onlyDigits(form.zip_code).length !== 8) return 'Informe um CEP válido.';
    if (form.address_line.trim().length < 3) return 'Informe o logradouro.';
    if (form.address_number.trim().length < 1) return 'Informe o número do endereço.';
    if (form.neighborhood.trim().length < 2) return 'Informe o bairro.';
    if (form.city.trim().length < 2) return 'Informe a cidade.';
    if (form.state.trim().length !== 2) return 'Informe a UF com 2 letras.';
    if (!Object.values(passwordChecks).every(Boolean)) return 'A senha não atende aos critérios mínimos.';
    if (form.password !== form.password_confirmation) return 'As senhas não conferem.';
    return '';
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    setSuccess('');

    const validationMessage = validateForm();
    if (validationMessage) {
      setError(validationMessage);
      setLoading(false);
      return;
    }

    try {
      const payload = {
        ...form,
        email: form.email.trim().toLowerCase(),
        extra_emails: form.type === 'pj' ? parseExtraEmails(form.extra_emails) : undefined,
      };
      const result = await apiFetch<RegisterResponse>('/auth/register-client', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      saveSession(result.access_token, result.refresh_token);
      setSuccess(result.message);
      redirectByRole(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível concluir o cadastro.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Cadastro do cliente"
      subtitle="Preencha seus dados para criar o acesso ao portal do cliente."
      roleLabel="Cliente"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Tipo de pessoa</span>
            <select className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.type} onChange={(e) => updateField('type', e.target.value as 'pf' | 'pj')}>
              <option value="pf">Pessoa física</option>
              <option value="pj">Pessoa jurídica</option>
            </select>
          </label>
          <label className="block md:col-span-2">
            <span className="mb-2 block text-sm font-medium text-slate-700">Nome completo / Razão social</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.name} onChange={(e) => updateField('name', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">CPF / CNPJ</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.cpf_cnpj} onChange={handleFormattedChange('cpf_cnpj')} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Telefone / WhatsApp</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.phone} onChange={handleFormattedChange('phone')} />
          </label>
          <label className="block md:col-span-2">
            <span className="mb-2 block text-sm font-medium text-slate-700">E-mail principal</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.email} onChange={(e) => updateField('email', e.target.value.toLowerCase())} />
          </label>
          {form.type === 'pj' && (
            <label className="block md:col-span-2">
              <span className="mb-2 block text-sm font-medium text-slate-700">E-mails adicionais da empresa</span>
              <textarea
                className="min-h-[96px] w-full rounded-xl border border-slate-300 px-4 py-3"
                placeholder="Separe por vírgula ou uma linha por e-mail"
                value={form.extra_emails}
                onChange={(e) => updateField('extra_emails', e.target.value)}
              />
            </label>
          )}
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">CEP</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.zip_code} onChange={handleFormattedChange('zip_code')} onBlur={(e) => fillAddressFromCep(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">UF</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.state} onChange={handleFormattedChange('state')} />
          </label>
          <label className="block md:col-span-2">
            <span className="mb-2 block text-sm font-medium text-slate-700">Logradouro</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_line} onChange={(e) => updateField('address_line', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Número</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_number} onChange={(e) => updateField('address_number', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Complemento</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.address_complement} onChange={(e) => updateField('address_complement', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Bairro</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.neighborhood} onChange={(e) => updateField('neighborhood', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Cidade</span>
            <input className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.city} onChange={(e) => updateField('city', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Senha</span>
            <input type="password" className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.password} onChange={(e) => updateField('password', e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-2 block text-sm font-medium text-slate-700">Confirmar senha</span>
            <input type="password" className="w-full rounded-xl border border-slate-300 px-4 py-3" value={form.password_confirmation} onChange={(e) => updateField('password_confirmation', e.target.value)} />
          </label>
        </div>

        <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-600">
          <p className="mb-2 font-semibold text-slate-900">Regras da senha</p>
          <div className="grid gap-1 md:grid-cols-2">
            <p className={passwordChecks.minLength ? 'text-emerald-600' : 'text-slate-500'}>• Mínimo de 8 caracteres</p>
            <p className={passwordChecks.upper ? 'text-emerald-600' : 'text-slate-500'}>• Uma letra maiúscula</p>
            <p className={passwordChecks.lower ? 'text-emerald-600' : 'text-slate-500'}>• Uma letra minúscula</p>
            <p className={passwordChecks.number ? 'text-emerald-600' : 'text-slate-500'}>• Um número</p>
            <p className={passwordChecks.special ? 'text-emerald-600' : 'text-slate-500'}>• Um caractere especial</p>
          </div>
        </div>

        {lookingUpCep && <p className="text-sm text-slate-500">Consultando CEP...</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
        {success && <p className="text-sm text-emerald-600">{success}</p>}

        <div className="flex justify-end">
          <Button type="submit" disabled={loading || lookingUpCep}>
            {loading ? 'Cadastrando...' : 'Criar conta'}
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
