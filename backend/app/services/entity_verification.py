"""
Dictamen de verificación de un documento: lo que responde el QR.

`/verify/{entity_id}` declaraba inválido **todo** documento que la dependencia
emitió. La causa era de semántica, no de código: exigía `entity.verified`, y ese
campo no lo pone ningún workflow —0 de 58 entidades en dev lo tienen— porque
significa otra cosa. `verified` es "una persona dio fe de este documento", que
aplica a lo que el ciudadano **sube**: una identificación, un acta. Un documento
que **emitió la propia dependencia** es auténtico por construcción: está en la
base porque el sistema lo generó al completar un trámite, y eso queda registrado
en `created_by_workflow`. Pedirle visto bueno humano al documento que uno mismo
firmó no verifica nada; solo produce un "inválido" falso.

Así que la autenticidad se deriva de lo que ya es cierto —existe, su estado es
vigente, lo creó un trámite— y `verified` se reserva para lo subido.

Esto vive aparte del endpoint por dos razones: es la regla de negocio que dice
cuándo un documento oficial vale, y es lo único que hay que poder probar sin
levantar Mongo ni Keycloak.

**Sobre el checksum.** No es un control de seguridad: es un hash sin llave de
datos públicos, así que cualquiera puede calcularlo. Sirve para lo que sí sirve un
hash impreso: detectar que el papel que tienes enfrente no dice lo mismo que el
registro. Por eso el material tiene que ser **exactamente lo identificatorio e
inmutable** del documento.

Antes incluía `status` —mutable: un QR ya impreso se autoinvalidaba al pasar de
`active` a `vigente`— y un `created_at` con microsegundos, que Mongo trunca a
milisegundos al persistir. Como el PDF se genera con la entidad en memoria y la
verificación recalcula leyendo de Mongo, los hashes diferían en 999 de cada 1000
emisiones y el ciudadano leía "el documento pudo haber sido modificado".

`material_de_checksum` es ahora el único lugar donde se decide qué se hashea, y lo
usan el generador del PDF y el verificador. No pueden volver a divergir.

Los documentos impresos antes de este cambio traen un checksum que ya no se puede
reproducir: sus microsegundos se perdieron al guardarse. Ese checksum ya fallaba,
así que no se pierde nada; los QR sin `?checksum=` siguen verificándose igual.
"""

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from .entity_validity import vigencia_de

# Estados en los que un documento se considera vigente. Conviven tres nombres por
# historia de los workflows; ninguno se puede retirar sin migrar datos.
ESTADOS_VIGENTES = ("active", "vigente", "valid")

# Lo que entra al checksum: identifica el documento y no cambia nunca.
# Deliberadamente **sin** `status`, que sí cambia.
CAMPOS_DEL_CHECKSUM = ("entity_id", "entity_type", "name", "created_at")

LARGO_CHECKSUM = 16


def _a_milisegundos(momento: datetime) -> datetime:
    """
    Recorta la marca de tiempo a la precisión que Mongo conserva.

    BSON guarda milisegundos. Sin este recorte, hashear la entidad en memoria
    (microsegundos) y la leída de la base dan resultados distintos.
    """
    return momento.replace(microsecond=(momento.microsecond // 1000) * 1000)


def material_de_checksum(entidad) -> str:
    """
    Cadena canónica que se hashea. Único lugar donde se decide qué entra.

    Formato explícito `clave=valor` por renglón en orden fijo, en vez del
    `str(sorted(dict.items()))` anterior: aquel dependía del `repr` de Python y de
    que ambos lados construyeran el diccionario igual.
    """
    renglones = []
    for campo in CAMPOS_DEL_CHECKSUM:
        valor = getattr(entidad, campo, None)
        if isinstance(valor, datetime):
            valor = _a_milisegundos(valor).isoformat()
        renglones.append(f"{campo}={'' if valor is None else valor}")
    return "\n".join(renglones)


def checksum_de(entidad) -> str:
    """Checksum que va impreso en el QR."""
    material = material_de_checksum(entidad).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:LARGO_CHECKSUM]


def folio_corto(entity_id: Optional[str]) -> str:
    """
    La parte identificatoria del folio, sin el prefijo del tipo.

    Los ids se componen como `{entity_type}_{uuid8}`. El wallet armaba sus URLs
    como `f"{entity_type}_{entity_id[:8]}"` creyendo que `entity_id` era solo el
    uuid: esos 8 caracteres recortaban el prefijo del tipo, así que
    `pescador_rnpa_a1b2c3d4` producía la liga `/verify/pescador_rnpa_pescador`, que
    no corresponde a ningún documento. Todas las credenciales añadidas a Apple o
    Google Wallet apuntaban a la nada.

    Para una liga de verificación no hace falta esta función —`entity.entity_id` ya
    es el folio completo—; sirve para mostrar un folio corto en la credencial.
    """
    if not entity_id:
        return ""
    return entity_id.rsplit("_", 1)[-1]


def es_documento_emitido(entidad) -> bool:
    """
    ¿Lo emitió la dependencia, o lo subió el ciudadano?

    `created_by_workflow` guarda la instancia de trámite que creó la entidad. Si
    está, el documento nació de un trámite completado por el propio sistema.
    """
    return bool(getattr(entidad, "created_by_workflow", None))


def _vencimiento(entidad) -> Optional[datetime]:
    """
    Fecha de vencimiento, preferentemente la guardada.

    Antes se hurgaba en `data["expiry_date"]`/`data["fecha_vencimiento"]` con un
    `except:` desnudo que convertía cualquier formato inesperado en "fecha
    inválida" — un motivo de rechazo que el ciudadano leía como sospecha. Ahora la
    fuente es `valid_until`, con la inferencia por años solo para las entidades
    emitidas antes de que ese campo existiera.
    """
    vigencia = vigencia_de(entidad)
    return vigencia["until"] if vigencia else None


def dictaminar(
    entidad,
    checksum: Optional[str] = None,
    ahora: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Dictamen completo de un documento.

    `ahora` se recibe para poder probar el vencimiento sin depender del reloj.

    Los motivos son los que lee quien escanea el QR desde la calle: van en
    español, redactados como una frase, y se acumulan todos en vez de cortar en el
    primero — quien verifica quiere saber todo lo que pasa con el documento.
    """
    ahora = ahora or datetime.utcnow()
    motivos: List[str] = []

    if getattr(entidad, "status", None) not in ESTADOS_VIGENTES:
        motivos.append("El documento no se encuentra vigente.")

    emitido = es_documento_emitido(entidad)
    if not emitido and not getattr(entidad, "verified", False):
        motivos.append("El documento aún no ha sido validado por la autoridad.")

    vence = _vencimiento(entidad)
    if vence is not None and vence < ahora:
        motivos.append(f"El documento venció el {vence.strftime('%d/%m/%Y')}.")

    checksum_valido = True
    calculado = checksum_de(entidad)
    if checksum:
        checksum_valido = checksum.strip().lower() == calculado.lower()
        if not checksum_valido:
            motivos.append("Los datos impresos no coinciden con el registro oficial.")

    return {
        "valid": not motivos,
        "motivos": motivos,
        "issued_by_authority": emitido,
        "valid_until": vence.isoformat() if vence else None,
        "checksum_valid": checksum_valido,
        "checksum_provided": checksum is not None,
        "calculated_checksum": calculado,
    }
