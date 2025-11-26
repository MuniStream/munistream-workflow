"""
Context Signing Service

Handles preparation of workflow context for digital signing,
storage of signable data, and validation of received signatures.
"""
import json
import hashlib
import base64
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.exceptions import InvalidSignature

from ...models.workflow import WorkflowInstance
from ...core.database import get_database
from .signature_verifier import SignatureVerifier
from .certificate_manager import CertificateManager

logger = logging.getLogger(__name__)


class ContextSignerService:
    """Service for handling workflow context digital signatures"""

    @staticmethod
    async def store_signable_data(
        instance_id: str,
        signable_data: Dict[str, Any],
        signature_field: str,
        timeout_minutes: int = 30
    ) -> bool:
        """
        Store signable data for later client retrieval and signing.

        Args:
            instance_id: Workflow instance ID
            signable_data: Data prepared for signing
            signature_field: Field name where signature will be stored
            timeout_minutes: Timeout for signature process

        Returns:
            True if stored successfully
        """
        try:
            db = await get_database()

            # Find the workflow instance
            instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
            if not instance:
                logger.error(f"Workflow instance not found: {instance_id}")
                return False

            # Store signable data in instance context
            if not instance.context:
                instance.context = {}

            signature_info_key = f"_signature_pending_{signature_field}"
            instance.context[signature_info_key] = {
                "signable_data": signable_data,
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(minutes=timeout_minutes)).isoformat(),
                "status": "pending",
                "signature_field": signature_field
            }

            await instance.save()

            logger.info(f"Stored signable data for instance {instance_id}, field {signature_field}")
            return True

        except Exception as e:
            logger.error(f"Failed to store signable data: {e}")
            return False

    @staticmethod
    async def get_signable_data(
        instance_id: str,
        signature_field: str
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve signable data for client signing.

        Args:
            instance_id: Workflow instance ID
            signature_field: Signature field name

        Returns:
            Signable data or None if not found
        """
        try:
            instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
            if not instance or not instance.context:
                return None

            signature_info_key = f"_signature_pending_{signature_field}"
            signature_info = instance.context.get(signature_info_key)
            if not signature_info:
                return None

            # Check if expired
            expires_at = datetime.fromisoformat(signature_info["expires_at"])
            if datetime.utcnow() > expires_at:
                logger.warning(f"Signable data expired for instance {instance_id}")
                return None

            return signature_info["signable_data"]

        except Exception as e:
            logger.error(f"Failed to retrieve signable data: {e}")
            return None

    @staticmethod
    async def store_signature(
        instance_id: str,
        signature_field: str,
        signature_data: Dict[str, Any]
    ) -> bool:
        """
        Store received signature from client.

        Args:
            instance_id: Workflow instance ID
            signature_field: Field where signature should be stored
            signature_data: Signature data from client

        Returns:
            True if stored successfully
        """
        try:
            instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
            if not instance:
                logger.error(f"Workflow instance not found: {instance_id}")
                return False

            # Validate signature data structure
            required_fields = ["signature", "certificate", "algorithm"]
            for field in required_fields:
                if field not in signature_data:
                    logger.error(f"Missing required signature field: {field}")
                    return False

            # Extract certificate information
            cert_info = await CertificateManager.extract_certificate_info(
                signature_data["certificate"]
            )
            if not cert_info:
                logger.error("Failed to extract certificate information")
                return False

            # Add certificate info to signature data
            signature_data["certificate_info"] = cert_info
            signature_data["received_at"] = datetime.utcnow().isoformat()

            # Store in instance context
            if not instance.context:
                instance.context = {}

            instance.context[signature_field] = signature_data

            # Update signature tracking in context
            signature_info_key = f"_signature_pending_{signature_field}"
            if signature_info_key in instance.context:
                instance.context[signature_info_key]["status"] = "signed"
                instance.context[signature_info_key]["signed_at"] = datetime.utcnow().isoformat()

            await instance.save()

            logger.info(f"Stored signature for instance {instance_id}, field {signature_field}")
            return True

        except Exception as e:
            logger.error(f"Failed to store signature: {e}")
            return False

    @staticmethod
    async def validate_signature(
        signable_data: Dict[str, Any],
        signature_data: Dict[str, Any]
    ) -> bool:
        """
        Validate a digital signature against signable data.

        Args:
            signable_data: Original data that was signed
            signature_data: Signature data with certificate

        Returns:
            True if signature is valid
        """
        try:
            # Prepare the data for verification (same as signing)
            verification_data = dict(signable_data)

            # Remove any fields that shouldn't be included in signature verification
            verification_data.pop("data_hash", None)

            # Create canonical JSON representation
            data_json = json.dumps(verification_data, sort_keys=True)
            data_bytes = data_json.encode('utf-8')

            # Use SignatureVerifier to validate
            verifier = SignatureVerifier()
            is_valid = await verifier.verify_signature(
                data=data_bytes,
                signature_base64=signature_data["signature"],
                certificate_pem=signature_data["certificate"],
                algorithm=signature_data.get("algorithm", "RSA-SHA256")
            )

            if is_valid:
                logger.info("Signature validation successful")
            else:
                logger.warning("Signature validation failed")

            return is_valid

        except Exception as e:
            logger.error(f"Signature validation error: {e}")
            return False

    @staticmethod
    async def create_signature_hash(
        signable_data: Dict[str, Any],
        algorithm: str = "SHA256"
    ) -> str:
        """
        Create a hash of signable data for integrity checking.

        Args:
            signable_data: Data to hash
            algorithm: Hash algorithm

        Returns:
            Hex-encoded hash
        """
        try:
            # Create canonical JSON representation
            data_json = json.dumps(signable_data, sort_keys=True)
            data_bytes = data_json.encode('utf-8')

            if algorithm.upper() == "SHA256":
                hash_obj = hashlib.sha256(data_bytes)
            elif algorithm.upper() == "SHA512":
                hash_obj = hashlib.sha512(data_bytes)
            else:
                raise ValueError(f"Unsupported hash algorithm: {algorithm}")

            return hash_obj.hexdigest()

        except Exception as e:
            logger.error(f"Failed to create signature hash: {e}")
            return ""

    @staticmethod
    async def get_signature_status(
        instance_id: str,
        signature_field: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the status of a signature process.

        Args:
            instance_id: Workflow instance ID
            signature_field: Signature field name

        Returns:
            Status information or None
        """
        try:
            instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
            if not instance:
                return None

            # Check signature tracking data in context
            signature_info_key = f"_signature_pending_{signature_field}"
            signature_info = {}
            if instance.context and signature_info_key in instance.context:
                signature_info = instance.context.get(signature_info_key, {})

            # Check if signature exists in context
            signature_exists = (
                instance.context and
                signature_field in instance.context
            )

            status = {
                "signature_field": signature_field,
                "exists": signature_exists,
                "status": signature_info.get("status", "unknown"),
                "created_at": signature_info.get("created_at"),
                "expires_at": signature_info.get("expires_at"),
                "signed_at": signature_info.get("signed_at")
            }

            # Check expiration
            if signature_info.get("expires_at"):
                expires_at = datetime.fromisoformat(signature_info["expires_at"])
                status["expired"] = datetime.utcnow() > expires_at

            return status

        except Exception as e:
            logger.error(f"Failed to get signature status: {e}")
            return None

    @staticmethod
    async def cleanup_expired_signatures():
        """Clean up expired signature data from instances"""
        try:
            db = await get_database()

            # Find instances with expired signature data
            # This is a maintenance function that could be run periodically

            # Implementation would depend on specific cleanup requirements
            # For now, just log that cleanup would happen here
            logger.info("Signature cleanup process executed")

        except Exception as e:
            logger.error(f"Failed to cleanup expired signatures: {e}")

    @staticmethod
    async def _get_current_timestamp() -> str:
        """Get current timestamp in ISO format"""
        return datetime.utcnow().isoformat()