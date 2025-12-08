"""
Certificate management service for wallet pass generation.
Handles Apple Developer certificates and Google service account keys.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class WalletCertificateService:
    """
    Service for managing certificates and keys used in wallet pass generation.
    """

    def __init__(self):
        self.apple_cert = None
        self.apple_private_key = None
        self.wwdr_cert = None
        self.google_service_account = None

    def load_apple_certificates(self) -> bool:
        """
        Load Apple Developer certificates for PKPass signing.
        Returns True if certificates are successfully loaded.
        """
        try:
            # Load Apple signing certificate
            if settings.APPLE_CERTIFICATE_PATH:
                cert_path = Path(settings.APPLE_CERTIFICATE_PATH)
                if cert_path.exists():
                    with open(cert_path, 'rb') as f:
                        cert_data = f.read()

                    # Try to load as PEM or DER
                    try:
                        self.apple_cert = x509.load_pem_x509_certificate(cert_data)
                    except ValueError:
                        self.apple_cert = x509.load_der_x509_certificate(cert_data)

                    logger.info("Apple certificate loaded successfully")
                else:
                    logger.warning(f"Apple certificate not found at {cert_path}")
                    return False

            # Load WWDR certificate
            if settings.APPLE_WWDR_CERT_PATH:
                wwdr_path = Path(settings.APPLE_WWDR_CERT_PATH)
                if wwdr_path.exists():
                    with open(wwdr_path, 'rb') as f:
                        wwdr_data = f.read()

                    try:
                        self.wwdr_cert = x509.load_pem_x509_certificate(wwdr_data)
                    except ValueError:
                        self.wwdr_cert = x509.load_der_x509_certificate(wwdr_data)

                    logger.info("WWDR certificate loaded successfully")
                else:
                    logger.warning(f"WWDR certificate not found at {wwdr_path}")
                    return False

            return self.apple_cert is not None and self.wwdr_cert is not None

        except Exception as e:
            logger.error(f"Error loading Apple certificates: {str(e)}")
            return False

    def load_google_service_account(self) -> bool:
        """
        Load Google service account credentials for Google Wallet.
        Returns True if credentials are successfully loaded.
        """
        try:
            if settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH:
                key_path = Path(settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH)
                if key_path.exists():
                    with open(key_path, 'r') as f:
                        self.google_service_account = json.load(f)

                    logger.info("Google service account loaded successfully")
                    return True
                else:
                    logger.warning(f"Google service account key not found at {key_path}")
                    return False

            return False

        except Exception as e:
            logger.error(f"Error loading Google service account: {str(e)}")
            return False

    def sign_manifest(self, manifest_data: bytes) -> Optional[bytes]:
        """
        Sign manifest data for PKPass using Apple certificate.
        Returns signature bytes or None if signing fails.
        """
        if not self.apple_cert or not self.apple_private_key:
            logger.error("Apple certificates not loaded")
            return None

        try:
            # Sign the manifest data
            signature = self.apple_private_key.sign(
                manifest_data,
                padding.PKCS1v15(),
                hashes.SHA1()
            )

            return signature

        except Exception as e:
            logger.error(f"Error signing manifest: {str(e)}")
            return None

    def get_apple_certificate_info(self) -> Optional[Dict[str, Any]]:
        """
        Get information about the loaded Apple certificate.
        """
        if not self.apple_cert:
            return None

        try:
            return {
                "subject": self.apple_cert.subject.rfc4514_string(),
                "issuer": self.apple_cert.issuer.rfc4514_string(),
                "serial_number": str(self.apple_cert.serial_number),
                "not_valid_before": self.apple_cert.not_valid_before.isoformat(),
                "not_valid_after": self.apple_cert.not_valid_after.isoformat(),
                "team_id": settings.APPLE_TEAM_ID
            }

        except Exception as e:
            logger.error(f"Error getting certificate info: {str(e)}")
            return None

    def get_google_service_account_info(self) -> Optional[Dict[str, Any]]:
        """
        Get information about the loaded Google service account.
        """
        if not self.google_service_account:
            return None

        try:
            return {
                "project_id": self.google_service_account.get("project_id"),
                "client_email": self.google_service_account.get("client_email"),
                "client_id": self.google_service_account.get("client_id"),
                "type": self.google_service_account.get("type"),
                "issuer_id": settings.GOOGLE_WALLET_ISSUER_ID
            }

        except Exception as e:
            logger.error(f"Error getting service account info: {str(e)}")
            return None

    def validate_apple_configuration(self) -> bool:
        """
        Validate that all required Apple configuration is present.
        """
        required_settings = [
            settings.APPLE_TEAM_ID,
            settings.APPLE_PASS_TYPE_ID,
            settings.APPLE_CERTIFICATE_PATH,
            settings.APPLE_WWDR_CERT_PATH
        ]

        missing = [setting for setting in required_settings if not setting]

        if missing:
            logger.error(f"Missing Apple configuration: {missing}")
            return False

        # Check if certificate files exist
        cert_files = [
            settings.APPLE_CERTIFICATE_PATH,
            settings.APPLE_WWDR_CERT_PATH
        ]

        for cert_file in cert_files:
            if cert_file and not Path(cert_file).exists():
                logger.error(f"Certificate file not found: {cert_file}")
                return False

        return True

    def validate_google_configuration(self) -> bool:
        """
        Validate that all required Google configuration is present.
        """
        required_settings = [
            settings.GOOGLE_WALLET_ISSUER_ID,
            settings.GOOGLE_WALLET_ISSUER_EMAIL,
            settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH
        ]

        missing = [setting for setting in required_settings if not setting]

        if missing:
            logger.error(f"Missing Google configuration: {missing}")
            return False

        # Check if service account key file exists
        if settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH and not Path(settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH).exists():
            logger.error(f"Service account key file not found: {settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH}")
            return False

        return True

    def initialize(self) -> Dict[str, bool]:
        """
        Initialize the certificate service by loading all available certificates.
        Returns dict with loading status for each provider.
        """
        result = {
            "apple": False,
            "google": False
        }

        # Try to load Apple certificates if configuration is present
        if self.validate_apple_configuration():
            result["apple"] = self.load_apple_certificates()
        else:
            logger.info("Apple Wallet configuration not complete, skipping")

        # Try to load Google service account if configuration is present
        if self.validate_google_configuration():
            result["google"] = self.load_google_service_account()
        else:
            logger.info("Google Wallet configuration not complete, skipping")

        return result


# Global instance
certificate_service = WalletCertificateService()