import type { Dispatch, FormEvent, SetStateAction } from 'react';
import { Plus, Trash2 } from 'lucide-react';

import { Modal } from '@/components/ui/modal';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { FormField, FormGrid, FormSection, FormDivider } from '@/components/ui/form-field';
import { BillingDayInput, erroDiaVencimento } from '@/components/ui/billing-day-input';
import { formatPhone } from '@/lib/format';
import { documentCategoryOptions, fileInputClass } from './helpers';
import type { ClientFormState, ContactItem } from './types';

export function ClientFormModal({
  open,
  isEditing,
  error,
  saving,
  canEdit,
  form,
  onFieldChange,
  onFormPatch,
  onAddContact,
  onRemoveContact,
  onUpdateContact,
  onZipBlur,
  onBuscarCep,
  lookingUpCep,
  docCategory,
  onDocCategoryChange,
  onDocFilesChange,
  onClose,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  isEditing: boolean;
  error: string;
  saving: boolean;
  canEdit: boolean;
  form: ClientFormState;
  onFieldChange: (field: keyof ClientFormState, value: string) => void;
  onFormPatch: Dispatch<SetStateAction<ClientFormState>>;
  onAddContact: () => void;
  onRemoveContact: (index: number) => void;
  onUpdateContact: (index: number, field: keyof ContactItem, value: string) => void;
  onZipBlur: (rawCep: string) => void;
  onBuscarCep: () => void;
  lookingUpCep: boolean;
  docCategory: string;
  onDocCategoryChange: (category: string) => void;
  onDocFilesChange: (files: File[]) => void;
  onClose: () => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEditing ? 'Editar cliente' : 'Novo cliente'}
      description="Preencha os dados principais do cadastro. Você também pode incluir documentação inicial no mesmo fluxo."
      size="xl"
    >
      <form className="space-y-6" onSubmit={onSubmit}>
        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}

        <FormSection title="Dados principais">
          <FormGrid cols={2}>
            <FormField label="Nome / razão social" required>
              <Input placeholder="Nome completo ou razão social" value={form.name} onChange={(e) => onFieldChange('name', e.target.value)} required />
            </FormField>
            <FormField label="Nome fantasia" hint="Opcional — usado principalmente para pessoa jurídica">
              <Input placeholder="Nome fantasia" value={form.trade_name} onChange={(e) => onFieldChange('trade_name', e.target.value)} />
            </FormField>
            <FormGrid cols={2}>
              <FormField label="Tipo de pessoa" required>
                <Select value={form.type} onChange={(e) => onFieldChange('type', e.target.value)}>
                  <option value="pf">Pessoa física</option>
                  <option value="pj">Pessoa jurídica</option>
                </Select>
              </FormField>
              <FormField label="Status" required>
                <Select value={form.status} onChange={(e) => onFieldChange('status', e.target.value)}>
                  <option value="ativo">Ativo</option>
                  <option value="inativo">Inativo</option>
                  <option value="inadimplente">Inadimplente</option>
                  <option value="suspenso">Suspenso</option>
                </Select>
              </FormField>
            </FormGrid>
            <FormField label={form.type === 'pj' ? 'CNPJ' : 'CPF'} required>
              <Input placeholder={form.type === 'pj' ? '00.000.000/0001-00' : '000.000.000-00'} value={form.cpf_cnpj} onChange={(e) => onFieldChange('cpf_cnpj', e.target.value)} required />
            </FormField>
            <FormField label="RG / Inscrição Estadual">
              <Input value={form.rg_ie} onChange={(e) => onFieldChange('rg_ie', e.target.value)} />
            </FormField>
            <FormField label="Data de nascimento">
              <Input type="date" value={form.birth_date} onChange={(e) => onFieldChange('birth_date', e.target.value)} />
            </FormField>
            <FormField label="E-mail principal" required>
              <Input type="email" placeholder="email@empresa.com" value={form.email} onChange={(e) => onFieldChange('email', e.target.value)} required />
            </FormField>
            <FormField label="Telefone principal">
              <div className="flex items-center gap-3">
                <Input placeholder="(11) 99999-0000" value={form.phone} onChange={(e) => onFieldChange('phone', e.target.value)} />
                <label
                  className={[
                    'flex shrink-0 cursor-pointer select-none items-center gap-2 rounded-xl border px-3 py-2.5 text-xs font-bold uppercase tracking-wide transition-colors',
                    form.send_boleto_whatsapp
                      ? 'border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-600 dark:bg-emerald-950/30 dark:text-emerald-300'
                      : 'border-orange-300 bg-orange-50 text-orange-600 hover:bg-orange-100 dark:border-orange-800/60 dark:bg-orange-950/20 dark:text-orange-400',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    checked={form.send_boleto_whatsapp}
                    onChange={(e) => onFormPatch((prev) => ({ ...prev, send_boleto_whatsapp: e.target.checked }))}
                    className="h-4 w-4 rounded accent-emerald-600"
                  />
                  Enviar boleto via Whats
                </label>
              </div>
            </FormField>
          </FormGrid>
          <FormField label="E-mails adicionais" hint="Um por linha ou separados por vírgula">
            <Textarea placeholder="outro@email.com, terceiro@email.com" value={form.extra_emails} onChange={(e) => onFieldChange('extra_emails', e.target.value)} className="min-h-[72px]" />
          </FormField>
        </FormSection>

        <FormDivider />

        <FormSection title="Contatos adicionais">
          <div className="flex items-center justify-between">
            <p className="text-xs text-slate-400 dark:text-slate-500">Responsáveis, técnicos ou gestores adicionais.</p>
            <Button type="button" variant="secondary" onClick={onAddContact} className="gap-1.5">
              <Plus className="h-3.5 w-3.5" /> Adicionar contato
            </Button>
          </div>
          {form.contacts.map((contact, i) => (
            <div key={i} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900/50 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
              <Input placeholder="Nome" value={contact.name} onChange={(e) => onUpdateContact(i, 'name', e.target.value)} />
              <Input placeholder="Telefone" value={contact.phone} onChange={(e) => onUpdateContact(i, 'phone', formatPhone(e.target.value))} />
              <Input placeholder="E-mail" value={contact.email} onChange={(e) => onUpdateContact(i, 'email', e.target.value)} />
              <Input placeholder="Cargo" value={contact.role} onChange={(e) => onUpdateContact(i, 'role', e.target.value)} />
              <button type="button" onClick={() => onRemoveContact(i)} className="flex items-center justify-center rounded-xl border border-rose-200 bg-rose-50 px-3 text-rose-600 hover:bg-rose-100 dark:border-rose-900/40 dark:bg-rose-950/30">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </FormSection>

        <FormDivider />

        <FormSection title="Endereço">
          <div className="flex gap-2">
            <FormField label="CEP" className="w-40">
              <Input placeholder="00000-000" value={form.zip_code} onChange={(e) => onFieldChange('zip_code', e.target.value)} onBlur={(e) => onZipBlur(e.target.value)} />
            </FormField>
            <div className="flex items-end">
              <Button type="button" variant="secondary" onClick={onBuscarCep} disabled={lookingUpCep}>
                {lookingUpCep ? 'Buscando…' : 'Buscar CEP'}
              </Button>
            </div>
          </div>
          <FormGrid cols={3}>
            <FormField label="Logradouro" className="col-span-2">
              <Input placeholder="Rua / Avenida" value={form.address_line} onChange={(e) => onFieldChange('address_line', e.target.value)} />
            </FormField>
            <FormField label="Número">
              <Input placeholder="Nº" value={form.address_number} onChange={(e) => onFieldChange('address_number', e.target.value)} />
            </FormField>
            <FormField label="Complemento">
              <Input placeholder="Apto, sala…" value={form.address_complement} onChange={(e) => onFieldChange('address_complement', e.target.value)} />
            </FormField>
            <FormField label="Bairro">
              <Input placeholder="Bairro" value={form.neighborhood} onChange={(e) => onFieldChange('neighborhood', e.target.value)} />
            </FormField>
            <FormField label="Cidade">
              <Input placeholder="Cidade" value={form.city} onChange={(e) => onFieldChange('city', e.target.value)} />
            </FormField>
            <FormField label="UF">
              <Input placeholder="SP" value={form.state} onChange={(e) => onFieldChange('state', e.target.value)} maxLength={2} />
            </FormField>
          </FormGrid>
          <FormField
            label="Dia de vencimento preferido"
            hint="1 a 31 — usado como padrão ao gerar contratos e cobranças. Em meses mais curtos, cai no último dia."
            error={erroDiaVencimento(form.billing_day) ?? undefined}
          >
            <BillingDayInput
              value={form.billing_day}
              onChange={(v) => onFormPatch((p) => ({ ...p, billing_day: v }))}
              placeholder="Ex.: 20"
              className="max-w-[120px]"
            />
          </FormField>
          <FormField label="Observações">
            <Textarea placeholder="Anotações administrativas internas" value={form.notes} onChange={(e) => onFieldChange('notes', e.target.value)} />
          </FormField>
        </FormSection>

        <FormDivider />

        <FormSection title="Contatos de emergência (pessoas autorizadas)">
          <FormGrid cols={3}>
            <FormField label="Contato 1">
              <Input placeholder="Nome" value={form.em1_name} onChange={(e) => onFieldChange('em1_name', e.target.value)} />
            </FormField>
            <FormField label="Telefone">
              <Input value={form.em1_phone} onChange={(e) => onFieldChange('em1_phone', e.target.value)} />
            </FormField>
            <FormField label="Celular">
              <Input value={form.em1_mobile} onChange={(e) => onFieldChange('em1_mobile', e.target.value)} />
            </FormField>
            <FormField label="Contato 2">
              <Input placeholder="Nome" value={form.em2_name} onChange={(e) => onFieldChange('em2_name', e.target.value)} />
            </FormField>
            <FormField label="Telefone">
              <Input value={form.em2_phone} onChange={(e) => onFieldChange('em2_phone', e.target.value)} />
            </FormField>
            <FormField label="Celular">
              <Input value={form.em2_mobile} onChange={(e) => onFieldChange('em2_mobile', e.target.value)} />
            </FormField>
          </FormGrid>
        </FormSection>

        <FormDivider />

        <FormSection title="Financeiro">
          <FormGrid cols={3}>
            <FormField label="Formato do Boleto" required>
              <Select value={form.boleto_format} onChange={(e) => onFieldChange('boleto_format', e.target.value)}>
                <option value="unico">Boleto Único</option>
                <option value="individual">Boleto Individual</option>
              </Select>
            </FormField>
            <FormField label="Taxa de Emissão do Boleto">
              <Select value={form.boleto_fee} onChange={(e) => onFieldChange('boleto_fee', e.target.value)}>
                <option value="nao">Não</option>
                <option value="sim">Sim</option>
              </Select>
            </FormField>
            <FormField label="Emitir Nota Fiscal">
              <Select value={form.issue_invoice} onChange={(e) => onFieldChange('issue_invoice', e.target.value)}>
                <option value="sim">Sim</option>
                <option value="nao">Não</option>
              </Select>
            </FormField>
            <FormField label="Tributação">
              <Select value={form.tributacao} onChange={(e) => onFieldChange('tributacao', e.target.value)}>
                <option value="dentro_municipio">Dentro do município</option>
                <option value="fora_municipio">Fora do município</option>
                <option value="isento">Isento</option>
              </Select>
            </FormField>
            <FormField label="Reter ISS">
              <Select value={form.iss_retido} onChange={(e) => onFieldChange('iss_retido', e.target.value)}>
                <option value="nao">Não</option>
                <option value="sim">Sim</option>
              </Select>
            </FormField>
            <FormField label="Optante do Simples Nacional">
              <Select value={form.optante_simples} onChange={(e) => onFieldChange('optante_simples', e.target.value)}>
                <option value="sim">Sim</option>
                <option value="nao">Não</option>
              </Select>
            </FormField>
            <FormField label="Tipo de Envio">
              <Select value={form.delivery_method} onChange={(e) => onFieldChange('delivery_method', e.target.value)}>
                <option value="email">Email</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="todos">Todos</option>
              </Select>
            </FormField>
          </FormGrid>
        </FormSection>

        <FormDivider />

        <FormSection title="Documentação inicial (opcional)">
          <p className="text-xs text-slate-400 dark:text-slate-500">Arquivos enviados automaticamente após salvar o cliente.</p>
          <FormGrid cols={2}>
            <FormField label="Categoria">
              <Select value={docCategory} onChange={(e) => onDocCategoryChange(e.target.value)}>
                {documentCategoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
              </Select>
            </FormField>
            <FormField label="Arquivo(s)">
              <input type="file" multiple className={fileInputClass} onChange={(e) => onDocFilesChange(Array.from(e.target.files || []))} />
            </FormField>
          </FormGrid>
        </FormSection>

        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button type="button" variant="secondary" onClick={onCancel}>Cancelar</Button>
          <Button type="submit" disabled={!canEdit || saving}>{saving ? 'Salvando…' : isEditing ? 'Atualizar cliente' : 'Cadastrar cliente'}</Button>
        </div>
      </form>
    </Modal>
  );
}
