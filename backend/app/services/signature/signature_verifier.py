"""
Signature Verification Service

Handles verification of X.509 certificate-based digital signatures.
"""
import base64
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding, ec
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)


class SignatureVerifier:
    """Service for verifying digital signatures"""

    def __init__(self):
        """Initialize the signature verifier"""
        self.supported_algorithms = {
            "RSA-SHA256": (padding.PSS, hashes.SHA256),
            "RSA-SHA512": (padding.PSS, hashes.SHA512),
            "ECDSA-SHA256": (None, hashes.SHA256),
            "ECDSA-SHA384": (None, hashes.SHA384)
        }

    async def verify_signature(
        self,
        data: bytes,
        signature_base64: str,
        certificate_pem: str,
        algorithm: str = "RSA-SHA256"
    ) -> bool:
        """
        Verify a digital signature against data using a certificate.

        Args:
            data: Original data that was signed
            signature_base64: Base64-encoded signature
            certificate_pem: PEM-encoded X.509 certificate
            algorithm: Signature algorithm used

        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Decode the signature
            signature_bytes = base64.b64decode(signature_base64)

            # Load the certificate
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())

            # Extract public key from certificate
            public_key = certificate.public_key()

            # Verify the signature based on algorithm
            if algorithm not in self.supported_algorithms:
                logger.error(f"Unsupported signature algorithm: {algorithm}")
                return False

            padding_type, hash_algorithm = self.supported_algorithms[algorithm]

            if algorithm.startswith("RSA"):
                # RSA signature verification
                if not isinstance(public_key, rsa.RSAPublicKey):
                    logger.error("Certificate does not contain RSA public key")
                    return False

                padding_obj = padding_type(
                    mgf=padding.MGF1(hash_algorithm()),
                    salt_length=padding.PSS.MAX_LENGTH
                )

                public_key.verify(
                    signature_bytes,
                    data,
                    padding_obj,
                    hash_algorithm()
                )

            elif algorithm.startswith("ECDSA"):
                # ECDSA signature verification
                if not isinstance(public_key, ec.EllipticCurvePublicKey):
                    logger.error("Certificate does not contain EC public key")
                    return False

                public_key.verify(
                    signature_bytes,
                    data,
                    ec.ECDSA(hash_algorithm())
                )

            else:
                logger.error(f"Unsupported algorithm type: {algorithm}")
                return False

            logger.info(f"Signature verification successful for algorithm {algorithm}")
            return True

        except InvalidSignature:
            logger.warning("Signature verification failed - invalid signature")
            return False
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False

    async def verify_certificate_chain(
        self,
        certificate_pem: str,
        ca_certificates: list = None
    ) -> bool:
        """
        Verify certificate chain (basic implementation).

        Args:
            certificate_pem: PEM-encoded certificate to verify
            ca_certificates: List of CA certificates for chain verification

        Returns:
            True if certificate chain is valid
        """
        try:
            # Load the certificate
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())

            # Basic certificate validation
            now = datetime.utcnow()

            # Check if certificate is currently valid (not expired)
            if certificate.not_valid_after < now:
                logger.warning("Certificate has expired")
                return False

            if certificate.not_valid_before > now:
                logger.warning("Certificate is not yet valid")
                return False

            # TODO: Implement full chain verification with CA certificates
            # This would require additional CA certificate management
            logger.info("Basic certificate validation successful")
            return True

        except Exception as e:
            logger.error(f"Certificate chain verification error: {e}")
            return False

    async def extract_signature_info(
        self,
        signature_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract information from signature data for verification display.

        Args:
            signature_data: Complete signature data

        Returns:
            Dictionary with signature information
        """
        try:
            info = {}

            # Basic signature info
            info["algorithm"] = signature_data.get("algorithm", "unknown")
            info["timestamp"] = signature_data.get("received_at", signature_data.get("timestamp"))

            # Certificate information
            certificate_info = signature_data.get("certificate_info", {})
            info["signer_subject"] = certificate_info.get("subject", "unknown")
            info["signer_issuer"] = certificate_info.get("issuer", "unknown")
            info["certificate_valid_from"] = certificate_info.get("not_valid_before")
            info["certificate_valid_until"] = certificate_info.get("not_valid_after")
            info["certificate_serial"] = certificate_info.get("serial_number")

            # Verification status (if available)
            info["verified"] = signature_data.get("verified", False)
            info["verification_timestamp"] = signature_data.get("verification_timestamp")

            return info

        except Exception as e:
            logger.error(f"Failed to extract signature info: {e}")
            return {}

    async def create_verification_report(
        self,
        data: bytes,
        signature_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a comprehensive verification report.

        Args:
            data: Original signed data
            signature_data: Signature data to verify

        Returns:
            Verification report
        """
        try:
            report = {
                "verification_timestamp": datetime.utcnow().isoformat(),
                "signature_info": await self.extract_signature_info(signature_data),
                "verification_results": {}
            }

            # Perform signature verification
            signature_valid = await self.verify_signature(
                data=data,
                signature_base64=signature_data["signature"],
                certificate_pem=signature_data["certificate"],
                algorithm=signature_data.get("algorithm", "RSA-SHA256")
            )

            report["verification_results"]["signature_valid"] = signature_valid

            # Perform certificate validation
            cert_valid = await self.verify_certificate_chain(
                certificate_pem=signature_data["certificate"]
            )

            report["verification_results"]["certificate_valid"] = cert_valid

            # Overall verification status
            report["overall_valid"] = signature_valid and cert_valid

            logger.info(f"Verification report created: overall_valid={report['overall_valid']}")

            return report

        except Exception as e:
            logger.error(f"Failed to create verification report: {e}")
            return {
                "verification_timestamp": datetime.utcnow().isoformat(),
                "error": str(e),
                "overall_valid": False
            }

    def get_supported_algorithms(self) -> list:
        """Get list of supported signature algorithms"""
        return list(self.supported_algorithms.keys())