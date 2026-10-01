# Exercício de recuperação ensaio20260930101420

- Commit do código testado: `28af706d1e2479d259d500dd849a4ed22d9989eb` (arquivos não commitados em backup/ e backend/: 11)
- PostgreSQL servidor: 16.14 (Debian 16.14-1.pgdg13+1); pg_dump (PostgreSQL) 16.14 (Debian 16.14-1.pgdg13+1)
- Revisão Alembic no backup: `b3f8a1c9d2e7`
- Execução: `20260930_101439` — dump `db.dump` sha256 `d62eadbb652327f7db7217ea036389a68c99832111f0306a76f6cb79b7db5592` (227396 bytes)
- Tabelas: 35; linhas: 7006; objetos: 193 (1505854 bytes); referências obrigatórias: 188, ausentes: 0
- Impressão digital da chave Fernet no manifest: `sha256:022415674e671483`
- Bancos no servidor novo ao final: `rastreamento,rastreamento_pre_restore_20260930_101500` (o banco vazio original foi preservado pela troca)
- Controle negativo sem chave Fernet: saída 1 (reprovado, como esperado)

## Tempos (segundos)

| Etapa | s |
|---|---|
| build imagem backend | 2 |
| build imagem backup | 1 |
| subir servidores de origem | 3 |
| schema (alembic upgrade head) | 2 |
| semear dados sintéticos | 3 |
| backup.sh (banco + objetos + envio externo) | 3 |
| subir servidores novos (vazios) | 3 |
| baixar do destino externo | 2 |
| restaurar banco em nome novo + conferir inventário | 1 |
| restaurar objetos no MinIO novo | 3 |
| promover (troca controlada) | 1 |
| verificar segredos, PFX, documentos e saldo | 2 |
| **RTO medido** (servidores novos → verificação aprovada) | **14** |
| Janela de perda no ensaio (início do backup → desastre) | 9 |

## Saldo financeiro restaurado (centavos)

```json
{
  "aprovado": true,
  "erros": [],
  "avisos": [],
  "alembic_revision": "b3f8a1c9d2e7",
  "fernet_key_fingerprint": "sha256:022415674e671483",
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
      "valido_ate": "2027-09-30T10:14:37+00:00",
      "vencido": false
    }
  ],
  "objetos": {
    "referencias_obrigatorias": 188,
    "ausentes": 0,
    "tamanho_divergente": 0,
    "inativos_sem_objeto": 8
  },
  "financeiro": {
    "billings[PENDING]": {
      "quantidade": 1188,
      "valor_centavos": 14230120,
      "pago_centavos": 0
    },
    "billings[PENDING|excluida]": {
      "quantidade": 12,
      "valor_centavos": 148880,
      "pago_centavos": 0
    },
    "billings[PAID]": {
      "quantidade": 2376,
      "valor_centavos": 28487240,
      "pago_centavos": 28487240
    },
    "billings[PAID|excluida]": {
      "quantidade": 24,
      "valor_centavos": 281760,
      "pago_centavos": 281760
    },
    "billings[OVERDUE]": {
      "quantidade": 1188,
      "valor_centavos": 14235120,
      "pago_centavos": 0
    },
    "billings[OVERDUE|excluida]": {
      "quantidade": 12,
      "valor_centavos": 140880,
      "pago_centavos": 0
    },
    "billings[CANCELED]": {
      "quantidade": 1188,
      "valor_centavos": 14236120,
      "pago_centavos": 0
    },
    "billings[CANCELED|excluida]": {
      "quantidade": 12,
      "valor_centavos": 143880,
      "pago_centavos": 0
    },
    "payables": {
      "quantidade": 300,
      "valor_centavos": 8995500
    }
  }
}```

## Inventário de cobranças na origem (mesmo snapshot do dump)

```
billings	CANCELED|excluida=false	1188|142361.20|0
billings	CANCELED|excluida=true	12|1438.80|0
billings	OVERDUE|excluida=false	1188|142351.20|0
billings	OVERDUE|excluida=true	12|1408.80|0
billings	PAID|excluida=false	2376|284872.40|284872.40
billings	PAID|excluida=true	24|2817.60|2817.60
billings	PENDING|excluida=false	1188|142301.20|0
billings	PENDING|excluida=true	12|1488.80|0
```
