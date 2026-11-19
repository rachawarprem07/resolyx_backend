import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.storage_service import storage_service


def main():
    print("=" * 60)
    print("Resolyx AWS S3 Connection Test")
    print("=" * 60)
    print(f"Storage Provider  : {settings.STORAGE_PROVIDER}")
    print(f"AWS Access Key ID : {settings.AWS_ACCESS_KEY_ID or '(not set)'}")
    print(
        f"AWS Secret Key    : {'*' * 8 + settings.AWS_SECRET_ACCESS_KEY[-4:] if settings.AWS_SECRET_ACCESS_KEY else '(not set)'}"
    )
    print(f"AWS Region        : {settings.AWS_REGION}")
    print(f"S3 Bucket Name    : {settings.S3_BUCKET_NAME or '(not set)'}")
    if settings.S3_ENDPOINT_URL:
        print(f"Endpoint URL      : {settings.S3_ENDPOINT_URL}")
    if settings.S3_PUBLIC_URL_PREFIX:
        print(f"Public CDN Prefix : {settings.S3_PUBLIC_URL_PREFIX}")
    print("-" * 60)

    if not storage_service.is_s3_configured():
        print("[!] S3 credentials are NOT fully set in backend/.env yet.")
        print("Please configure the following in backend/.env:")
        print("  AWS_ACCESS_KEY_ID=AKIA...")
        print("  AWS_SECRET_ACCESS_KEY=...")
        print("  AWS_REGION=us-east-1")
        print("  S3_BUCKET_NAME=your-bucket-name")
        sys.exit(1)

    print("Testing connection to AWS S3...")
    success, message = storage_service.test_connection()
    if success:
        print(f"[SUCCESS] {message}")
        sys.exit(0)
    else:
        print(f"[FAILED] {message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
