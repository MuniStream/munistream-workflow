"""El PDF firmado se arma con una copia de la entidad; esa copia tiene que
conservar el vencimiento (`valid_until`), o un título prorrogado imprime sin
su vigencia nueva."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.legal_entity import LegalEntity
from app.services.visualizers.signed_pdf_visualizer import SignedPDFVisualizer


class _Generador:
    def __init__(self):
        self.entidad = None

    async def generate_entity_report(self, entity, **kwargs):
        self.entidad = entity
        return b"%PDF-1.4 prueba"


async def test_la_copia_conserva_valid_until():
    vis = SignedPDFVisualizer({"verify_signatures": False})
    vis.report_generator = _Generador()
    entidad = LegalEntity.model_construct(
        entity_id="e-1", entity_type="concesion_acuacultura", owner_user_id="u",
        name="Título", data={"signature": {"subject": "Firmante"}},
        visualization_config=None, entity_display_config=None, status="active",
        verified=False, created_at=datetime(2022, 3, 1), updated_at=datetime(2026, 10, 12),
        valid_until=datetime(2032, 3, 1), relationships=[], created_by_workflow=None,
    )
    await vis.generate_pdf(entidad)
    assert vis.report_generator.entidad.valid_until == datetime(2032, 3, 1)
    assert "signature_verification" in vis.report_generator.entidad.data
