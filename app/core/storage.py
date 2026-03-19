import uuid

import boto3
from botocore.config import Config

from app.core.config import settings


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_file(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    folder: str = "attachments",
) -> str:
    """
    Uploads file to R2, returns the public URL.
    Key format: {folder}/{uuid}_{filename}
    """
    client = get_r2_client()
    key = f"{folder}/{uuid.uuid4()}_{filename}"
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    return f"{settings.R2_PUBLIC_URL}/{key}"
