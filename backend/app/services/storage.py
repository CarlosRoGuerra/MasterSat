from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from minio import Minio

from app.core.config import settings


client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_root_user,
    secret_key=settings.minio_root_password,
    secure=settings.minio_secure,
)


def ensure_bucket() -> None:
    found = client.bucket_exists(settings.minio_bucket)
    if not found:
        client.make_bucket(settings.minio_bucket)


def upload_bytes(object_name: str, content: bytes, content_type: str) -> str:
    data = BytesIO(content)
    client.put_object(
        bucket_name=settings.minio_bucket,
        object_name=object_name,
        data=data,
        length=len(content),
        content_type=content_type,
    )
    return object_name


def remove_object(object_name: str) -> None:
    client.remove_object(settings.minio_bucket, object_name)


def get_object_stream(object_name: str):
    return client.get_object(settings.minio_bucket, object_name)


def get_presigned_url(object_name: str, expires_hours: int = 2) -> str:
    return client.presigned_get_object(
        settings.minio_bucket,
        object_name,
        expires=timedelta(hours=expires_hours),
    )
