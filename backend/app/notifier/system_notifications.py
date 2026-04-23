"""System-shipped notifications catalog.

These are the default notifications that every tenant gets out-of-the-box. Each
entry results in one `NotificationTrigger` (with `is_system=True`) and a set of
`NotificationTemplate` rows (one per locale x channel) seeded on backend
startup. The admin can toggle `active` or edit the template bodies, but the
underlying trigger is not meant to be deleted.

The `key` on each `SystemNotification` is reused as:
- the `template_key` of the matching `NotificationTrigger`
- the key in `Customer.notification_preferences.per_notification`

So citizens who toggle "avance de paso / whatsapp off" hit the `paso_avanzado`
entry in their per-notification prefs and the dispatcher skips that channel.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..models.workflow import EventType
from .models import NotificationChannel


SUPPORTED_LOCALES: Tuple[str, ...] = ("es", "en")


@dataclass(frozen=True)
class SystemTemplate:
    subject: str  # ignored by the WhatsApp handler
    body: str


@dataclass(frozen=True)
class SystemNotification:
    key: str
    event_type: EventType
    title_i18n: Dict[str, str]
    description_i18n: Dict[str, str]
    default_channels: Tuple[NotificationChannel, ...]
    templates: Dict[Tuple[str, NotificationChannel], SystemTemplate] = field(default_factory=dict)

    def title(self, locale: str) -> str:
        return self.title_i18n.get(locale) or self.title_i18n["es"]

    def description(self, locale: str) -> str:
        return self.description_i18n.get(locale) or self.description_i18n["es"]


_BOTH_CHANNELS: Tuple[NotificationChannel, ...] = (
    NotificationChannel.EMAIL,
    NotificationChannel.WHATSAPP,
)


def _email(subject: str, body: str) -> SystemTemplate:
    return SystemTemplate(subject=subject, body=body)


def _whatsapp(body: str) -> SystemTemplate:
    # WhatsApp templates ignore subject but the model requires the field, so we
    # carry an empty string here and let the handler skip it.
    return SystemTemplate(subject="", body=body)


_TRAMITE_RECIBIDO_ES_EMAIL = _email(
    subject="Confirmación de recepción de su trámite",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Le confirmamos que hemos recibido su trámite con identificador "
        "{{ instancia.id }}.\n\n"
        "Puede consultar el estado de su trámite en cualquier momento desde el "
        "portal del ciudadano.\n\n"
        "Atentamente,\nMuniStream"
    ),
)
_TRAMITE_RECIBIDO_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: hemos recibido su trámite "
    "{{ instancia.id }}. Le notificaremos cuando avance."
)
_TRAMITE_RECIBIDO_EN_EMAIL = _email(
    subject="Your request has been received",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "We confirm that your request has been received with identifier "
        "{{ instancia.id }}.\n\n"
        "You can check its status at any time from the citizen portal.\n\n"
        "Sincerely,\nMuniStream"
    ),
)
_TRAMITE_RECIBIDO_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: we have received your request "
    "{{ instancia.id }}. We will let you know when it progresses."
)


_PASO_AVANZADO_ES_EMAIL = _email(
    subject="Su trámite avanzó a una nueva etapa",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Su trámite {{ instancia.id }} avanzó al paso "
        "\"{{ evento.datos.current_step }}\".\n\n"
        "Puede revisar el detalle desde el portal del ciudadano.\n\n"
        "Atentamente,\nMuniStream"
    ),
)
_PASO_AVANZADO_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite {{ instancia.id }} "
    "avanzó al paso: {{ evento.datos.current_step }}."
)
_PASO_AVANZADO_EN_EMAIL = _email(
    subject="Your request advanced to a new stage",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "Your request {{ instancia.id }} advanced to the step "
        "\"{{ evento.datos.current_step }}\".\n\n"
        "You can review the details from the citizen portal.\n\n"
        "Sincerely,\nMuniStream"
    ),
)
_PASO_AVANZADO_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request {{ instancia.id }} "
    "advanced to the step: {{ evento.datos.current_step }}."
)


_MODIFICACIONES_ES_EMAIL = _email(
    subject="Se requieren modificaciones en su trámite",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "El revisor asignado a su trámite {{ instancia.id }} ha solicitado "
        "modificaciones.\n\n"
        "Por favor ingrese al portal del ciudadano para atender la "
        "solicitud.\n\n"
        "Atentamente,\nMuniStream"
    ),
)
_MODIFICACIONES_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite {{ instancia.id }} "
    "requiere modificaciones. Ingrese al portal para atender la solicitud."
)
_MODIFICACIONES_EN_EMAIL = _email(
    subject="Modifications required on your request",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "The reviewer assigned to your request {{ instancia.id }} has "
        "requested modifications.\n\n"
        "Please sign in to the citizen portal to respond to the request.\n\n"
        "Sincerely,\nMuniStream"
    ),
)
_MODIFICACIONES_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request {{ instancia.id }} "
    "requires modifications. Sign in to the citizen portal to respond."
)


_RESOLUCION_ES_EMAIL = _email(
    subject="Resolución final de su trámite",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Su trámite {{ instancia.id }} ha sido resuelto.\n\n"
        "Puede consultar el resultado en el portal del ciudadano.\n\n"
        "Atentamente,\nMuniStream"
    ),
)
_RESOLUCION_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite {{ instancia.id }} "
    "ha sido resuelto. Consulte el detalle en el portal del ciudadano."
)
_RESOLUCION_EN_EMAIL = _email(
    subject="Final resolution of your request",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "Your request {{ instancia.id }} has been resolved.\n\n"
        "You can review the outcome from the citizen portal.\n\n"
        "Sincerely,\nMuniStream"
    ),
)
_RESOLUCION_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request {{ instancia.id }} "
    "has been resolved. Review the details on the citizen portal."
)


_ENTIDAD_ES_EMAIL = _email(
    subject="Documento disponible en su trámite",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Se ha emitido un nuevo documento en su trámite "
        "{{ instancia.id }}.\n\n"
        "Puede descargarlo desde el portal del ciudadano.\n\n"
        "Atentamente,\nMuniStream"
    ),
)
_ENTIDAD_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: hay un nuevo documento disponible "
    "en su trámite {{ instancia.id }}. Consulte el portal del ciudadano."
)
_ENTIDAD_EN_EMAIL = _email(
    subject="Document available on your request",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "A new document has been issued on your request "
        "{{ instancia.id }}.\n\n"
        "You can download it from the citizen portal.\n\n"
        "Sincerely,\nMuniStream"
    ),
)
_ENTIDAD_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: a new document is available on your "
    "request {{ instancia.id }}. Review it on the citizen portal."
)


SYSTEM_NOTIFICATIONS: List[SystemNotification] = [
    SystemNotification(
        key="tramite_recibido",
        event_type=EventType.STARTED,
        title_i18n={
            "es": "Recepción del trámite",
            "en": "Request received",
        },
        description_i18n={
            "es": "Aviso inmediato cuando su trámite es recibido por la plataforma.",
            "en": "Immediate notice when your request is received by the platform.",
        },
        default_channels=_BOTH_CHANNELS,
        templates={
            ("es", NotificationChannel.EMAIL): _TRAMITE_RECIBIDO_ES_EMAIL,
            ("es", NotificationChannel.WHATSAPP): _TRAMITE_RECIBIDO_ES_WA,
            ("en", NotificationChannel.EMAIL): _TRAMITE_RECIBIDO_EN_EMAIL,
            ("en", NotificationChannel.WHATSAPP): _TRAMITE_RECIBIDO_EN_WA,
        },
    ),
    SystemNotification(
        key="paso_avanzado",
        event_type=EventType.STEP_ADVANCED,
        title_i18n={
            "es": "Avance de paso",
            "en": "Step advanced",
        },
        description_i18n={
            "es": "Aviso cada vez que su trámite pasa al siguiente paso.",
            "en": "Notice each time your request moves to the next step.",
        },
        default_channels=_BOTH_CHANNELS,
        templates={
            ("es", NotificationChannel.EMAIL): _PASO_AVANZADO_ES_EMAIL,
            ("es", NotificationChannel.WHATSAPP): _PASO_AVANZADO_ES_WA,
            ("en", NotificationChannel.EMAIL): _PASO_AVANZADO_EN_EMAIL,
            ("en", NotificationChannel.WHATSAPP): _PASO_AVANZADO_EN_WA,
        },
    ),
    SystemNotification(
        key="modificaciones_solicitadas",
        event_type=EventType.MODIFICATION_REQUESTED,
        title_i18n={
            "es": "Solicitud de modificaciones",
            "en": "Modification requested",
        },
        description_i18n={
            "es": "Aviso cuando el revisor le solicita cambios o documentos adicionales.",
            "en": "Notice when the reviewer asks you for changes or extra documents.",
        },
        default_channels=_BOTH_CHANNELS,
        templates={
            ("es", NotificationChannel.EMAIL): _MODIFICACIONES_ES_EMAIL,
            ("es", NotificationChannel.WHATSAPP): _MODIFICACIONES_ES_WA,
            ("en", NotificationChannel.EMAIL): _MODIFICACIONES_EN_EMAIL,
            ("en", NotificationChannel.WHATSAPP): _MODIFICACIONES_EN_WA,
        },
    ),
    SystemNotification(
        key="resolucion_final",
        event_type=EventType.COMPLETED,
        title_i18n={
            "es": "Resolución final",
            "en": "Final resolution",
        },
        description_i18n={
            "es": "Aviso al concluir su trámite, con el resultado de la resolución.",
            "en": "Notice when your request concludes, with the final outcome.",
        },
        default_channels=_BOTH_CHANNELS,
        templates={
            ("es", NotificationChannel.EMAIL): _RESOLUCION_ES_EMAIL,
            ("es", NotificationChannel.WHATSAPP): _RESOLUCION_ES_WA,
            ("en", NotificationChannel.EMAIL): _RESOLUCION_EN_EMAIL,
            ("en", NotificationChannel.WHATSAPP): _RESOLUCION_EN_WA,
        },
    ),
    SystemNotification(
        key="entidad_emitida",
        event_type=EventType.ENTITY_CREATED,
        title_i18n={
            "es": "Emisión de documentos",
            "en": "Document issued",
        },
        description_i18n={
            "es": "Aviso cuando se emite un nuevo documento o entidad en su trámite.",
            "en": "Notice when a new document or entity is issued on your request.",
        },
        default_channels=_BOTH_CHANNELS,
        templates={
            ("es", NotificationChannel.EMAIL): _ENTIDAD_ES_EMAIL,
            ("es", NotificationChannel.WHATSAPP): _ENTIDAD_ES_WA,
            ("en", NotificationChannel.EMAIL): _ENTIDAD_EN_EMAIL,
            ("en", NotificationChannel.WHATSAPP): _ENTIDAD_EN_WA,
        },
    ),
]


SYSTEM_NOTIFICATION_KEYS = {n.key for n in SYSTEM_NOTIFICATIONS}


def find_by_key(key: str) -> SystemNotification:
    for entry in SYSTEM_NOTIFICATIONS:
        if entry.key == key:
            return entry
    raise KeyError(f"Unknown system notification key: {key}")


__all__ = [
    "SUPPORTED_LOCALES",
    "SystemTemplate",
    "SystemNotification",
    "SYSTEM_NOTIFICATIONS",
    "SYSTEM_NOTIFICATION_KEYS",
    "find_by_key",
]
