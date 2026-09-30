# Exercício de recuperação ensaio20260930101604

- Commit do código testado: `28af706d1e2479d259d500dd849a4ed22d9989eb` (arquivos não commitados em backup/ e backend/: 11)
- PostgreSQL servidor: 16.14 (Debian 16.14-1.pgdg13+1); pg_dump (PostgreSQL) 16.14 (Debian 16.14-1.pgdg13+1)
- Revisão Alembic no backup: `b3f8a1c9d2e7`
- Execução: `20260930_101724` — dump `db.dump` sha256 `d15273350b0b2548c2149ea9d2ca5f73c42b6a58939b8f6495b910cbae6efa97` (3968673 bytes)
- Tabelas: 35; linhas: 502306; objetos: 1921 (78984190 bytes); referências obrigatórias: 1871, ausentes: 0
- Impressão digital da chave Fernet no manifest: `sha256:187776ed5adc0ca4`
- Bancos no servidor novo ao final: `rastreamento,rastreamento_pre_restore_20260930_101815` (o banco vazio original foi preservado pela troca)
- Controle negativo sem chave Fernet: saída 1 (reprovado, como esperado)

## Tempos (segundos)

| Etapa | s |
|---|---|
| build imagem backend | 2 |
| build imagem backup | 2 |
| subir servidores de origem | 4 |
| schema (alembic upgrade head) | 2 |
| semear dados sintéticos | 63 |
| backup.sh (banco + objetos + envio externo) | 9 |
| subir servidores novos (vazios) | 4 |
| baixar do destino externo | 7 |
| restaurar banco em nome novo + conferir inventário | 4 |
| restaurar objetos no MinIO novo | 8 |
| promover (troca controlada) | 2 |
| verificar segredos, PFX, documentos e saldo | 4 |
| **RTO medido** (servidores novos → verificação aprovada) | **29** |
| Janela de perda no ensaio (início do backup → desastre) | 22 |

## Saldo financeiro restaurado (centavos)

```json
{
  "aprovado": true,
  "erros": [],
  "avisos": [],
  "alembic_revision": "b3f8a1c9d2e7",
  "fernet_key_fingerprint": "sha256:187776ed5adc0ca4",
  "segredos": {
    "ailos_client_tokens": {
      "total": 1,
      "decifrados": 1,
      "falhas": 0
    },
    "ailos_integrations": {
      "total": 1,
      "decifrados": 1,
      "falhas": 0
    },
    "smtp_password": {
      "total": 1,
      "decifrados": 1,
      "falhas": 0
    },
    "nfse_certificados": {
      "total": 1,
      "decifrados": 1,
      "falhas": 0
    }
  },
  "certificados": [
    {
      "id": 1,
      "ativo": true,
      "abre": true,
      "valido_ate": "2027-09-30T10:16:34+00:00",
      "vencido": false
    }
  ],
  "objetos": {
    "referencias_obrigatorias": 1871,
    "ausentes": 0,
    "tamanho_divergente": 0,
    "inativos_sem_objeto": 80
  },
  "financeiro": {
    "billings[PENDING]": {
      "quantidade": 94992,
      "valor_centavos": 1138943080,
      "pago_centavos": 0
    },
    "billings[PENDING|excluida]": {
      "quantidade": 1008,
      "valor_centavos": 12085920,
      "pago_centavos": 0
    },
    "billings[PAID]": {
      "quantidade": 190032,
      "valor_centavos": 2278459680,
      "pago_centavos": 2278459680
    },
    "billings[PAID|excluida]": {
      "quantidade": 1968,
      "valor_centavos": 23600320,
      "pago_centavos": 23600320
    },
    "billings[OVERDUE]": {
      "quantidade": 95016,
      "valor_centavos": 1139232840,
      "pago_centavos": 0
    },
    "billings[OVERDUE|excluida]": {
      "quantidade": 984,
      "valor_centavos": 11800160,
      "pago_centavos": 0
    },
    "billings[CANCELED]": {
      "quantidade": 95016,
      "valor_centavos": 1139228840,
      "pago_centavos": 0
    },
    "billings[CANCELED|excluida]": {
      "quantidade": 984,
      "valor_centavos": 11801160,
      "pago_centavos": 0
    },
    "payables": {
      "quantidade": 300,
      "valor_centavos": 8995500
    }
  }
}
```

## Inventário de cobranças na origem (mesmo snapshot do dump)

```
billings	CANCELED|excluida=false	95016|11392288.40|0
billings	CANCELED|excluida=true	984|118011.60|0
billings	OVERDUE|excluida=false	95016|11392328.40|0
billings	OVERDUE|excluida=true	984|118001.60|0
billings	PAID|excluida=false	190032|22784596.80|22784596.80
billings	PAID|excluida=true	1968|236003.20|236003.20
billings	PENDING|excluida=false	94992|11389430.80|0
billings	PENDING|excluida=true	1008|120859.20|0
```
