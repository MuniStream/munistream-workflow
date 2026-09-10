"""Normaliza las referencias a archivos que un tramite deja en su ``context``.

Los adjuntos de una instancia no viven en un campo propio del modelo: quedan
esparcidos por ``WorkflowInstance.context`` en tres formas distintas, segun
quien los escribio:

1. **Subida del ciudadano.** ``POST /public/instances/{id}/submit-data`` manda
   cada archivo a S3 y guarda solo la referencia bajo
   ``context["{task_id}_input"][campo]``, con el shape que devuelve
   ``s3_storage.upload_pending_file``: ``{filename, content_type, size,
   s3_key, s3_bucket}``.
2. **Salida del operador de S3.** ``S3UploadOperator`` copia el archivo a su
   destino final y escribe un resumen en ``context["{task_id}_result"]`` con
   ``uploaded_files[]`` (cada entrada con ``s3_key``, ``bucket``, ``url``,
   ``size``) y las listas planas ``s3_keys``/``s3_urls``.
3. **Legado.** Claves ``{paso}_uploaded_files``, y algun dict con ``base64``
   embebido de instancias viejas (el executor purga esos blobs al guardar,
   pero el historico puede tenerlos).

Este modulo recorre el context y devuelve una lista uniforme, para que la API
del admin no tenga que conocer ninguna de las tres formas.

Las ``url`` que quedan guardadas en el context son presignadas con caducidad de
7 dias, asi que no sirven como enlace estable: aqui se ignoran a proposito y el
contenido se sirve siempre a partir de la ``s3_key``, por un proxy que verifica
que el archivo pertenezca a la instancia que se esta consultando.
"""

import hashlib
from typing import Any, Dict, List, Optional

# Profundidad maxima al recorrer el context. El mismo limite que usa
# ``executor._strip_oversized_base64_blobs``: mas alla no hay estructuras reales.
_MAX_DEPTH = 8

# Sufijos de clave de primer nivel que delimitan a que paso pertenece un archivo
# y de donde salio. El orden importa: ``_uploaded_files`` termina en ``_files``,
# asi que se comprueba antes que cualquier sufijo mas corto.
_SUFFIX_ORIGINS = (
    ("_uploaded_files", "legacy"),
    ("_result", "operator_output"),
    ("_input", "citizen_upload"),
)

# Claves de primer nivel que NO son del tramite: son copias del estado de otro
# recurso, embebidas por conveniencia de los operadores.
#
# - `_selected_entities_data`: los datos completos de las entidades que el
#   ciudadano eligio como requisito. Sus archivos pertenecen a la entidad, no a
#   este tramite; el revisor los ve abriendo la entidad en el panel lateral.
# - `_parent_context`: el context entero del tramite padre en las instancias
#   hijas. Sus adjuntos son del padre.
#
# Sin esta exclusion, la pestana de adjuntos mezcla archivos ajenos con los del
# expediente y los etiqueta con un paso que no existe.
_MIRRORED_KEYS = frozenset({"_selected_entities_data", "_parent_context"})

# Claves que guardan lo que el tramite *ofrecio*, no lo que el ciudadano aporto.
#
# `form_config` es el formulario que se le pinta, y cuando un paso ofrece elegir
# entre entidades de la cartera va con las entidades enteras dentro; el cache del
# selector de entidades, igual. Los archivos que cuelgan de ahi son de esas
# entidades y ya se ven en su propio documento.
#
# Sin esto, el expediente de un tramite que subio dos archivos listaba treinta y
# dos: los dos suyos y los de las ocho embarcaciones que el selector le mostro.
def _es_catalogo(key: str) -> bool:
    return key == "form_config" or key.endswith("_discovery_cache")


def _is_s3_ref(value: Any) -> bool:
    """Un dict con ``s3_key`` no vacia es una referencia a un objeto de S3."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("s3_key"), str)
        and bool(value["s3_key"].strip())
    )


def _is_inline_file(value: Any) -> bool:
    """Archivo legado embebido en el context como base64."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("base64"), str)
        and bool(value.get("filename"))
    )


def _split_task_key(key: str) -> tuple[str, str]:
    """Deriva ``(task_id, origen)`` de una clave de primer nivel del context."""
    for suffix, origin in _SUFFIX_ORIGINS:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)], origin
    return key, "context"


def _attachment_id(bucket: str, key: str) -> str:
    """Identificador estable y derivable, para que las URLs sean cacheables.

    No es un secreto: la autorizacion la da el scope de la instancia, no el
    hecho de conocer este id.
    """
    return hashlib.sha1(f"{bucket}/{key}".encode("utf-8")).hexdigest()[:16]


def _filename_from_key(s3_key: str) -> str:
    return s3_key.rsplit("/", 1)[-1] or "archivo"


# El operador de S3 no guarda el tipo de contenido: su resumen solo lleva la
# key, el bucket y el tamano. Sin tipo, la vista previa no sabe si pintar un
# visor de PDF o una imagen, y acababa ofreciendo solo la descarga. Se deduce de
# la extension, que es lo unico que queda del archivo original.
_TIPOS_POR_EXTENSION = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "txt": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "xml": "application/xml",
}


def _content_type(value: Dict[str, Any], filename: str) -> Optional[str]:
    declarado = value.get("content_type")
    if isinstance(declarado, str) and declarado.strip():
        return declarado
    _, _, extension = filename.rpartition(".")
    return _TIPOS_POR_EXTENSION.get(extension.lower())


def _ref_to_attachment(
    value: Dict[str, Any],
    task_id: str,
    field: str,
    origin: str,
    default_bucket: str,
    uploaded_at: Optional[str],
) -> Dict[str, Any]:
    # El operador de S3 llama al bucket ``bucket``; la subida del ciudadano,
    # ``s3_bucket``. Se aceptan ambos.
    bucket = value.get("s3_bucket") or value.get("bucket") or default_bucket
    s3_key = value["s3_key"]
    filename = value.get("filename") or _filename_from_key(s3_key)
    return {
        "attachment_id": _attachment_id(bucket, s3_key),
        "task_id": task_id,
        "field": field,
        "filename": filename,
        "content_type": _content_type(value, filename),
        "size": value.get("size"),
        "s3_bucket": bucket,
        "s3_key": s3_key,
        "origin": origin,
        "uploaded_at": uploaded_at,
        "available": True,
    }


def _inline_to_attachment(
    value: Dict[str, Any],
    task_id: str,
    field: str,
    path: str,
    uploaded_at: Optional[str],
) -> Dict[str, Any]:
    """Archivo legado sin s3_key: se identifica por su ruta en el context."""
    filename = value.get("filename") or "archivo"
    return {
        "attachment_id": hashlib.sha1(path.encode("utf-8")).hexdigest()[:16],
        "task_id": task_id,
        "field": field,
        "filename": filename,
        "content_type": _content_type(value, filename),
        "size": value.get("size"),
        "s3_bucket": None,
        "s3_key": None,
        "context_path": path,
        "origin": "legacy_inline",
        "uploaded_at": uploaded_at,
        "available": True,
    }


def _walk(
    value: Any,
    task_id: str,
    origin: str,
    default_bucket: str,
    uploaded_at: Optional[str],
    field_parts: List[str],
    path: str,
    depth: int,
    out: List[Dict[str, Any]],
    seen: set,
) -> None:
    if depth > _MAX_DEPTH:
        return

    field = ".".join(field_parts) or task_id

    if _is_s3_ref(value):
        att = _ref_to_attachment(value, task_id, field, origin, default_bucket, uploaded_at)
        if att["attachment_id"] not in seen:
            seen.add(att["attachment_id"])
            out.append(att)
        return

    if _is_inline_file(value):
        att = _inline_to_attachment(value, task_id, field, path, uploaded_at)
        if att["attachment_id"] not in seen:
            seen.add(att["attachment_id"])
            out.append(att)
        return

    if isinstance(value, dict):
        for k, v in value.items():
            # ``failed_files`` del operador son intentos fallidos, no adjuntos.
            if k == "failed_files":
                continue
            _walk(
                v, task_id, origin, default_bucket, uploaded_at,
                field_parts + [str(k)], f"{path}.{k}", depth + 1, out, seen,
            )
        return

    if isinstance(value, list):
        for i, v in enumerate(value):
            _walk(
                v, task_id, origin, default_bucket, uploaded_at,
                field_parts, f"{path}[{i}]", depth + 1, out, seen,
            )


def _is_pending_key(s3_key: Optional[str]) -> bool:
    """Las subidas del ciudadano aterrizan bajo `tmp/` hasta que el operador las copia."""
    return bool(s3_key) and s3_key.startswith("tmp/")


def _drop_superseded(attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deja una sola entrada por archivo, la que de verdad se puede servir.

    Un mismo documento llega a aparecer tres veces en el context: embebido en
    base64 por el operador de captura, como referencia temporal bajo `tmp/`
    cuando el ciudadano lo sube, y como objeto definitivo despues de que el
    operador de S3 lo copie. Para el revisor es un solo archivo.

    Ademas de duplicar, las dos primeras formas son enlaces muertos: el
    operador *borra* el objeto temporal tras copiarlo, asi que ofrecer la
    referencia `tmp/` daria un 404 al descargar.

    El emparejamiento va por tamano en bytes, lo unico que las tres copias
    conservan igual (el nombre cambia: el operador le antepone el task_id y la
    fecha). Si no existe copia definitiva del mismo tamano, la entrada se
    mantiene: perder un adjunto seria peor que mostrarlo duplicado.
    """
    tamanos_definitivos = {
        a["size"] for a in attachments
        if a.get("s3_key") and not _is_pending_key(a["s3_key"]) and isinstance(a.get("size"), int)
    }
    if not tamanos_definitivos:
        return attachments

    def superseded(a: Dict[str, Any]) -> bool:
        if a.get("size") not in tamanos_definitivos:
            return False
        return a["origin"] == "legacy_inline" or _is_pending_key(a.get("s3_key"))

    return [a for a in attachments if not superseded(a)]


def collect_instance_attachments(instance) -> List[Dict[str, Any]]:
    """Devuelve los adjuntos de una instancia en un unico shape.

    Recorre ``instance.context`` en vez de buscar claves concretas, porque las
    tres formas historicas anidan a distinta profundidad y por campo. Un mismo
    archivo referenciado dos veces (la subida del ciudadano y el resumen del
    operador apuntan a la misma key tras la copia) aparece una sola vez.
    """
    from . import s3_storage

    context = getattr(instance, "context", None) or {}
    if not isinstance(context, dict):
        return []
    return _attachments_de(context)


def collect_origin_attachments(instance, origin_instance=None) -> List[Dict[str, Any]]:
    """Adjuntos del tramite del que nace esta instancia.

    Una validacion administrativa corre como instancia aparte, y lo que se valida
    son los documentos que el ciudadano entrego en el tramite padre. Sin esto, el
    expediente de origen ensena que se aporto pero no deja abrir ni uno.

    Se prefiere el padre vivo cuando quien llama ya lo cargo: la copia que el hijo
    lleva dentro (`_parent_context`) se congelo al lanzarse la validacion, y los
    archivos que el padre produzca despues no estan en ella. Sin padre a mano se
    usa la copia, que es mejor que nada.

    No amplia el acceso del revisor: esa copia ya viaja dentro de la instancia que
    esta viendo.
    """
    padre = getattr(origin_instance, "context", None)
    if not isinstance(padre, dict):
        context = getattr(instance, "context", None) or {}
        padre = context.get("_parent_context") if isinstance(context, dict) else None
    if not isinstance(padre, dict):
        return []
    return _attachments_de(padre)


def _attachments_de(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        default_bucket = s3_storage.default_bucket()
    except Exception:
        # Sin bucket configurado seguimos listando: cada referencia trae el suyo
        # cuando el operador lo escribio, y la descarga fallara con un error
        # explicito en vez de romper el listado entero del expediente.
        default_bucket = ""

    out: List[Dict[str, Any]] = []
    seen: set = set()

    for key, value in context.items():
        if not isinstance(value, (dict, list)):
            continue
        if key in _MIRRORED_KEYS or _es_catalogo(key):
            continue
        task_id, origin = _split_task_key(key)
        uploaded_at = context.get(f"{task_id}_submitted_at")
        if not isinstance(uploaded_at, str):
            uploaded_at = None
        _walk(
            value, task_id, origin, default_bucket, uploaded_at,
            [], key, 0, out, seen,
        )

    out = _drop_superseded(out)
    out.sort(key=lambda a: (a["task_id"], a["field"], a["filename"]))
    return out


def find_attachment(instance, attachment_id: str, origin_instance=None) -> Optional[Dict[str, Any]]:
    """Busca un adjunto por id dentro de una instancia concreta.

    Es la comprobacion de pertenencia que usa el proxy de descarga: pedir un
    ``attachment_id`` que no salga del context de *esta* instancia no devuelve
    nada, aunque el archivo exista en el bucket.
    """
    for att in collect_instance_attachments(instance) + collect_origin_attachments(instance, origin_instance):
        if att["attachment_id"] == attachment_id:
            return att
    return None


def instance_s3_keys(instance, origin_instance=None) -> set:
    """Conjunto de s3_keys que pertenecen a la instancia, incluidas las heredadas.

    Los del tramite padre cuentan: son lo que hay que revisar en una validacion
    administrativa. Si no se pasa el padre se usan los de la copia embebida, que
    es lo que se ve en pantalla en ese caso.
    """
    todos = collect_instance_attachments(instance) + collect_origin_attachments(instance, origin_instance)
    return {a["s3_key"] for a in todos if a.get("s3_key")}
