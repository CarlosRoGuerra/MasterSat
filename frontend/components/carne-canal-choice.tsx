'use client';

/** Onde o carnê é gerado: registrado na Ailos (1 boleto real por parcela) ou
 *  só no sistema (carnê simples). As parcelas do carnê simples são marcadas
 *  `somente_sistema` e o backend recusa emiti-las no banco depois. */
export type CarneCanal = 'ailos' | 'sistema';

const OPCOES: { value: CarneCanal; titulo: string; descricao: string }[] = [
  { value: 'ailos', titulo: 'Registrar na Ailos', descricao: 'Carnê Ailos: 1 boleto real por parcela' },
  { value: 'sistema', titulo: 'Somente no sistema', descricao: 'Carnê simples: sem boleto no banco, recebimento manual' },
];

export function CarneCanalChoice({ value, onChange }: { value: CarneCanal; onChange: (canal: CarneCanal) => void }) {
  return (
    <div role="radiogroup" aria-label="Onde gerar o carnê" className="grid grid-cols-2 gap-2">
      {OPCOES.map((op) => (
        <button
          key={op.value}
          type="button"
          role="radio"
          aria-checked={value === op.value}
          onClick={() => onChange(op.value)}
          className={[
            'rounded-xl border px-4 py-3 text-left text-sm transition-colors',
            value === op.value
              ? 'border-brand-500 bg-brand-50 dark:border-brand-400 dark:bg-brand-950/30'
              : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800/60',
          ].join(' ')}
        >
          <span className="block font-semibold">{op.titulo}</span>
          <span className="mt-0.5 block text-xs text-slate-500">{op.descricao}</span>
        </button>
      ))}
    </div>
  );
}
