"""Assinatura da contratada usada nos PDFs de contrato."""
from __future__ import annotations

import base64
import io

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.models.system_setting import SystemSetting


SIGNATURE_SETTING_KEY = 'contract_signature_png_base64'
MAX_SIGNATURE_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_PIXELS = 4_000_000


def normalize_signature_image(content: bytes) -> bytes:
    """Aceita PNG/JPEG válido e guarda um PNG sem metadados ou código ativo."""
    if not content or len(content) > MAX_SIGNATURE_UPLOAD_BYTES:
        raise ValueError('A imagem da assinatura deve ter até 2 MB.')
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ('PNG', 'JPEG'):
                raise ValueError('Envie a assinatura em PNG ou JPG.')
            if source.width * source.height > MAX_SIGNATURE_PIXELS:
                raise ValueError('A imagem da assinatura tem dimensões grandes demais.')
            source.load()
            image = source.convert('RGBA' if 'A' in source.getbands() else 'RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Não foi possível ler a imagem da assinatura.') from exc
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()


def get_contract_signature(db: Session) -> bytes | None:
    row = db.query(SystemSetting).filter(SystemSetting.key == SIGNATURE_SETTING_KEY).first()
    return base64.b64decode(row.value) if row else None


def save_contract_signature(db: Session, content: bytes) -> None:
    value = base64.b64encode(normalize_signature_image(content)).decode('ascii')
    row = db.query(SystemSetting).filter(SystemSetting.key == SIGNATURE_SETTING_KEY).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=SIGNATURE_SETTING_KEY, value=value))
    db.commit()


def delete_contract_signature(db: Session) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == SIGNATURE_SETTING_KEY).first()
    if row:
        db.delete(row)
        db.commit()
