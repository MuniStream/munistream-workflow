"""
QR Code Generation for Entity Visualization
"""

import io
import base64
from typing import Union, Optional
import qrcode
from qrcode.image.pil import PilImage
from PIL import Image


class QRCodeGenerator:
    """Generate QR codes for entity fields and verification"""

    def __init__(self):
        """Initialize QR code generator with default settings"""
        self.default_settings = {
            'version': 1,  # Controls size (1 is smallest)
            'error_correction': qrcode.constants.ERROR_CORRECT_L,
            'box_size': 10,
            'border': 4,
        }

    async def generate_qr_code(
        self,
        data: str,
        size: int = 200,
        format: str = "PNG",
        error_correction: str = "L"
    ) -> bytes:
        """
        Generate QR code for given data

        Args:
            data: Data to encode in QR code
            size: Size of the QR code image in pixels
            format: Image format (PNG, JPEG, etc.)
            error_correction: Error correction level (L, M, Q, H)

        Returns:
            QR code image as bytes
        """
        # Map error correction level
        error_levels = {
            "L": qrcode.constants.ERROR_CORRECT_L,
            "M": qrcode.constants.ERROR_CORRECT_M,
            "Q": qrcode.constants.ERROR_CORRECT_Q,
            "H": qrcode.constants.ERROR_CORRECT_H,
        }

        # Calculate box size based on desired final size
        # QR codes are square, so we need to determine appropriate box_size
        estimated_modules = 21  # Version 1 has 21x21 modules
        box_size = max(1, size // (estimated_modules + 2 * self.default_settings['border']))

        # Create QR code instance
        qr = qrcode.QRCode(
            version=self.default_settings['version'],
            error_correction=error_levels.get(error_correction, qrcode.constants.ERROR_CORRECT_L),
            box_size=box_size,
            border=self.default_settings['border'],
        )

        # Add data and optimize
        qr.add_data(data)
        qr.make(fit=True)

        # Create image
        img = qr.make_image(fill_color="black", back_color="white")

        # Resize to exact size if needed
        if img.size[0] != size:
            img = img.resize((size, size), Image.Resampling.LANCZOS)

        # Convert to bytes
        buffer = io.BytesIO()
        img.save(buffer, format=format)
        buffer.seek(0)
        return buffer.read()

    async def generate_verification_qr(
        self,
        entity_id: str,
        verification_data: dict,
        size: int = 150
    ) -> bytes:
        """
        Generate QR code for entity verification

        Args:
            entity_id: ID of the entity
            verification_data: Additional verification data
            size: Size of QR code

        Returns:
            QR code bytes
        """
        # Create verification payload
        payload = {
            "entity_id": entity_id,
            "verification_url": f"/api/v1/verify/{entity_id}",
            "timestamp": verification_data.get("timestamp"),
            "checksum": verification_data.get("checksum"),
        }

        # Convert to string (JSON-like format)
        qr_data = f"verify:{entity_id}:{payload.get('checksum', '')}"

        return await self.generate_qr_code(qr_data, size=size)

    async def generate_signature_qr(
        self,
        signature_data: str,
        signer_info: dict,
        size: int = 100
    ) -> bytes:
        """
        Generate QR code for digital signature verification

        Args:
            signature_data: The signature data/hash
            signer_info: Information about the signer
            size: Size of QR code

        Returns:
            QR code bytes
        """
        # Create signature verification payload
        qr_data = f"signature:{signature_data}:{signer_info.get('signer_id', '')}"

        return await self.generate_qr_code(qr_data, size=size)

    async def generate_field_qr(
        self,
        field_name: str,
        field_value: Union[str, int, float, bool],
        entity_id: str,
        size: int = 150
    ) -> bytes:
        """
        Generate QR code for a specific entity field

        Args:
            field_name: Name of the field
            field_value: Value of the field
            entity_id: ID of the entity
            size: Size of QR code

        Returns:
            QR code bytes
        """
        # Create field verification payload
        qr_data = f"field:{entity_id}:{field_name}:{field_value}"

        return await self.generate_qr_code(qr_data, size=size)

    def to_data_url(self, qr_bytes: bytes, format: str = "PNG") -> str:
        """
        Convert QR code bytes to data URL for embedding in HTML

        Args:
            qr_bytes: QR code image bytes
            format: Image format

        Returns:
            Data URL string
        """
        base64_str = base64.b64encode(qr_bytes).decode('utf-8')
        mime_type = f"image/{format.lower()}"
        return f"data:{mime_type};base64,{base64_str}"

    async def generate_bulk_qr_codes(
        self,
        data_list: list,
        size: int = 150
    ) -> list:
        """
        Generate multiple QR codes efficiently

        Args:
            data_list: List of data strings to encode
            size: Size of each QR code

        Returns:
            List of QR code bytes
        """
        qr_codes = []
        for data in data_list:
            qr_bytes = await self.generate_qr_code(data, size=size)
            qr_codes.append(qr_bytes)
        return qr_codes