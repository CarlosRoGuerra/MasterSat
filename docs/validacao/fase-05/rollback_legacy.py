"""Executado com código/imagem anteriores; somente leitura e UPDATE recusado."""
from sqlalchemy import text
from app.main import _apply_database_migrations
from app.db.session import SessionLocal, engine
from app.models.nfse_nota import NfseNota
from app.schemas.nfse import NfseOut

_apply_database_migrations()  # carimbo voltou à revisão que a imagem conhece
with SessionLocal() as db:
    notas = db.query(NfseNota).order_by(NfseNota.id).all()
    estados = [NfseOut.model_validate(n).status for n in notas]
    assert estados == ['emitida', 'desconhecido']
    print('Imagem anterior: boot de migrations e serialização compatíveis:', estados)
for estado in ('emitida', 'desconhecido'):
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE nfse_notas SET status='pending' WHERE status=:estado"), {'estado': estado})
    except Exception as exc:
        assert 'NFS-e' in str(exc)
        print('Escritor antigo bloqueado antes de reenviar:', estado)
    else:
        raise AssertionError('Regressão fiscal não bloqueada')
