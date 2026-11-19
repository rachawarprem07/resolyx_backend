import logging
from typing import Optional
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, status
from pydantic import BaseModel, Field

from app.services.storage_service import storage_service

logger = logging.getLogger("resolva")
router = APIRouter()


class PresignedUrlRequest(BaseModel):
    filename: str = Field(..., description="Name of the file to upload")
    content_type: str = Field("application/octet-stream", description="MIME content type")
    folder: str = Field("kb-media", description="Target directory/prefix")


@router.get(
    "/status",
    summary="Get Cloud Storage / S3 Status",
)
def get_storage_status():
    """Returns the current S3 storage status, active provider, bucket, and region configured in .env."""
    return storage_service.get_status()


@router.get(
    "/test",
    summary="Test S3 Connection from Environment Configuration",
)
@router.post(
    "/test",
    summary="Test S3 Connection from Environment Configuration",
)
def test_s3_connection():
    """
    Tests live read, write, and delete permissions on the S3 bucket using
    the AWS credentials configured in the environment (.env).
    """
    success, message = storage_service.test_connection()
    return {
        "success": success,
        "message": message,
        "status": storage_service.get_status(),
    }


@router.post(
    "/upload",
    summary="Upload Media / File to Cloud Storage",
)
async def upload_file(
    file: UploadFile = File(...),
    folder: str = Form("kb-media"),
):
    """
    Uploads a file (video, audio, document, image, or archive) to AWS S3.
    Returns the public URL, file size, MIME type, and storage provider.
    """
    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        result = storage_service.upload_file(
            file_bytes=file_bytes,
            filename=file.filename or "upload.bin",
            content_type=file.content_type or "application/octet-stream",
            folder=folder,
        )
        return result
    except Exception as e:
        logger.error("File upload failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}",
        )


@router.post(
    "/presigned-url",
    summary="Generate Presigned URL for Direct S3 Upload",
)
def get_presigned_url(payload: PresignedUrlRequest):
    """
    Generates a secure presigned S3 PUT URL for uploading massive videos/files directly from the client.
    """
    try:
        return storage_service.generate_presigned_upload(
            filename=payload.filename,
            content_type=payload.content_type,
            folder=payload.folder,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
