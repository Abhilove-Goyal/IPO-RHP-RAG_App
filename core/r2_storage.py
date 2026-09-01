from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from core.settings import settings


class R2ConfigurationError(RuntimeError):
    """Raised when required R2 configuration is unavailable."""


_client = None


def _require_settings() -> None:
    required = {
        "R2_ACCOUNT_ID": settings.r2_account_id,
        "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
        "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
        "R2_BUCKET_NAME": settings.r2_bucket_name,
        "R2_ENDPOINT_URL": settings.r2_endpoint_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise R2ConfigurationError(f"Missing R2 configuration: {', '.join(missing)}")


def _get_client():
    global _client
    if _client is None:
        _require_settings()
        try:
            import boto3
        except ImportError as exc:
            raise R2ConfigurationError("boto3 is required for R2 storage operations.") from exc
        _client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )
    return _client


def configuration_status() -> dict[str, bool]:
    """Return R2 configuration presence flags without exposing secret values."""
    return {
        "R2_ACCOUNT_ID": bool(settings.r2_account_id),
        "R2_ACCESS_KEY_ID": bool(settings.r2_access_key_id),
        "R2_SECRET_ACCESS_KEY": bool(settings.r2_secret_access_key),
        "R2_BUCKET_NAME": bool(settings.r2_bucket_name),
        "R2_ENDPOINT_URL": bool(settings.r2_endpoint_url),
    }


def upload_file(file_path: str | Path, object_key: str, content_type: str | None = None) -> str:
    extra_args = {"ContentType": content_type} if content_type else None
    kwargs = {"Filename": str(file_path), "Bucket": settings.r2_bucket_name, "Key": object_key}
    if extra_args:
        kwargs["ExtraArgs"] = extra_args
    _get_client().upload_file(**kwargs)
    return object_key


def upload_bytes(data: bytes, object_key: str, content_type: str | None = None) -> str:
    extra_args = {"ContentType": content_type} if content_type else None
    kwargs = {"Fileobj": BytesIO(data), "Bucket": settings.r2_bucket_name, "Key": object_key}
    if extra_args:
        kwargs["ExtraArgs"] = extra_args
    _get_client().upload_fileobj(**kwargs)
    return object_key


def download_file(object_key: str, destination: str | Path) -> Path:
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    _get_client().download_file(settings.r2_bucket_name, object_key, str(destination_path))
    return destination_path


def download_bytes(object_key: str) -> bytes:
    response = _get_client().get_object(Bucket=settings.r2_bucket_name, Key=object_key)
    return response["Body"].read()


def delete_object(object_key: str) -> None:
    _get_client().delete_object(Bucket=settings.r2_bucket_name, Key=object_key)


def object_exists(object_key: str) -> bool:
    try:
        _get_client().head_object(Bucket=settings.r2_bucket_name, Key=object_key)
        return True
    except Exception as exc:
        try:
            from botocore.exceptions import ClientError
        except ImportError:
            raise
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def document_object_key(document_id: str, filename: str) -> str:
    return f"documents/{document_id}/original/{Path(filename).name}"


def asset_object_key(document_id: str, asset_id: str, filename: str) -> str:
    return f"documents/{document_id}/assets/{asset_id}/{Path(filename).name}"
