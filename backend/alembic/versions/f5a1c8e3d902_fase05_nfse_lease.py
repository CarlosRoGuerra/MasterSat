"""Fase 05: reserva fiscal, lease, payload efetivo e reconciliação conservadora.

Não deduz resultado fiscal de status técnico. Pendentes e erros legados sem
classificação passam a desconhecido; XML, número, série, chave e protocolo
permanecem intactos. Nenhuma transmissão ou consulta ocorre na migration.

Rollback operacional é de imagem, com emissão desabilitada. O downgrade só
recua o carimbo Alembic: mantém colunas, evidências e guardas para a imagem
anterior poder ler o schema aditivo sem apagar a história fiscal.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f5a1c8e3d902'
down_revision = 'a4c7e2f9b1d6'
branch_labels = None
depends_on = None


def _columns():
    return [
        sa.Column('tentativa_id', sa.String(36)),
        sa.Column('tentativa_numero', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('emissor_id', sa.String(128)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True)),
        sa.Column('envio_iniciado_em', sa.DateTime(timezone=True)),
        sa.Column('erro_tipo', sa.String(20)),
        sa.Column('provedor', sa.String(20)),
        sa.Column('ambiente', sa.String(30)),
        sa.Column('prestador_cnpj', sa.String(14)),
        sa.Column('prestador_im', sa.String(30)),
        sa.Column('codigo_municipio', sa.String(7)),
        sa.Column('dps_id', sa.String(60)),
        sa.Column('competencia', sa.Date()),
        sa.Column('discriminacao', sa.Text()),
        sa.Column('codigo_servico', sa.String(20)),
        sa.Column('tentativas_anteriores', sa.JSON(), nullable=False, server_default='[]'),
    ]


def upgrade():
    conn = op.get_bind()
    existing = {c['name'] for c in sa.inspect(conn).get_columns('nfse_notas')}
    for column in _columns():
        if column.name not in existing:  # reaplicar após rollback preservador
            op.add_column('nfse_notas', column)

    # Só legado: leases/tentativas modernas sobrevivem a novo upgrade.
    conn.execute(sa.text("""
        UPDATE nfse_notas SET status = 'desconhecido', erro_tipo = 'desconhecido'
        WHERE tentativa_id IS NULL AND erro_tipo IS NULL
          AND status IN ('pending', 'processing', 'erro')
    """))
    if conn.dialect.name == 'postgresql':
        # Defesa também contra um escritor antigo no rollback/misto. Uma nota
        # autorizada pode receber campos auxiliares, nunca perder sua identidade.
        op.execute("""
        CREATE OR REPLACE FUNCTION nfse_preservar_estado_fiscal() RETURNS trigger AS $$
        BEGIN
          IF OLD.status = 'emitida' AND (
              NEW.status IS DISTINCT FROM OLD.status OR
              ROW(NEW.numero_nfse, NEW.serie_nfse, NEW.chave_acesso,
                  NEW.codigo_verificacao, NEW.numero_rps, NEW.serie_rps,
                  NEW.numero_lote, NEW.protocolo, NEW.xml_envio, NEW.xml_retorno)
              IS DISTINCT FROM
              ROW(OLD.numero_nfse, OLD.serie_nfse, OLD.chave_acesso,
                  OLD.codigo_verificacao, OLD.numero_rps, OLD.serie_rps,
                  OLD.numero_lote, OLD.protocolo, OLD.xml_envio, OLD.xml_retorno)
          ) THEN
            RAISE EXCEPTION 'NFS-e autorizada e evidências são imutáveis (nota %)', OLD.id;
          END IF;
          IF OLD.status = 'desconhecido' AND NEW.status = 'pending' THEN
            RAISE EXCEPTION 'NFS-e de desfecho desconhecido exige reconciliação (nota %)', OLD.id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """)
        op.execute('DROP TRIGGER IF EXISTS trg_nfse_estado_fiscal ON nfse_notas')
        op.execute("""CREATE TRIGGER trg_nfse_estado_fiscal BEFORE UPDATE ON nfse_notas
                      FOR EACH ROW EXECUTE FUNCTION nfse_preservar_estado_fiscal()""")


def downgrade():
    # Intencionalmente preservador: apagar campos ou desfazer desconhecido
    # reabriria o risco de duplicação. A imagem anterior lê as colunas antigas.
    # O Alembic atualiza alembic_version ao final; upgrade é idempotente.
    return
