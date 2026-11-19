import os
import re
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError

from app.core.config import settings

logger = logging.getLogger("resolva")

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


def sanitize_filename(filename: str) -> str:
    """Removes unsafe characters from filename while preserving extension."""
    base, ext = os.path.splitext(filename)
    clean_base = re.sub(r"[^a-zA-Z0-9_\-]", "_", base)[:50]
    clean_ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:10]
    return f"{clean_base}{clean_ext}"


class StorageService:
    def __init__(self):
        self.upload_dir = UPLOAD_DIR
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def _get_s3_client(self, override_creds: Optional[Dict[str, str]] = None):
        """Creates and returns a boto3 S3 client using settings or overrides."""
        access_key = (
            override_creds.get("access_key_id")
            if override_creds
            else settings.AWS_ACCESS_KEY_ID
        )
        secret_key = (
            override_creds.get("secret_access_key")
            if override_creds
            else settings.AWS_SECRET_ACCESS_KEY
        )
        region = (
            override_creds.get("region")
            if override_creds
            else (settings.AWS_REGION or "us-east-1")
        )
        endpoint_url = (
            override_creds.get("endpoint_url")
            if override_creds
            else settings.S3_ENDPOINT_URL
        )

        if not access_key or not secret_key:
            return None

        client_kwargs: Dict[str, Any] = {
            "service_name": "s3",
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
            "config": Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }

        if endpoint_url and endpoint_url.strip():
            client_kwargs["endpoint_url"] = endpoint_url.strip()

        return boto3.client(**client_kwargs)

    def is_s3_configured(self) -> bool:
        """Checks if minimum S3 credentials and bucket name are set."""
        return bool(
            settings.AWS_ACCESS_KEY_ID
            and settings.AWS_SECRET_ACCESS_KEY
            and settings.S3_BUCKET_NAME
        )

    def get_status(self) -> Dict[str, Any]:
        """Returns the current storage configuration and connectivity status."""
        configured = self.is_s3_configured()
        if not configured:
            return {
                "provider": "local" if settings.STORAGE_PROVIDER == "local" else "s3",
                "configured": False,
                "connected": False,
                "bucket": settings.S3_BUCKET_NAME or "",
                "region": settings.AWS_REGION or "us-east-1",
                "message": "AWS S3 credentials or bucket name are not configured yet. Fallback local storage is active.",
            }

        client = self._get_s3_client()
        if not client:
            return {
                "provider": "s3",
                "configured": False,
                "connected": False,
                "bucket": settings.S3_BUCKET_NAME or "",
                "region": settings.AWS_REGION or "us-east-1",
                "message": "Invalid AWS credentials in configuration.",
            }

        try:
            client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
            return {
                "provider": "s3",
                "configured": True,
                "connected": True,
                "bucket": settings.S3_BUCKET_NAME,
                "region": settings.AWS_REGION,
                "message": f"Successfully connected to AWS S3 bucket '{settings.S3_BUCKET_NAME}' in region '{settings.AWS_REGION}'.",
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            logger.warning("S3 health check failed (%s): %s", error_code, error_msg)
            return {
                "provider": "s3",
                "configured": True,
                "connected": False,
                "bucket": settings.S3_BUCKET_NAME,
                "region": settings.AWS_REGION,
                "error_code": error_code,
                "message": f"S3 Connection Error [{error_code}]: {error_msg}",
            }
        except Exception as e:
            return {
                "provider": "s3",
                "configured": True,
                "connected": False,
                "bucket": settings.S3_BUCKET_NAME,
                "region": settings.AWS_REGION,
                "message": f"Failed to reach AWS S3: {str(e)}",
            }

    def test_connection(
        self,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Runs an active ping test: writes a test object to S3, reads it, and cleans it up.
        Accepts optional parameter overrides for live testing from Admin UI.
        """
        bucket = bucket_name or settings.S3_BUCKET_NAME
        reg = region or settings.AWS_REGION or "us-east-1"
        overrides = {
            "access_key_id": access_key_id or settings.AWS_ACCESS_KEY_ID or "",
            "secret_access_key": secret_access_key or settings.AWS_SECRET_ACCESS_KEY or "",
            "region": reg,
            "endpoint_url": endpoint_url or settings.S3_ENDPOINT_URL or "",
        }

        if not overrides["access_key_id"] or not overrides["secret_access_key"]:
            return False, "AWS Access Key ID and Secret Access Key are required."
        if not bucket:
            return False, "S3 Bucket Name is required."

        client = self._get_s3_client(override_creds=overrides)
        if not client:
            return False, "Could not initialize AWS S3 client with provided credentials."

        test_key = f"_health_check/ping_{uuid.uuid4().hex[:8]}.txt"
        test_payload = b"Resolyx S3 Connection Test - OK"

        try:
            # 1. Test Write
            client.put_object(
                Bucket=bucket,
                Key=test_key,
                Body=test_payload,
                ContentType="text/plain",
            )

            # 2. Test Read
            response = client.get_object(Bucket=bucket, Key=test_key)
            data = response["Body"].read()
            if data != test_payload:
                return False, "Data verification failed during read test."

            # 3. Clean up
            try:
                client.delete_object(Bucket=bucket, Key=test_key)
            except Exception:
                pass

            return True, f"Verified! Successfully performed Read, Write, and Delete operations on bucket '{bucket}' in '{reg}'."
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            msg = e.response.get("Error", {}).get("Message", str(e))
            if code == "NoSuchBucket":
                return False, f"Bucket '{bucket}' does not exist in region '{reg}'. Please create it in AWS first."
            if code == "AccessDenied":
                return False, "Access Denied: IAM credentials lack s3:PutObject / s3:GetObject permissions on this bucket."
            return False, f"AWS Error [{code}]: {msg}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        folder: str = "kb-media",
    ) -> Dict[str, Any]:
        """
        Uploads file to AWS S3 if configured, or falls back gracefully to local storage.
        Returns a dictionary with public URL, key, size, and metadata.
        """
        clean_name = sanitize_filename(filename)
        file_uuid = uuid.uuid4().hex[:12]
        key = f"{folder}/{file_uuid}_{clean_name}"
        size = len(file_bytes)

        if self.is_s3_configured():
            client = self._get_s3_client()
            if client:
                try:
                    bucket = settings.S3_BUCKET_NAME
                    client.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=file_bytes,
                        ContentType=content_type,
                        ContentDisposition=f'inline; filename="{clean_name}"',
                    )

                    # Determine public URL
                    if settings.S3_PUBLIC_URL_PREFIX and settings.S3_PUBLIC_URL_PREFIX.strip():
                        public_url = f"{settings.S3_PUBLIC_URL_PREFIX.rstrip('/')}/{key}"
                    elif settings.S3_ENDPOINT_URL and settings.S3_ENDPOINT_URL.strip():
                        public_url = f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{bucket}/{key}"
                    else:
                        region = settings.AWS_REGION or "us-east-1"
                        if region == "us-east-1":
                            public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
                        else:
                            public_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

                    logger.info("Uploaded %s to S3 (%s): %s", clean_name, key, public_url)
                    return {
                        "url": public_url,
                        "key": key,
                        "bucket": bucket,
                        "provider": "s3",
                        "filename": clean_name,
                        "size": size,
                        "content_type": content_type,
                    }
                except Exception as e:
                    logger.error("Failed to upload to S3, falling back to local storage: %s", e)

        # Fallback to local file storage
        target_dir = self.upload_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        local_filename = f"{file_uuid}_{clean_name}"
        local_path = target_dir / local_filename

        with open(local_path, "wb") as f:
            f.write(file_bytes)

        local_url = f"/uploads/{folder}/{local_filename}"
        logger.info("Saved %s locally: %s", clean_name, local_url)
        return {
            "url": local_url,
            "key": f"{folder}/{local_filename}",
            "bucket": "local-disk",
            "provider": "local",
            "filename": clean_name,
            "size": size,
            "content_type": content_type,
        }

    def generate_presigned_upload(
        self,
        filename: str,
        content_type: str = "application/octet-stream",
        folder: str = "kb-media",
        expires_in: int = 3600,
    ) -> Dict[str, Any]:
        """
        Generates a direct presigned PUT URL for browser-to-S3 uploads (ideal for huge video/audio files).
        """
        if not self.is_s3_configured():
            raise ValueError("S3 is not configured. Cannot generate presigned upload URL.")

        client = self._get_s3_client()
        if not client:
            raise ValueError("Failed to initialize S3 client.")

        clean_name = sanitize_filename(filename)
        file_uuid = uuid.uuid4().hex[:12]
        key = f"{folder}/{file_uuid}_{clean_name}"
        bucket = settings.S3_BUCKET_NAME

        presigned_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

        region = settings.AWS_REGION or "us-east-1"
        if settings.S3_PUBLIC_URL_PREFIX and settings.S3_PUBLIC_URL_PREFIX.strip():
            public_url = f"{settings.S3_PUBLIC_URL_PREFIX.rstrip('/')}/{key}"
        elif region == "us-east-1":
            public_url = f"https://{bucket}.s3.amazonaws.com/{key}"
        else:
            public_url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

        return {
            "upload_url": presigned_url,
            "public_url": public_url,
            "key": key,
            "bucket": bucket,
            "filename": clean_name,
        }


storage_service = StorageService()
