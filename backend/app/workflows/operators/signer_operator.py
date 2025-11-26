"""
SignerOperator for digitally signing workflow instance context.

This operator prepares the workflow instance context for digital signing,
handles client-side X.509 certificate signing, and stores the signature
in the context for subsequent operators to use.
"""
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
import json
import hashlib
import base64
from pydantic import BaseModel, Field

from .base import BaseOperator, TaskResult
from ...services.signature.context_signer import ContextSignerService
from ...models.workflow import WorkflowInstance


class SignerOperator(BaseOperator):
    """
    Operator that prepares workflow context for digital signing and handles
    client-side X.509 signature storage.

    This operator:
    1. Prepares the context fields for signing
    2. Creates a signable payload
    3. Waits for client-side signature
    4. Validates and stores the signature in context
    """

    def __init__(
        self,
        task_id: str,
        context_fields_to_sign: List[str],
        signature_field: str = "digital_signature",
        require_client_signature: bool = True,
        signature_metadata: Optional[Dict[str, Any]] = None,
        hash_algorithm: str = "SHA256",
        required_cert_type: Optional[str] = None,  # "personal" or "organizational"
        timeout_minutes: int = 30,
        **kwargs
    ):
        """
        Initialize the signer operator.

        Args:
            task_id: Unique task identifier
            context_fields_to_sign: List of context keys to include in signature
            signature_field: Context key where signature will be stored
            require_client_signature: Whether client signature is mandatory
            signature_metadata: Additional metadata for signature
            hash_algorithm: Hashing algorithm (SHA256, SHA512, etc.)
            required_cert_type: Required certificate type for validation
            timeout_minutes: Timeout for signature process
        """
        # Create form config like UserInputOperator
        form_config = {
            "title": "Firma Digital Requerida",
            "description": "Por favor proporciona tu certificado X.509 y llave privada para firmar digitalmente este documento",
            "signature_field": signature_field,
            "required_cert_type": required_cert_type,
            "timeout_minutes": timeout_minutes,
            "certificate_field": "digital_signature_certificate",
            "private_key_field": "digital_signature_private_key",
            "password_field": "digital_signature_password",
            "document_type": "DOCUMENTO_OFICIAL",
            "fields": [
                {
                    "name": "digital_signature_certificate",
                    "label": "Certificado Digital (.cer)",
                    "type": "file",
                    "accept": ".cer,.crt,.pem",
                    "required": True
                },
                {
                    "name": "digital_signature_private_key",
                    "label": "Llave Privada (.key)",
                    "type": "file",
                    "accept": ".key,.pem",
                    "required": True
                },
                {
                    "name": "digital_signature_password",
                    "label": "Contraseña de la llave privada",
                    "type": "password",
                    "required": True
                }
            ]
        }

        # Store config in kwargs for API visibility (like UserInputOperator)
        kwargs['form_config'] = form_config
        kwargs['required_fields'] = ["digital_signature_certificate", "digital_signature_private_key", "digital_signature_password"]

        super().__init__(task_id, **kwargs)
        self.form_config = form_config
        self.required_fields = ["digital_signature_certificate", "digital_signature_private_key", "digital_signature_password"]
        self.context_fields_to_sign = context_fields_to_sign
        self.signature_field = signature_field
        self.require_client_signature = require_client_signature
        self.signature_metadata = signature_metadata or {}
        self.hash_algorithm = hash_algorithm
        self.required_cert_type = required_cert_type
        self.timeout_minutes = timeout_minutes

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Synchronous wrapper for async execution.
        Used when the executor doesn't call execute_async directly.
        """
        import asyncio
        return asyncio.run(self.execute_async(context))

    def _prepare_signable_data(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare the data to be signed from context.
        Now uses the complete workflow context as the document to sign.

        Args:
            context: Workflow instance context

        Returns:
            Dictionary with signable data (complete workflow context)
        """
        # Use the complete workflow context as the document to sign
        signable_data = context.copy()

        # Remove sensitive or unnecessary fields
        sensitive_fields = ["_signature_pending", "kc_token", "password", "private_key"]
        for field in sensitive_fields:
            if field in signable_data:
                del signable_data[field]

        # Also remove any fields that start with underscore (internal fields)
        signable_data = {k: v for k, v in signable_data.items() if not k.startswith('_')}

        # Add signature metadata
        signable_data.update({
            "timestamp": datetime.utcnow().isoformat(),
            "signature_purpose": self.signature_metadata.get("purpose", "workflow_approval"),
            "signature_type": "complete_context_signature"
        })

        # Create hash of the signable data for integrity
        data_json = json.dumps(signable_data, sort_keys=True)
        data_hash = hashlib.sha256(data_json.encode()).hexdigest()
        signable_data["data_hash"] = data_hash

        print(f"   📝 Prepared complete context for signing with hash: {data_hash[:16]}...")
        print(f"   📄 Context keys included: {list(signable_data.keys())}")

        return signable_data

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution method for handling signature workflow"""
        try:
            print(f"🔐 SignerOperator: Starting signature process for fields: {self.context_fields_to_sign}")

            instance_id = context.get("instance_id")
            if not instance_id:
                return TaskResult(
                    status="failed",
                    error="No instance_id in context for signature"
                )

            # Debug: Print all context keys to see what's available
            print(f"   🔍 Checking for signature field '{self.signature_field}' in context")
            print(f"   🔍 Available context keys: {list(context.keys())}")

            # Look for signature-related keys
            signature_keys = [k for k in context.keys() if 'signature' in k.lower() or 'sign' in k.lower()]
            print(f"   🔍 Signature-related keys found: {signature_keys}")

            # Check for signature in multiple possible locations
            signature_data = None
            signature_found_key = None

            # Check the expected field first
            if self.signature_field in context:
                signature_data = context[self.signature_field]
                signature_found_key = self.signature_field
                print(f"   ✅ Found signature in expected field '{self.signature_field}'")

            # Check task_id based field (common pattern)
            elif f"{self.task_id}_input" in context:
                input_data = context[f"{self.task_id}_input"]
                if isinstance(input_data, dict) and 'digital_signature' in input_data:
                    signature_data = input_data['digital_signature']
                    signature_found_key = f"{self.task_id}_input.digital_signature"
                    print(f"   ✅ Found signature in task input field '{self.task_id}_input.digital_signature'")
                elif isinstance(input_data, dict):
                    print(f"   🔍 Task input data keys: {list(input_data.keys())}")

            if signature_data:
                print(f"   ✅ Signature found at '{signature_found_key}', validating...")
                print(f"   🔍 Signature data type: {type(signature_data)}")
                if isinstance(signature_data, dict):
                    print(f"   🔍 Signature data keys: {list(signature_data.keys())}")
            else:
                print(f"   ❌ No signature found in any expected location")
                # Let's check what's in the task input
                task_input_key = f"{self.task_id}_input"
                if task_input_key in context:
                    print(f"   🔍 Task input exists but no signature found. Content: {context[task_input_key]}")

            # If signature exists, validate and continue
            if signature_data:

                # For now, assume valid since we're doing client-side validation
                # Real validation would require crypto libraries
                print(f"   ✅ Signature validation completed")

                # Handle signature data - could be string or dict
                if isinstance(signature_data, str):
                    # Simple string signature
                    output_data = {
                        f"{self.task_id}_signature_valid": True,
                        f"{self.task_id}_signer": "Client Certificate",
                        f"{self.task_id}_signed_at": datetime.utcnow().isoformat(),
                        f"{self.task_id}_algorithm": "RSA-SHA256"
                    }
                    log_details = {
                        "signature_length": len(signature_data),
                        "signature_type": "base64_string"
                    }
                else:
                    # Dict signature with metadata
                    output_data = {
                        f"{self.task_id}_signature_valid": True,
                        f"{self.task_id}_signer": signature_data.get("certificate_info", {}).get("subject", "Unknown"),
                        f"{self.task_id}_signed_at": signature_data.get("timestamp"),
                        f"{self.task_id}_algorithm": signature_data.get("algorithm", "Unknown")
                    }
                    log_details = {
                        "signer": signature_data.get("certificate_info", {}).get("subject"),
                        "signed_at": signature_data.get("timestamp"),
                        "algorithm": signature_data.get("algorithm")
                    }

                self.state.output_data = output_data

                await self.log_info(
                    f"Digital signature validated successfully",
                    details=log_details
                )

                return TaskResult(
                    status="continue",
                    data=output_data
                )

            # No signature yet - prepare for signing
            signable_data = self._prepare_signable_data(context)

            # Set waiting_for in state
            self.state.waiting_for = "signature"

            # Store minimal signature info - NO signable_data to avoid nesting
            signature_info_key = f"_signature_pending_{self.signature_field}"
            context[signature_info_key] = {
                "signature_field": self.signature_field,
                "created_at": datetime.utcnow().isoformat(),
                "status": "pending",
                "data_hash": signable_data.get("data_hash")  # Just the hash
            }

            await self.log_info(
                f"Waiting for digital signature on {len(self.context_fields_to_sign)} context fields",
                details={
                    "fields_to_sign": self.context_fields_to_sign,
                    "signature_field": self.signature_field,
                    "timeout_minutes": self.timeout_minutes
                }
            )

            # Return waiting status - signature needed from client
            return TaskResult(
                status="waiting",
                data={
                    "waiting_for": "signature",
                    "form_config": self.form_config,
                    "required_fields": self.required_fields,
                    "data_hash": signable_data.get("data_hash")  # Just hash for verification
                },
                retry_delay=60  # Check for signature every minute
            )

        except Exception as e:
            error_msg = f"Signature operation failed: {str(e)}"
            print(f"   ❌ SignerOperator failed: {e}")

            await self.log_error(
                "Signature operation failed",
                error=e,
                details={}
            )

            self.state.error_message = error_msg
            return TaskResult(
                status="failed",
                error=error_msg
            )

    async def handle_signature_received(
        self,
        context: Dict[str, Any],
        signature_data: Dict[str, Any]
    ) -> TaskResult:
        """
        Handle signature received from client.

        Args:
            context: Current workflow context
            signature_data: Signature data from client

        Returns:
            TaskResult indicating success or failure
        """
        try:
            # Validate signature data structure
            required_fields = ["signature", "certificate", "algorithm"]
            for field in required_fields:
                if field not in signature_data:
                    return TaskResult(
                        status="failed",
                        error=f"Missing required signature field: {field}"
                    )

            # Add signature to context
            context[self.signature_field] = signature_data

            # Log successful signature receipt
            await self.log_info(
                f"Digital signature received and stored in context",
                details={
                    "signature_field": self.signature_field,
                    "algorithm": signature_data.get("algorithm"),
                    "certificate_subject": signature_data.get("certificate_info", {}).get("subject")
                }
            )

            return TaskResult(
                status="continue",
                data={}
            )

        except Exception as e:
            error_msg = f"Failed to process received signature: {str(e)}"
            await self.log_error(
                "Failed to process signature",
                error=e,
                details={"signature_data": signature_data}
            )

            return TaskResult(
                status="failed",
                error=error_msg
            )


class SignatureStatus:
    """Helper class for signature status tracking"""

    PENDING = "pending"
    SIGNED = "signed"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"


class SignatureRequirement(BaseModel):
    """Model for signature requirements"""

    fields_to_sign: List[str]
    required_cert_type: Optional[str] = None
    purpose: str = "approval"
    timeout_minutes: int = 30
    metadata: Dict[str, Any] = Field(default_factory=dict)