"""
Entity Report Generator using ReportLab and Jinja2
"""

import io
import base64
from typing import Any, Dict, Optional, List, Union
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.platypus import KeepTogether, Flowable
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY

from xhtml2pdf import pisa
import jinja2

from .template_engine import TemplateEngine
from .qr_generator import QRCodeGenerator
from .data_formatter import DataFormatter
from ...models.legal_entity import LegalEntity


class EntityReportGenerator:
    """Generate PDF reports for entities based on templates"""

    def __init__(self):
        self.template_engine = TemplateEngine()
        self.qr_generator = QRCodeGenerator()
        self.data_formatter = DataFormatter()
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Setup custom paragraph styles for the PDF"""
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Title'],
            fontSize=24,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=30,
            alignment=TA_CENTER
        ))

        self.styles.add(ParagraphStyle(
            name='SectionTitle',
            parent=self.styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#34495e'),
            spaceBefore=20,
            spaceAfter=12,
            leftIndent=0
        ))

        self.styles.add(ParagraphStyle(
            name='FieldLabel',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#7f8c8d'),
            fontName='Helvetica-Bold'
        ))

        self.styles.add(ParagraphStyle(
            name='FieldValue',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=colors.HexColor('#2c3e50')
        ))

    async def generate_entity_report(
        self,
        entity: LegalEntity,
        template_name: str = "default",
        include_qr: bool = True,
        include_signatures: bool = True,
        format: str = "pdf"
    ) -> bytes:
        """
        Generate PDF report using ReportLab with Jinja2 templates

        Args:
            entity: The entity to generate report for
            template_name: Template name to use
            include_qr: Include QR codes in the report
            include_signatures: Include signatures section
            format: Output format (pdf or html)

        Returns:
            PDF bytes
        """
        # Prepare data for template
        template_data = await self._prepare_template_data(
            entity,
            include_qr,
            include_signatures
        )

        if format == "html":
            # Generate HTML only
            html = await self.template_engine.render_entity(
                entity,
                template_name,
                template_data
            )
            return html.encode('utf-8')

        # For PDF, use different approach based on template complexity
        if template_name in ["default", "simple"]:
            # Use ReportLab directly for simple layouts
            return await self._generate_reportlab_pdf(entity, template_data)
        else:
            # Use HTML to PDF conversion for complex layouts
            return await self._generate_html_to_pdf(
                entity,
                template_name,
                template_data
            )

    async def _prepare_template_data(
        self,
        entity: LegalEntity,
        include_qr: bool,
        include_signatures: bool
    ) -> Dict[str, Any]:
        """Prepare data for template rendering"""
        data = {
            "entity": entity,
            "generated_at": datetime.utcnow().isoformat(),
            "formatted_data": self.data_formatter.format_entity_data(entity.data),
            "sections": self._organize_data_sections(entity),
        }

        # Generate QR codes if requested
        if include_qr:
            # Main entity QR code
            qr_data = {
                "entity_id": entity.entity_id,
                "type": entity.entity_type,
                "name": entity.name,
                "verification_url": f"/verify/{entity.entity_id}"
            }
            data["qr_code"] = await self.qr_generator.generate_qr_code(
                str(qr_data),
                size=200
            )
            data["qr_code_data_url"] = self._to_data_url(data["qr_code"])

            # Field-specific QR codes
            if entity.visualization_config:
                data["field_qr_codes"] = {}
                for field_name, config in entity.visualization_config.items():
                    if config.get("type") == "qr_code":
                        field_value = entity.data.get(field_name, "")
                        qr_bytes = await self.qr_generator.generate_qr_code(
                            str(field_value),
                            size=config.get("options", {}).get("size", 150)
                        )
                        data["field_qr_codes"][field_name] = self._to_data_url(qr_bytes)

        # Add signatures if available
        if include_signatures and hasattr(entity, 'signatures'):
            data["signatures"] = []
            for sig in getattr(entity, 'signatures', []):
                sig_data = {
                    "signer_name": sig.get("signer_name"),
                    "signed_at": sig.get("signed_at"),
                    "role": sig.get("signer_role"),
                }
                if sig.get("signature_data"):
                    # Generate QR for signature verification
                    sig_qr = await self.qr_generator.generate_qr_code(
                        sig.get("signature_data"),
                        size=100
                    )
                    sig_data["qr_code"] = self._to_data_url(sig_qr)
                data["signatures"].append(sig_data)

        return data

    async def _generate_reportlab_pdf(
        self,
        entity: LegalEntity,
        template_data: Dict[str, Any]
    ) -> bytes:
        """Generate PDF using ReportLab directly"""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20*mm,
            leftMargin=20*mm,
            topMargin=20*mm,
            bottomMargin=20*mm
        )

        # Build story (content flow)
        story = []

        # Title
        title = Paragraph(
            f"{entity.name}",
            self.styles['CustomTitle']
        )
        story.append(title)

        # Entity type and ID
        subtitle = Paragraph(
            f"{entity.entity_type.upper()} - ID: {entity.entity_id}",
            self.styles['Heading2']
        )
        story.append(subtitle)
        story.append(Spacer(1, 20))

        # Add QR code if available
        if "qr_code" in template_data:
            qr_image = self._create_image_from_bytes(
                template_data["qr_code"],
                width=2*inch
            )
            story.append(qr_image)
            story.append(Spacer(1, 20))

        # Data sections
        for section in template_data.get("sections", []):
            story.append(Paragraph(section["title"], self.styles['SectionTitle']))

            # Create table for fields
            table_data = []
            for field_name, field_value in section["fields"].items():
                # Check if this field has special visualization
                viz_config = entity.visualization_config.get(field_name, {})

                if viz_config.get("type") == "qr_code" and field_name in template_data.get("field_qr_codes", {}):
                    # Add QR code for field
                    qr_img = self._create_image_from_data_url(
                        template_data["field_qr_codes"][field_name],
                        width=1*inch
                    )
                    table_data.append([
                        Paragraph(self._format_field_name(field_name), self.styles['FieldLabel']),
                        qr_img
                    ])
                else:
                    # Regular field
                    table_data.append([
                        Paragraph(self._format_field_name(field_name), self.styles['FieldLabel']),
                        Paragraph(str(field_value), self.styles['FieldValue'])
                    ])

            if table_data:
                table = Table(table_data, colWidths=[3*inch, 4*inch])
                table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                    ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]))
                story.append(table)
                story.append(Spacer(1, 20))

        # Signatures section
        if template_data.get("signatures"):
            story.append(PageBreak())
            story.append(Paragraph("Digital Signatures", self.styles['SectionTitle']))

            for sig in template_data["signatures"]:
                sig_content = []
                if sig.get("qr_code"):
                    sig_content.append(
                        self._create_image_from_data_url(sig["qr_code"], width=1*inch)
                    )
                sig_content.append(
                    Paragraph(
                        f"{sig['signer_name']} ({sig['role']})<br/>Signed: {sig['signed_at']}",
                        self.styles['Normal']
                    )
                )
                story.append(KeepTogether(sig_content))
                story.append(Spacer(1, 10))

        # Footer
        story.append(Spacer(1, 30))
        footer = Paragraph(
            f"Generated on {template_data['generated_at']}",
            self.styles['Normal']
        )
        story.append(footer)

        # Build PDF
        doc.build(story)
        buffer.seek(0)
        return buffer.read()

    async def _generate_html_to_pdf(
        self,
        entity: LegalEntity,
        template_name: str,
        template_data: Dict[str, Any]
    ) -> bytes:
        """Generate PDF from HTML template using xhtml2pdf"""
        # Render HTML
        html = await self.template_engine.render_entity(
            entity,
            template_name,
            template_data
        )

        # Convert HTML to PDF
        buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(
            html,
            dest=buffer,
            encoding='utf-8'
        )

        if pisa_status.err:
            raise Exception(f"PDF generation failed: {pisa_status.err}")

        buffer.seek(0)
        return buffer.read()

    async def generate_field_visualization(
        self,
        field_name: str,
        field_value: Any,
        visualization_type: str,
        options: Dict[str, Any] = None
    ) -> bytes:
        """Generate visualization for a specific field"""
        options = options or {}

        if visualization_type == "qr_code":
            return await self.qr_generator.generate_qr_code(
                str(field_value),
                size=options.get("size", 200)
            )
        elif visualization_type == "structured_pdf":
            # Generate mini PDF for structured data
            return await self._generate_structured_field_pdf(
                field_name,
                field_value,
                options.get("template", "field_default")
            )
        else:
            # Default: return text representation
            return str(field_value).encode('utf-8')

    async def _generate_structured_field_pdf(
        self,
        field_name: str,
        field_value: Any,
        template_name: str
    ) -> bytes:
        """Generate PDF for a structured field (JSON/dict)"""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        story = []

        # Title
        story.append(Paragraph(
            self._format_field_name(field_name),
            self.styles['Heading1']
        ))
        story.append(Spacer(1, 12))

        # Format the data
        formatted_data = self.data_formatter.format_value(field_value)

        if isinstance(field_value, dict):
            # Create table for dict
            table_data = []
            for key, value in field_value.items():
                table_data.append([
                    Paragraph(self._format_field_name(key), self.styles['FieldLabel']),
                    Paragraph(str(value), self.styles['FieldValue'])
                ])

            table = Table(table_data)
            table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(table)
        elif isinstance(field_value, list):
            # Create list
            for item in field_value:
                story.append(Paragraph(f"• {str(item)}", self.styles['Normal']))
        else:
            # Simple value
            story.append(Paragraph(formatted_data, self.styles['Normal']))

        doc.build(story)
        buffer.seek(0)
        return buffer.read()

    def _organize_data_sections(self, entity: LegalEntity) -> List[Dict[str, Any]]:
        """Organize entity data into sections based on display config"""
        display_config = entity.entity_display_config or {}
        sections = display_config.get("sections", [])

        if sections:
            # Use configured sections
            organized = []
            for section in sections:
                section_data = {
                    "title": section["title"],
                    "fields": {}
                }
                for field_name in section.get("fields", []):
                    if field_name in entity.data:
                        section_data["fields"][field_name] = entity.data[field_name]
                organized.append(section_data)
            return organized
        else:
            # Default: single section with all data
            return [{
                "title": "Entity Information",
                "fields": entity.data
            }]

    def _format_field_name(self, field_name: str) -> str:
        """Format field name for display"""
        return field_name.replace("_", " ").title()

    def _to_data_url(self, image_bytes: bytes) -> str:
        """Convert image bytes to data URL"""
        base64_str = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/png;base64,{base64_str}"

    def _create_image_from_bytes(self, image_bytes: bytes, width: float) -> Image:
        """Create ReportLab Image from bytes"""
        buffer = io.BytesIO(image_bytes)
        return Image(buffer, width=width)

    def _create_image_from_data_url(self, data_url: str, width: float) -> Image:
        """Create ReportLab Image from data URL"""
        # Extract base64 data
        base64_str = data_url.split(",")[1]
        image_bytes = base64.b64decode(base64_str)
        return self._create_image_from_bytes(image_bytes, width)