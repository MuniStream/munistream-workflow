"""
S3Upload Operator for uploading files to Amazon S3.
Handles file uploads from citizen portals and stores them in S3 buckets.
"""
import os
import boto3
import hashlib
import mimetypes
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
from pathlib import Path
import tempfile
import aiofiles
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .base import BaseOperator, TaskResult
from ...core.config import settings


class S3UploadOperator(BaseOperator):
    """
    Operator for uploading files to Amazon S3.

    This operator handles:
    - Single or multiple file uploads
    - Automatic content type detection
    - Metadata tagging
    - Public/private access control
    - Progress tracking
    - Error handling with retry logic
    """

    def __init__(
        self,
        task_id: str,
        bucket_name: Optional[str] = None,
        s3_prefix: str = "",
        file_source: str = "uploaded_files",  # Context key containing file data
        make_public: bool = False,
        metadata_tags: Optional[Dict[str, str]] = None,
        content_type: Optional[str] = None,
        storage_class: str = "STANDARD",
        server_side_encryption: str = "AES256",
        acl: Optional[str] = None,
        max_file_size: int = 100 * 1024 * 1024,  # 100MB default
        allowed_extensions: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize S3Upload operator.

        Args:
            task_id: Unique task identifier
            bucket_name: S3 bucket name (uses settings default if not provided)
            s3_prefix: Prefix/folder path in S3 bucket
            file_source: Context key containing file(s) to upload
            make_public: Whether to make files publicly readable
            metadata_tags: Additional metadata to attach to S3 objects
            content_type: Override content type (auto-detected if not provided)
            storage_class: S3 storage class (STANDARD, REDUCED_REDUNDANCY, etc.)
            server_side_encryption: Encryption method (AES256, aws:kms)
            acl: S3 ACL (private, public-read, public-read-write, etc.)
            max_file_size: Maximum allowed file size in bytes
            allowed_extensions: List of allowed file extensions
        """
        super().__init__(task_id, **kwargs)
        self.bucket_name = bucket_name or os.getenv("S3_BUCKET_NAME", "munistream-uploads")
        self.s3_prefix = s3_prefix
        self.file_source = file_source
        self.make_public = make_public
        self.metadata_tags = metadata_tags or {}
        self.content_type = content_type
        self.storage_class = storage_class
        self.server_side_encryption = server_side_encryption
        self.acl = acl or ("public-read" if make_public else "private")
        self.max_file_size = max_file_size
        self.allowed_extensions = allowed_extensions or [
            '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.bmp',
            '.doc', '.docx', '.xls', '.xlsx', '.csv', '.txt',
            '.zip', '.rar', '.7z', '.json', '.xml'
        ]

        # S3 client will be initialized during execution
        self.s3_client = None
        self.executor = ThreadPoolExecutor(max_workers=5)

    def _init_s3_client(self) -> boto3.client:
        """Initialize S3 client with credentials from environment variables"""
        # Environment variables are automatically read by boto3:
        # AWS_ACCESS_KEY_ID
        # AWS_SECRET_ACCESS_KEY
        # AWS_DEFAULT_REGION

        aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        # Support for custom S3 endpoints (e.g., MinIO, LocalStack)
        endpoint_url = os.getenv("S3_ENDPOINT_URL")

        # boto3 automatically reads AWS credentials from environment variables
        # or IAM role if running on EC2/ECS/Lambda
        return boto3.client(
            's3',
            region_name=aws_region,
            endpoint_url=endpoint_url
        )

    def _validate_file(self, file_data: Dict[str, Any]) -> Optional[str]:
        """
        Validate file before upload.

        Args:
            file_data: Dictionary containing file information

        Returns:
            Error message if validation fails, None if valid
        """
        # Check file size
        file_size = file_data.get("size", 0)
        if file_size > self.max_file_size:
            return f"File size {file_size} exceeds maximum allowed size {self.max_file_size}"

        # Check file extension
        filename = file_data.get("filename", "")
        file_ext = Path(filename).suffix.lower()
        if self.allowed_extensions and file_ext not in self.allowed_extensions:
            return f"File extension {file_ext} not allowed. Allowed: {', '.join(self.allowed_extensions)}"

        return None

    def _generate_s3_key(self, filename: str, context: Dict[str, Any]) -> str:
        """
        Generate S3 object key with proper path structure.

        Args:
            filename: Original filename
            context: Execution context

        Returns:
            S3 object key
        """
        # Build path components
        path_parts = []

        # Add prefix if provided
        if self.s3_prefix:
            # Support template variables in prefix
            prefix = self.s3_prefix
            for key, value in context.items():
                prefix = prefix.replace(f"{{{key}}}", str(value))
            path_parts.append(prefix.strip("/"))

        # Add date-based organization
        now = datetime.utcnow()
        path_parts.extend([
            str(now.year),
            f"{now.month:02d}",
            f"{now.day:02d}"
        ])

        # Add instance ID if available
        if "instance_id" in context:
            path_parts.append(context["instance_id"])

        # Generate unique filename to prevent collisions
        file_hash = hashlib.md5(f"{filename}{now.isoformat()}".encode()).hexdigest()[:8]
        name_parts = filename.rsplit(".", 1)
        if len(name_parts) == 2:
            unique_filename = f"{name_parts[0]}_{file_hash}.{name_parts[1]}"
        else:
            unique_filename = f"{filename}_{file_hash}"

        path_parts.append(unique_filename)

        return "/".join(path_parts)

    def _get_files_from_context(self, context: Dict[str, Any], file_source: str) -> List[Dict[str, Any]]:
        """
        Get files from context with support for both direct keys and task output data.

        This method handles the common pattern where UserInputOperator stores files
        in task output data (e.g., collect_document_data) but S3UploadOperator
        needs to access them by field name.
        """
        # First try direct access
        files_data = context.get(file_source)
        if files_data:
            return files_data if isinstance(files_data, list) else [files_data]

        # If not found directly, look in task output data from UserInputOperator
        # Check all task output data for the file field
        for key, value in context.items():
            if key.endswith('_data') and isinstance(value, dict):
                if file_source in value:
                    file_data = value[file_source]
                    return file_data if isinstance(file_data, list) else [file_data]

        # Debug: print available context keys to help diagnose issues
        print(f"🔍 S3UploadOperator: No files found for '{file_source}'")
        print(f"   Available context keys: {list(context.keys())}")
        for key, value in context.items():
            if isinstance(value, dict) and any(k for k in value.keys() if 'file' in k.lower()):
                print(f"   Nested data in '{key}': {list(value.keys())}")

        return []

    def _upload_file_to_s3(
        self,
        file_content: bytes,
        s3_key: str,
        content_type: str,
        metadata: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Perform the actual S3 upload.

        Args:
            file_content: File content as bytes
            s3_key: S3 object key
            content_type: MIME content type
            metadata: Metadata dictionary

        Returns:
            Upload result dictionary
        """
        print(f"      Starting S3 upload to bucket: {self.bucket_name}")
        print(f"      S3 key: {s3_key}")
        print(f"      Content size: {len(file_content)} bytes")
        try:
            # Prepare upload parameters
            put_params = {
                'Bucket': self.bucket_name,
                'Key': s3_key,
                'Body': file_content,
                'ContentType': content_type,
                'Metadata': metadata,
                'StorageClass': self.storage_class,
                'ACL': self.acl
            }

            # Only add ServerSideEncryption for real AWS S3 (not MinIO)
            endpoint_url = os.getenv("S3_ENDPOINT_URL")
            if not endpoint_url or "amazonaws.com" in endpoint_url:
                put_params['ServerSideEncryption'] = self.server_side_encryption

            # Upload to S3
            print(f"      Calling S3 put_object...")
            response = self.s3_client.put_object(**put_params)
            print(f"      S3 upload successful! ETag: {response.get('ETag', 'unknown')}")

            # Generate URL
            if self.make_public:
                url = f"https://{self.bucket_name}.s3.amazonaws.com/{s3_key}"
            else:
                # Generate presigned URL (valid for 7 days)
                url = self.s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': self.bucket_name, 'Key': s3_key},
                    ExpiresIn=604800  # 7 days
                )

            return {
                "success": True,
                "s3_key": s3_key,
                "bucket": self.bucket_name,
                "url": url,
                "etag": response.get('ETag', '').strip('"'),
                "version_id": response.get('VersionId'),
                "size": len(file_content)
            }

        except Exception as e:
            print(f"      ❌ S3 upload failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "s3_key": s3_key
            }

    async def _process_file_upload(
        self,
        file_data: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process a single file upload asynchronously.

        Args:
            file_data: File information dictionary
            context: Execution context

        Returns:
            Upload result
        """
        filename = file_data.get("filename", "unknown")
        print(f"🔍 Processing file: {filename}")
        print(f"   File data keys: {list(file_data.keys())}")
        print(f"   File size: {file_data.get('size', 'unknown')}")
        print(f"   Content type: {file_data.get('content_type', 'unknown')}")

        # Validate file
        error = self._validate_file(file_data)
        if error:
            print(f"❌ File validation failed: {error}")
            return {
                "filename": filename,
                "success": False,
                "error": error
            }

        # Get file content - check both 'content' and 'base64' keys
        file_content = file_data.get("content") or file_data.get("base64")
        print(f"   Content type: {type(file_content)}")
        print(f"   Content length: {len(file_content) if file_content else 'None'}")

        if isinstance(file_content, str):
            # Base64 encoded content
            print(f"   Decoding base64 content...")
            import base64
            try:
                file_content = base64.b64decode(file_content)
                print(f"   Decoded to {len(file_content)} bytes")
            except Exception as e:
                print(f"❌ Base64 decode failed: {e}")
                return {
                    "filename": filename,
                    "success": False,
                    "error": f"Failed to decode base64 content: {str(e)}"
                }
        elif not isinstance(file_content, bytes):
            # Try to read from file path
            file_path = file_data.get("path")
            print(f"   Trying to read from file path: {file_path}")
            if file_path and os.path.exists(file_path):
                async with aiofiles.open(file_path, 'rb') as f:
                    file_content = await f.read()
                print(f"   Read {len(file_content)} bytes from file")
            else:
                print(f"❌ No valid file content or path provided")
                print(f"   Available file_data keys: {list(file_data.keys())}")
                return {
                    "filename": filename,
                    "success": False,
                    "error": "No valid file content or path provided"
                }

        # Generate S3 key
        s3_key = self._generate_s3_key(filename, context)

        # Detect content type
        content_type = self.content_type or file_data.get("content_type")
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"

        # Prepare metadata
        metadata = {
            **self.metadata_tags,
            "original-filename": filename,
            "upload-timestamp": datetime.utcnow().isoformat(),
            "instance-id": context.get("instance_id", ""),
            "user-id": context.get("user_id", context.get("customer_id", "")),
            "workflow-id": context.get("workflow_id", "")
        }

        # Remove empty metadata values
        metadata = {k: v for k, v in metadata.items() if v}

        # Upload to S3 in thread pool to avoid blocking
        print(f"   Uploading to S3: {s3_key}")
        print(f"   Content size: {len(file_content)} bytes")
        print(f"   Content type: {content_type}")
        print(f"   Metadata: {metadata}")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            self._upload_file_to_s3,
            file_content,
            s3_key,
            content_type,
            metadata
        )

        print(f"   S3 upload result: {result}")
        result["filename"] = filename
        return result

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Execute method required by BaseOperator - delegates to execute_async"""
        return TaskResult(
            status="pending_async",
            data={}
        )

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for S3 file uploads"""
        try:
            print(f"☁️ S3UploadOperator: Starting file upload to {self.bucket_name}")

            # Initialize S3 client
            self.s3_client = self._init_s3_client()

            # Get files from context - support nested keys for task output data
            files_data = self._get_files_from_context(context, self.file_source)
            if not isinstance(files_data, list):
                files_data = [files_data]

            if not files_data:
                error_msg = f"No files found in context key '{self.file_source}'. Expected files for upload but none were provided."
                print(f"❌ S3UploadOperator: {error_msg}")
                return TaskResult(
                    status="failed",
                    error=error_msg
                )

            print(f"☁️ S3UploadOperator: Processing {len(files_data)} file(s) asynchronously")

            # Process uploads asynchronously
            tasks = [
                self._process_file_upload(file_data, context)
                for file_data in files_data
            ]
            results = await asyncio.gather(*tasks)

            # Process results
            successful_uploads = [r for r in results if r.get("success")]
            failed_uploads = [r for r in results if not r.get("success")]

            # Store results in context
            upload_summary = {
                "uploaded_files": successful_uploads,
                "failed_files": failed_uploads,
                "total_files": len(results),
                "successful_count": len(successful_uploads),
                "failed_count": len(failed_uploads),
                "bucket": self.bucket_name,
                "timestamp": datetime.utcnow().isoformat()
            }

            # Add S3 URLs to context for easy access
            if successful_uploads:
                upload_summary["s3_urls"] = [f["url"] for f in successful_uploads]
                upload_summary["s3_keys"] = [f["s3_key"] for f in successful_uploads]

            # Log results
            print(f"✅ S3UploadOperator: Uploaded {len(successful_uploads)}/{len(results)} files")
            if failed_uploads:
                print(f"⚠️ S3UploadOperator: Failed uploads: {[f.get('filename', 'unknown') for f in failed_uploads]}")

            # Determine status based on results
            if not successful_uploads:
                return TaskResult(
                    status="failed",
                    error=f"All {len(failed_uploads)} uploads failed",
                    data=upload_summary
                )
            elif failed_uploads:
                return TaskResult(
                    status="continue",
                    data=upload_summary,
                    metadata={"warning": f"{len(failed_uploads)} uploads failed"}
                )
            else:
                return TaskResult(
                    status="continue",
                    data=upload_summary
                )

        except Exception as e:
            import traceback
            error_msg = f"S3 upload failed: {str(e)}"
            print(f"❌ S3UploadOperator: {error_msg}")
            print(f"   Full traceback:")
            traceback.print_exc()
            print(f"   Error occurred at line: {traceback.extract_tb(e.__traceback__)[-1].lineno}")
            return TaskResult(
                status="failed",
                error=error_msg
            )

        finally:
            # Cleanup
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=False)