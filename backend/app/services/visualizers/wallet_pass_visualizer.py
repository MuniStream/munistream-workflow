"""
Base class for generating digital wallet passes (Apple PKPass and Google Wallet)
for any entity in the MuniStream platform.
"""

import json
import uuid
import hashlib
import tempfile
import zipfile
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union
from pathlib import Path
import base64

try:
    import py_pkpass as pkpass
    PKPASS_AVAILABLE = True
except ImportError:
    PKPASS_AVAILABLE = False

try:
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    import jwt
    GOOGLE_WALLET_AVAILABLE = True
except ImportError:
    GOOGLE_WALLET_AVAILABLE = False

from app.models.legal_entity import LegalEntity
from app.core.config import settings


class WalletPassVisualizer:
    """
    Base class for generating wallet passes for any entity type.
    Supports both Apple Wallet (PKPass) and Google Wallet.
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

        # Validate required configuration
        if not settings.FRONTEND_BASE_URL:
            raise ValueError("FRONTEND_BASE_URL is required for wallet pass generation")

    def get_pass_data(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Extract data from entity for wallet pass.
        Override this method in tenant-specific implementations.
        """
        return {
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
            "data": entity.data if hasattr(entity, 'data') else {}
        }

    def get_nfc_url(self, entity: LegalEntity) -> str:
        """Generate NFC verification URL following /verify/{entity_id}/nfc pattern"""
        entity_type = entity.entity_type.lower().replace(' ', '_')
        entity_id_short = entity.entity_id[:8].lower() if entity.entity_id else 'unknown'
        return f"{settings.FRONTEND_BASE_URL}/verify/{entity_type}_{entity_id_short}/nfc"

    def get_verification_url(self, entity: LegalEntity) -> str:
        """Generate verification URL following /verify/{entity_id} pattern"""
        entity_type = entity.entity_type.lower().replace(' ', '_')
        entity_id_short = entity.entity_id[:8].lower() if entity.entity_id else 'unknown'
        return f"{settings.FRONTEND_BASE_URL}/verify/{entity_type}_{entity_id_short}"

    def get_nfc_payload(self, entity: LegalEntity) -> Dict[str, Any]:
        """
        Generate NFC payload data for quick verification.
        Optimized for <200ms response time.
        """
        return {
            "v": 1,  # Version
            "t": entity.entity_type[:10].upper(),  # Type (truncated)
            "i": entity.entity_id[:8].upper() if entity.entity_id else "UNKNOWN",
            "n": entity.name[:50] if entity.name else "Unknown LegalEntity",
            "u": self.get_nfc_url(entity),
            "ts": datetime.now().isoformat(),
            "valid": True,
            "tenant": self.tenant_id.upper()
        }

    def generate_pkpass(self, entity: LegalEntity) -> Optional[bytes]:
        """
        Generate Apple Wallet PKPass file for the entity.
        Returns binary PKPass data or None if generation fails.
        """
        if not PKPASS_AVAILABLE:
            raise ImportError("py-pkpass library not available. Install with: pip install py-pkpass")

        # Check required Apple configuration
        if not settings.APPLE_TEAM_ID or not settings.APPLE_PASS_TYPE_ID:
            raise ValueError("Apple Wallet configuration missing: APPLE_TEAM_ID and APPLE_PASS_TYPE_ID required")

        try:
            pass_data = self.get_pass_data(entity)

            # Create pass data dict for py-pkpass
            pass_data = self.get_pass_data(entity)

            # Build pass data structure for py-pkpass
            expiry_date = entity.created_at + timedelta(days=3*365) if entity.created_at else datetime.now() + timedelta(days=3*365)

            pass_dict = {
                "passTypeIdentifier": settings.APPLE_PASS_TYPE_ID,
                "organizationName": f"{self.tenant_id.upper()} - MuniStream",
                "teamIdentifier": settings.APPLE_TEAM_ID,
                "serialNumber": f"{self.tenant_id}-{entity.entity_id}",
                "description": f"{entity.entity_type} - {entity.name}",
                "formatVersion": 1,
                "storeCard": {
                    "primaryFields": [
                        {
                            "key": "name",
                            "label": "Nombre",
                            "value": entity.name
                        }
                    ],
                    "secondaryFields": [
                        {
                            "key": "type",
                            "label": "Tipo",
                            "value": entity.entity_type
                        },
                        {
                            "key": "id",
                            "label": "ID",
                            "value": entity.entity_id[:12] if entity.entity_id else "N/A"
                        }
                    ],
                    "auxiliaryFields": [
                        {
                            "key": "created",
                            "label": "Expedición",
                            "value": entity.created_at.strftime('%Y-%m-%d') if entity.created_at else "N/A"
                        },
                        {
                            "key": "expires",
                            "label": "Vigencia",
                            "value": expiry_date.strftime('%Y-%m-%d')
                        }
                    ]
                },
                "barcode": {
                    "message": self.get_verification_url(entity),
                    "format": "PKBarcodeFormatQR",
                    "messageEncoding": "iso-8859-1",
                    "altText": "Verificación"
                },
                "nfc": {
                    "message": json.dumps(self.get_nfc_payload(entity)),
                    "encryptionPublicKey": None
                },
                "backgroundColor": "rgb(25, 25, 25)",
                "foregroundColor": "rgb(255, 255, 255)",
                "labelColor": "rgb(200, 200, 200)"
            }

            # Generate the PKPass file
            return self._build_pkpass(pass_dict, entity)

        except Exception as e:
            print(f"Error generating PKPass: {str(e)}")
            return None

    def _build_pkpass(self, pass_dict: Dict[str, Any], entity: LegalEntity) -> bytes:
        """Build PKPass using py-pkpass library"""
        if not settings.APPLE_CERTIFICATE_PATH or not settings.APPLE_WWDR_CERT_PATH:
            raise ValueError("Apple certificates not configured. PKPass generation requires APPLE_CERTIFICATE_PATH and APPLE_WWDR_CERT_PATH")

        if not Path(settings.APPLE_CERTIFICATE_PATH).exists():
            raise FileNotFoundError(f"Apple certificate not found: {settings.APPLE_CERTIFICATE_PATH}")

        if not Path(settings.APPLE_WWDR_CERT_PATH).exists():
            raise FileNotFoundError(f"WWDR certificate not found: {settings.APPLE_WWDR_CERT_PATH}")

        # Use py-pkpass to generate signed PKPass
        pass_builder = pkpass.PKPass()
        pass_builder.certificate_path = settings.APPLE_CERTIFICATE_PATH
        pass_builder.wwdr_certificate_path = settings.APPLE_WWDR_CERT_PATH

        pkpass_data = pass_builder.create(pass_dict)
        return pkpass_data

    def generate_google_wallet_jwt(self, entity: LegalEntity) -> Optional[str]:
        """
        Generate Google Wallet JWT for the entity.
        Returns JWT string or None if generation fails.
        """
        if not GOOGLE_WALLET_AVAILABLE:
            raise ImportError("Google Wallet libraries not available")

        # Check required Google configuration
        if not settings.GOOGLE_WALLET_ISSUER_ID or not settings.GOOGLE_WALLET_ISSUER_EMAIL:
            raise ValueError("Google Wallet configuration missing: GOOGLE_WALLET_ISSUER_ID and GOOGLE_WALLET_ISSUER_EMAIL required")

        try:
            # Create Google Wallet object
            wallet_object = {
                "iss": settings.GOOGLE_WALLET_ISSUER_EMAIL,
                "aud": "google",
                "origins": [settings.FRONTEND_BASE_URL],
                "typ": "savetowallet",
                "iat": int(datetime.now().timestamp()),
                "payload": {
                    "genericObjects": [{
                        "id": f"{settings.GOOGLE_WALLET_ISSUER_ID}.{entity.entity_id}",
                        "classId": f"{settings.GOOGLE_WALLET_ISSUER_ID}.{self.tenant_id}_entity",
                        "state": "active",
                        "cardTitle": {
                            "defaultValue": {
                                "language": "es",
                                "value": entity.name or "Documento Oficial"
                            }
                        },
                        "subheader": {
                            "defaultValue": {
                                "language": "es",
                                "value": entity.entity_type or "Entidad"
                            }
                        },
                        "header": {
                            "defaultValue": {
                                "language": "es",
                                "value": f"{self.tenant_id.upper()} - MuniStream"
                            }
                        },
                        "textModulesData": [
                            {
                                "id": "entity_id",
                                "header": "ID",
                                "body": entity.entity_id[:12] if entity.entity_id else "N/A"
                            },
                            {
                                "id": "created_date",
                                "header": "Expedición",
                                "body": entity.created_at.strftime('%Y-%m-%d') if entity.created_at else "N/A"
                            }
                        ],
                        "barcode": {
                            "type": "QR_CODE",
                            "value": self.get_verification_url(entity),
                            "alternateText": "Verificación"
                        },
                        "heroImage": {
                            "sourceUri": {
                                "uri": f"{settings.FRONTEND_BASE_URL}/assets/wallet-hero.png"
                            },
                            "contentDescription": {
                                "defaultValue": {
                                    "language": "es",
                                    "value": f"Credencial {entity.entity_type}"
                                }
                            }
                        }
                    }]
                }
            }

            # Sign JWT with service account key
            if not settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH:
                raise ValueError("Google service account key not configured. Set GOOGLE_SERVICE_ACCOUNT_KEY_PATH")

            if not Path(settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH).exists():
                raise FileNotFoundError(f"Google service account key not found: {settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH}")

            with open(settings.GOOGLE_SERVICE_ACCOUNT_KEY_PATH, 'r') as f:
                key_data = json.load(f)
                private_key = key_data['private_key']

            return jwt.encode(wallet_object, private_key, algorithm="RS256")

        except Exception as e:
            print(f"Error generating Google Wallet JWT: {str(e)}")
            return None

    def get_wallet_urls(self, entity: LegalEntity) -> Dict[str, str]:
        """Get all wallet-related URLs for the entity"""
        entity_type = entity.entity_type.lower().replace(' ', '_')
        entity_id_short = entity.entity_id[:8].lower() if entity.entity_id else 'unknown'
        base_path = f"/verify/{entity_type}_{entity_id_short}"

        return {
            "main": f"{settings.FRONTEND_BASE_URL}{base_path}/wallet",
            "apple": f"{settings.FRONTEND_BASE_URL}{base_path}/wallet/apple",
            "google": f"{settings.FRONTEND_BASE_URL}{base_path}/wallet/google",
            "qr": f"{settings.FRONTEND_BASE_URL}{base_path}/wallet/qr",
            "nfc": f"{settings.FRONTEND_BASE_URL}{base_path}/nfc",
            "verify": f"{settings.FRONTEND_BASE_URL}{base_path}"
        }