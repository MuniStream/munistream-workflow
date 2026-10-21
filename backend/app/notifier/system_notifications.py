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
    attach_documents: bool = False
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


# Template variables (see hook.NotificationDispatcher._build_context):
# remitente, ciudadano.{nombre,email,telefono}, workflow.{id,nombre},
# instancia.{id,folio}, paso.{id,nombre}, url_tramite, resultado.{codigo,
# rechazado,mensaje}, documento.{id,nombre,tipo,folio}, documentos[], adjuntos,
# evento.{id,tipo,datos}. Every key is always defined.

_TRAMITE_RECIBIDO_ES_EMAIL = _email(
    subject="Recibimos su trámite: {{ workflow.nombre }}",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Le confirmamos que recibimos su trámite \"{{ workflow.nombre }}\" "
        "con folio {{ instancia.folio }}.\n\n"
        "{% if url_tramite %}Puede consultar su estado en cualquier momento en: {{ url_tramite }}\n\n{% endif %}"
        "Atentamente,\n{{ remitente }}"
    ),
)
_TRAMITE_RECIBIDO_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: recibimos su trámite "
    "\"{{ workflow.nombre }}\" (folio {{ instancia.folio }}). Le avisaremos cuando avance."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)
_TRAMITE_RECIBIDO_EN_EMAIL = _email(
    subject="We received your request: {{ workflow.nombre }}",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "We confirm that we received your request \"{{ workflow.nombre }}\" "
        "with reference {{ instancia.folio }}.\n\n"
        "{% if url_tramite %}You can check its status at any time at: {{ url_tramite }}\n\n{% endif %}"
        "Sincerely,\n{{ remitente }}"
    ),
)
_TRAMITE_RECIBIDO_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: we received your request "
    "\"{{ workflow.nombre }}\" (reference {{ instancia.folio }}). We will let you know when it progresses."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)


_PASO_AVANZADO_ES_EMAIL = _email(
    subject="Su trámite avanzó: {{ workflow.nombre }}",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Su trámite \"{{ workflow.nombre }}\" (folio {{ instancia.folio }}) "
        "concluyó la etapa \"{{ paso.nombre }}\".\n\n"
        "{% if url_tramite %}Puede revisar el detalle en: {{ url_tramite }}\n\n{% endif %}"
        "Atentamente,\n{{ remitente }}"
    ),
)
_PASO_AVANZADO_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite \"{{ workflow.nombre }}\" "
    "(folio {{ instancia.folio }}) concluyó la etapa \"{{ paso.nombre }}\"."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)
_PASO_AVANZADO_EN_EMAIL = _email(
    subject="Your request advanced: {{ workflow.nombre }}",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "Your request \"{{ workflow.nombre }}\" (reference {{ instancia.folio }}) "
        "completed the stage \"{{ paso.nombre }}\".\n\n"
        "{% if url_tramite %}You can review the details at: {{ url_tramite }}\n\n{% endif %}"
        "Sincerely,\n{{ remitente }}"
    ),
)
_PASO_AVANZADO_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request \"{{ workflow.nombre }}\" "
    "(reference {{ instancia.folio }}) completed the stage \"{{ paso.nombre }}\"."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)


_MODIFICACIONES_ES_EMAIL = _email(
    subject="Su trámite requiere modificaciones: {{ workflow.nombre }}",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "El revisor de su trámite \"{{ workflow.nombre }}\" (folio "
        "{{ instancia.folio }}) solicitó modificaciones.\n\n"
        "{% if url_tramite %}Ingrese para atender la solicitud: {{ url_tramite }}\n\n{% endif %}"
        "Atentamente,\n{{ remitente }}"
    ),
)
_MODIFICACIONES_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite \"{{ workflow.nombre }}\" "
    "(folio {{ instancia.folio }}) requiere modificaciones. Ingrese al portal para atenderlas."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)
_MODIFICACIONES_EN_EMAIL = _email(
    subject="Your request requires changes: {{ workflow.nombre }}",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "The reviewer of your request \"{{ workflow.nombre }}\" (reference "
        "{{ instancia.folio }}) requested changes.\n\n"
        "{% if url_tramite %}Sign in to respond: {{ url_tramite }}\n\n{% endif %}"
        "Sincerely,\n{{ remitente }}"
    ),
)
_MODIFICACIONES_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request \"{{ workflow.nombre }}\" "
    "(reference {{ instancia.folio }}) requires changes. Sign in to the citizen portal to respond."
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)


_DOCUMENTOS_ES = (
    "{% if documentos %}"
    "{% if adjuntos %}Se adjunta{% if documentos|length > 1 %}n los documentos emitidos{% else %} el documento emitido{% endif %}:"
    "{% else %}Documentos emitidos, disponibles en el portal:{% endif +%}\n"
    "{% for d in documentos %}- {{ d.nombre }}{% if d.folio %} (folio {{ d.folio }}){% endif +%}\n{% endfor +%}\n"
    "{% endif %}"
)
_DOCUMENTOS_EN = (
    "{% if documentos %}"
    "{% if adjuntos %}Attached {% if documentos|length > 1 %}are the issued documents{% else %}is the issued document{% endif %}:"
    "{% else %}Issued documents, available on the portal:{% endif +%}\n"
    "{% for d in documentos %}- {{ d.nombre }}{% if d.folio %} (reference {{ d.folio }}){% endif +%}\n{% endfor +%}\n"
    "{% endif %}"
)

_RESOLUCION_ES_EMAIL = _email(
    subject=(
        "{% if resultado.rechazado %}Su trámite fue rechazado{% else %}Su trámite concluyó{% endif %}"
        ": {{ workflow.nombre }}"
    ),
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "{% if resultado.rechazado %}"
        "Le informamos que su trámite \"{{ workflow.nombre }}\" (folio {{ instancia.folio }}) fue rechazado.\n\n"
        "{% if resultado.mensaje %}Motivo: {{ resultado.mensaje }}\n\n{% endif %}"
        "{% else %}"
        "Le informamos que su trámite \"{{ workflow.nombre }}\" (folio {{ instancia.folio }}) concluyó.\n\n"
        "{% endif %}"
        + _DOCUMENTOS_ES +
        "{% if url_tramite %}Puede consultar el detalle en: {{ url_tramite }}\n\n{% endif %}"
        "Atentamente,\n{{ remitente }}"
    ),
)
_RESOLUCION_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: su trámite \"{{ workflow.nombre }}\" "
    "(folio {{ instancia.folio }}) "
    "{% if resultado.rechazado %}fue rechazado.{% if resultado.mensaje %} Motivo: {{ resultado.mensaje }}{% endif %}"
    "{% else %}concluyó.{% endif %}"
    "{% if documentos and adjuntos %} Le enviamos {% if documentos|length > 1 %}los documentos emitidos{% else %}el documento emitido{% endif %} a continuación.{% endif %}"
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)
_RESOLUCION_EN_EMAIL = _email(
    subject=(
        "{% if resultado.rechazado %}Your request was rejected{% else %}Your request is complete{% endif %}"
        ": {{ workflow.nombre }}"
    ),
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "{% if resultado.rechazado %}"
        "Your request \"{{ workflow.nombre }}\" (reference {{ instancia.folio }}) was rejected.\n\n"
        "{% if resultado.mensaje %}Reason: {{ resultado.mensaje }}\n\n{% endif %}"
        "{% else %}"
        "Your request \"{{ workflow.nombre }}\" (reference {{ instancia.folio }}) is complete.\n\n"
        "{% endif %}"
        + _DOCUMENTOS_EN +
        "{% if url_tramite %}You can review the details at: {{ url_tramite }}\n\n{% endif %}"
        "Sincerely,\n{{ remitente }}"
    ),
)
_RESOLUCION_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: your request \"{{ workflow.nombre }}\" "
    "(reference {{ instancia.folio }}) "
    "{% if resultado.rechazado %}was rejected.{% if resultado.mensaje %} Reason: {{ resultado.mensaje }}{% endif %}"
    "{% else %}is complete.{% endif %}"
    "{% if documentos and adjuntos %} The issued {% if documentos|length > 1 %}documents follow{% else %}document follows{% endif %}.{% endif %}"
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)


_ENTIDAD_ES_EMAIL = _email(
    subject="Documento emitido: {{ documento.nombre }}",
    body=(
        "Estimado(a) {{ ciudadano.nombre }}:\n\n"
        "Se emitió el documento \"{{ documento.nombre }}\""
        "{% if documento.folio %} con folio {{ documento.folio }}{% endif %} "
        "en su trámite \"{{ workflow.nombre }}\" (folio {{ instancia.folio }}).\n\n"
        "{% if adjuntos %}Lo encontrará adjunto a este correo. {% endif %}"
        "También está disponible en su cartera de documentos del portal.\n\n"
        "{% if url_tramite %}Detalle del trámite: {{ url_tramite }}\n\n{% endif %}"
        "Atentamente,\n{{ remitente }}"
    ),
)
_ENTIDAD_ES_WA = _whatsapp(
    "Estimado(a) {{ ciudadano.nombre }}: se emitió el documento "
    "\"{{ documento.nombre }}\"{% if documento.folio %} (folio {{ documento.folio }}){% endif %} "
    "en su trámite \"{{ workflow.nombre }}\".{% if adjuntos %} Se lo enviamos a continuación.{% endif %}"
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
)
_ENTIDAD_EN_EMAIL = _email(
    subject="Document issued: {{ documento.nombre }}",
    body=(
        "Dear {{ ciudadano.nombre }}:\n\n"
        "The document \"{{ documento.nombre }}\""
        "{% if documento.folio %} with reference {{ documento.folio }}{% endif %} "
        "was issued on your request \"{{ workflow.nombre }}\" (reference {{ instancia.folio }}).\n\n"
        "{% if adjuntos %}You will find it attached to this email. {% endif %}"
        "It is also available in your document wallet on the portal.\n\n"
        "{% if url_tramite %}Request details: {{ url_tramite }}\n\n{% endif %}"
        "Sincerely,\n{{ remitente }}"
    ),
)
_ENTIDAD_EN_WA = _whatsapp(
    "Dear {{ ciudadano.nombre }}: the document \"{{ documento.nombre }}\""
    "{% if documento.folio %} (reference {{ documento.folio }}){% endif %} was issued on your request "
    "\"{{ workflow.nombre }}\".{% if adjuntos %} It follows below.{% endif %}"
    "{% if url_tramite %} {{ url_tramite }}{% endif %}"
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
            "es": "Aviso cuando su trámite concluye una etapa relevante, como el envío o la revisión.",
            "en": "Notice when your request completes a relevant stage, such as submission or review.",
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
        attach_documents=True,
        title_i18n={
            "es": "Resolución final",
            "en": "Final resolution",
        },
        description_i18n={
            "es": "Aviso al concluir su trámite, con el resultado y los documentos emitidos.",
            "en": "Notice when your request concludes, with the outcome and the issued documents.",
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
        attach_documents=True,
        title_i18n={
            "es": "Emisión de documentos",
            "en": "Document issued",
        },
        description_i18n={
            "es": "Aviso con el documento en PDF cada vez que se emite uno en su trámite.",
            "en": "Notice with the PDF document each time one is issued on your request.",
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
