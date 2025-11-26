"""
Certificate Management Service

Handles X.509 certificate parsing, validation, and information extraction.
"""
import base64
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives import serialization, hashes

logger = logging.getLogger(__name__)


class CertificateManager:
    """Service for managing X.509 certificates"""

    @staticmethod
    async def extract_certificate_info(certificate_pem: str) -> Optional[Dict[str, Any]]:
        """
        Extract information from an X.509 certificate.

        Args:
            certificate_pem: PEM-encoded certificate

        Returns:
            Dictionary with certificate information or None if failed
        """
        try:
            # Load the certificate
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())

            # Extract basic information
            info = {
                "version": certificate.version.value,
                "serial_number": str(certificate.serial_number),
                "not_valid_before": certificate.not_valid_before.isoformat(),
                "not_valid_after": certificate.not_valid_after.isoformat(),
                "subject": CertificateManager._format_name(certificate.subject),
                "issuer": CertificateManager._format_name(certificate.issuer),
                "signature_algorithm": certificate.signature_algorithm_oid._name,
                "public_key_algorithm": type(certificate.public_key()).__name__
            }

            # Extract subject alternative names if present
            try:
                san_extension = certificate.extensions.get_extension_for_oid(
                    x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME
                )
                sans = []
                for name in san_extension.value:
                    if isinstance(name, x509.DNSName):
                        sans.append(f"DNS:{name.value}")
                    elif isinstance(name, x509.RFC822Name):
                        sans.append(f"Email:{name.value}")
                    elif isinstance(name, x509.UniformResourceIdentifier):
                        sans.append(f"URI:{name.value}")
                info["subject_alternative_names"] = sans
            except x509.ExtensionNotFound:
                info["subject_alternative_names"] = []

            # Extract key usage if present
            try:
                key_usage = certificate.extensions.get_extension_for_oid(
                    x509.ExtensionOID.KEY_USAGE
                )
                info["key_usage"] = {
                    "digital_signature": key_usage.value.digital_signature,
                    "content_commitment": key_usage.value.content_commitment,
                    "key_encipherment": key_usage.value.key_encipherment,
                    "data_encipherment": key_usage.value.data_encipherment,
                    "key_agreement": key_usage.value.key_agreement,
                    "key_cert_sign": key_usage.value.key_cert_sign,
                    "crl_sign": key_usage.value.crl_sign
                }
            except x509.ExtensionNotFound:
                info["key_usage"] = {}

            # Add public key information
            public_key = certificate.public_key()
            if hasattr(public_key, 'key_size'):
                info["public_key_size"] = public_key.key_size

            # Calculate certificate fingerprints
            info["fingerprints"] = {
                "sha256": certificate.fingerprint(hashes.SHA256()).hex(),
                "sha1": certificate.fingerprint(hashes.SHA1()).hex()
            }

            logger.info(f"Extracted certificate info for subject: {info['subject']}")
            return info

        except Exception as e:
            logger.error(f"Failed to extract certificate info: {e}")
            return None

    @staticmethod
    def _format_name(name: x509.Name) -> str:
        """
        Format an X.509 Name object as a readable string.

        Args:
            name: X.509 Name object

        Returns:
            Formatted name string
        """
        try:
            # Create a human-readable representation
            parts = []
            for attribute in name:
                oid_name = attribute.oid._name
                value = attribute.value

                # Use common name abbreviations
                name_map = {
                    "commonName": "CN",
                    "organizationName": "O",
                    "organizationalUnitName": "OU",
                    "countryName": "C",
                    "localityName": "L",
                    "stateOrProvinceName": "ST",
                    "emailAddress": "E"
                }

                display_name = name_map.get(oid_name, oid_name)
                parts.append(f"{display_name}={value}")

            return ", ".join(parts)

        except Exception as e:
            logger.error(f"Failed to format name: {e}")
            return str(name)

    @staticmethod
    async def validate_certificate_for_signing(certificate_pem: str) -> Dict[str, Any]:
        """
        Validate a certificate for digital signing purposes.

        Args:
            certificate_pem: PEM-encoded certificate

        Returns:
            Validation result dictionary
        """
        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())

            result = {
                "valid": False,
                "errors": [],
                "warnings": [],
                "info": {}
            }

            # Check validity period
            now = datetime.utcnow()
            if certificate.not_valid_before > now:
                result["errors"].append("Certificate is not yet valid")
            elif certificate.not_valid_after < now:
                result["errors"].append("Certificate has expired")
            else:
                # Check if certificate is expiring soon (within 30 days)
                time_to_expiry = certificate.not_valid_after - now
                if time_to_expiry.days < 30:
                    result["warnings"].append(f"Certificate expires in {time_to_expiry.days} days")

            # Check key usage for digital signature
            try:
                key_usage = certificate.extensions.get_extension_for_oid(
                    x509.ExtensionOID.KEY_USAGE
                )
                if not key_usage.value.digital_signature:
                    result["warnings"].append("Certificate not marked for digital signature")
            except x509.ExtensionNotFound:
                result["warnings"].append("No key usage extension found")

            # Check public key algorithm and size
            public_key = certificate.public_key()
            if hasattr(public_key, 'key_size'):
                if public_key.key_size < 2048:
                    result["warnings"].append(f"Public key size ({public_key.key_size}) may be insufficient")

            # If no errors, certificate is valid for signing
            result["valid"] = len(result["errors"]) == 0

            # Add certificate info
            result["info"] = await CertificateManager.extract_certificate_info(certificate_pem)

            logger.info(f"Certificate validation: valid={result['valid']}, "
                       f"errors={len(result['errors'])}, warnings={len(result['warnings'])}")

            return result

        except Exception as e:
            logger.error(f"Certificate validation failed: {e}")
            return {
                "valid": False,
                "errors": [f"Certificate validation error: {str(e)}"],
                "warnings": [],
                "info": {}
            }

    @staticmethod
    async def extract_public_key_pem(certificate_pem: str) -> Optional[str]:
        """
        Extract the public key from a certificate in PEM format.

        Args:
            certificate_pem: PEM-encoded certificate

        Returns:
            PEM-encoded public key or None if failed
        """
        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
            public_key = certificate.public_key()

            # Serialize public key to PEM format
            public_key_pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode()

            return public_key_pem

        except Exception as e:
            logger.error(f"Failed to extract public key: {e}")
            return None

    @staticmethod
    def parse_certificate_from_file_content(file_content: bytes) -> Optional[str]:
        """
        Parse certificate from uploaded file content.

        Args:
            file_content: Raw file content

        Returns:
            PEM-encoded certificate or None if failed
        """
        try:
            # Try to decode as text first
            content_str = file_content.decode('utf-8')

            # Check if already PEM format
            if '-----BEGIN CERTIFICATE-----' in content_str:
                return content_str

            # Try to load as DER format and convert to PEM
            try:
                certificate = x509.load_der_x509_certificate(file_content)
                pem_data = certificate.public_bytes(serialization.Encoding.PEM)
                return pem_data.decode()
            except Exception:
                pass

            # Try base64 decode and then load as DER
            try:
                der_data = base64.b64decode(content_str)
                certificate = x509.load_der_x509_certificate(der_data)
                pem_data = certificate.public_bytes(serialization.Encoding.PEM)
                return pem_data.decode()
            except Exception:
                pass

            logger.error("Unable to parse certificate from file content")
            return None

        except Exception as e:
            logger.error(f"Failed to parse certificate from file: {e}")
            return None