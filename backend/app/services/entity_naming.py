"""
Cómo se llama una entidad, y por qué eso no puede salir mal en silencio.

En la cartera del ciudadano aparecían documentos llamados así:

    _selected_entities_data.embarcacion_ids.0.nombre

Es el `name_source` del trámite tal cual. La ruta apunta a un campo que la entidad
seleccionada no trae —no todas las embarcaciones registran `nombre`— y, al no
resolver, el nombre se quedó con la ruta literal. El ciudadano ve una expresión de
programación donde debería ir el nombre de su barco.

Había una guarda para las rutas peladas, pero **excluía las plantillas** `{{ }}` y
el resolvedor de plantillas deja la llave puesta cuando la variable no existe.
Varios trámites usan esa forma, así que el mismo defecto seguía abierto por el
otro lado.

La regla que implementa este módulo es una sola y vale para las dos formas: **lo
que sale nunca parece código**. Ni llaves, ni rutas con puntos, ni el separador
colgando del hueco que no se llenó —dejar "Permiso de Pesca Comercial —" delata el
fallo igual que dejar la llave—. Y cuando no queda nada utilizable, un nombre
legible del tipo antes que su clave técnica: quien abre su cartera no tiene por qué
leer `permiso_pesca_comercial`.

Vive aparte del operador porque es una regla de presentación, y porque es lo único
que hay que poder probar sin Mongo.
"""

import re
from typing import Any, Callable, Dict, Optional

# Campos de la entidad que sirven para nombrarla, en orden de preferencia. Un
# identificador oficial nombra el documento mejor que su tipo.
CAMPOS_PARA_NOMBRAR = (
    "nombre",
    "name",
    "nombre_completo",
    "razon_social",
    "denominacion",
    "matricula",
    "folio",
    "numero_rnpa",
    "clave",
)

# Palabras del tipo que no aportan al nombre legible; el resto se une con "de".
_SEPARADORES_COLGANTES = "—-–:·|,;/ "

_LLAVES = re.compile(r"\{\{[^}]*\}\}")

# Una ruta de contexto: identificadores unidos por puntos, sin espacios. Se exige
# que ninguna parte esté vacía para no confundirla con prosa abreviada
# ("S.C. de R.L." lleva espacios, así que no entra).
_RUTA = re.compile(r"^[A-Za-z_][\w-]*(\.[\w-]+)+$")

_ACENTOS = {
    "embarcacion": "embarcación",
    "concesion": "concesión",
    "autorizacion": "autorización",
    "certificacion": "certificación",
    "identificacion": "identificación",
    "representacion": "representación",
    "acuacultura": "acuacultura",
    "credencial": "credencial",
    "guia": "guía",
    "bitacora": "bitácora",
    "produccion": "producción",
    "recoleccion": "recolección",
    "didactica": "didáctica",
    "electronico": "electrónico",
    "sanitario": "sanitario",
    "juridica": "jurídica",
    "moral": "moral",
}

# Palabras que se unen con "de" para que el tipo se lea como una frase:
# `permiso_pesca_comercial` -> "Permiso de pesca comercial".
_CONECTOR_TRAS = {"permiso", "concesion", "titulo", "aviso", "certificado", "constancia", "guia"}


def hay_sin_resolver(texto: Any) -> bool:
    """
    ¿Este texto todavía parece código en vez de un nombre?

    Cubre las dos formas en que el fallo llega al ciudadano: la llave de plantilla
    sin sustituir y la ruta de contexto usada tal cual.
    """
    if not isinstance(texto, str):
        return False
    if "{{" in texto or "}}" in texto:
        return True
    return bool(_RUTA.match(texto.strip()))


def nombre_legible_de_tipo(entity_type: Optional[str]) -> str:
    """
    Un nombre presentable a partir de la clave del tipo.

    No sustituye al catálogo de tipos —ahí están los nombres oficiales— pero este
    módulo no puede consultar la base: es el último recurso, y tiene que ser algo
    que se pueda leer sin saber cómo está hecho el sistema.
    """
    if not entity_type:
        return "Documento"

    palabras = [p for p in str(entity_type).replace("-", "_").split("_") if p]
    if not palabras:
        return "Documento"

    salida = []
    for i, palabra in enumerate(palabras):
        salida.append(_ACENTOS.get(palabra, palabra))
        if i == 0 and palabra in _CONECTOR_TRAS and len(palabras) > 1:
            salida.append("de")

    frase = " ".join(salida)
    return frase[0].upper() + frase[1:]


def _limpiar_huecos(texto: str) -> str:
    """
    Quita las plantillas que no se resolvieron y el separador que las acompañaba.

    Dejar "Permiso de Pesca Comercial —" delata el fallo igual que dejar la llave:
    el separador solo tiene sentido si hay algo a los dos lados.
    """
    sin_llaves = _LLAVES.sub("", texto)
    # Colapsa los espacios que deja el hueco y recorta separadores de las orillas.
    sin_llaves = re.sub(r"\s+", " ", sin_llaves)
    return sin_llaves.strip(_SEPARADORES_COLGANTES).strip()


def _primer_valor_util(entity_data: Dict[str, Any]) -> Optional[str]:
    """
    El primer campo de la entidad que de verdad la nombre.

    Se exige texto no vacío: un booleano o un número suelto no es un nombre, y
    dejarlo pasar cambia un fallo visible por otro más raro de diagnosticar.
    """
    for campo in CAMPOS_PARA_NOMBRAR:
        valor = (entity_data or {}).get(campo)
        if isinstance(valor, str) and valor.strip() and not hay_sin_resolver(valor):
            return valor.strip()
    return None


def resolver_nombre(
    name_source: Optional[str],
    context: Dict[str, Any],
    entity_data: Dict[str, Any],
    entity_type: Optional[str] = None,
    extraer: Optional[Callable[[Dict[str, Any], str], Any]] = None,
) -> str:
    """
    Nombre definitivo de la entidad. Nunca devuelve algo que parezca código.

    `extraer` permite al operador pasar su propio resolvedor de rutas; por defecto
    se usa uno equivalente para que el módulo se pueda probar solo.

    El orden es: un campo de la propia entidad (el dato más específico), la ruta o
    plantilla resuelta contra el contexto, y por último el respaldo.
    """
    datos = entity_data or {}
    fuente = name_source or ""
    extraer = extraer or _extraer_por_defecto

    # 1. `name_source` puede nombrar un campo de la entidad que se está creando.
    if fuente in datos:
        valor = datos[fuente]
        if isinstance(valor, str) and valor.strip():
            return valor.strip()

    # 2. Plantilla con llaves: se sustituye lo que resuelva y se limpia lo que no.
    if "{{" in fuente:
        resuelto = fuente
        for expresion in re.findall(r"\{\{([^}]+)\}\}", fuente):
            valor = _resolver_alternativas(expresion, context, extraer)
            if valor is not None:
                resuelto = resuelto.replace("{{" + expresion + "}}", valor)
        limpio = _limpiar_huecos(resuelto)
        return limpio if limpio else _respaldo(datos, entity_type)

    # 3. Ruta pelada (o varias alternativas) contra el contexto.
    if fuente:
        texto = _resolver_alternativas(fuente, context, extraer)
        if texto is not None:
            # Una ruta puede resolver a otra ruta si el contexto la arrastra.
            return texto if not hay_sin_resolver(texto) else _respaldo(datos, entity_type)

        # No resolvió. Si era una ruta —o una lista de rutas— no se usa literal:
        # es el fallo reportado. Un título fijo, en cambio, se respeta tal cual.
        parece_ruta = "|" in fuente or hay_sin_resolver(fuente)
        if not parece_ruta:
            return fuente.strip()

    return _respaldo(datos, entity_type)


def _respaldo(entity_data: Dict[str, Any], entity_type: Optional[str]) -> str:
    return _primer_valor_util(entity_data) or nombre_legible_de_tipo(entity_type)


def _resolver_alternativas(
    expresion: str, context: Dict[str, Any], extraer: Callable[[Dict[str, Any], str], Any]
) -> Optional[str]:
    """
    Resuelve una ruta, o la primera de varias alternativas separadas por `|`.

    Las alternativas no son un lujo: un solo campo nunca alcanza. Medido en la
    base, de 38 solicitantes seleccionados, 25 traen `nombre_completo` y 13
    `razon_social` —ninguno los dos, ninguno ninguno—, porque son personas físicas
    y morales. Nombrar el documento con un campo solo deja sin nombre a un tercio
    de la gente, y ese hueco es justo el que producía los nombres rotos.
    """
    for alternativa in expresion.split("|"):
        alternativa = alternativa.strip()
        if not alternativa:
            continue
        valor = extraer(context, alternativa)
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return None


def _extraer_por_defecto(context: Dict[str, Any], ruta: str) -> Any:
    """Resuelve `a.b.0.c` sobre el contexto, con índices de lista."""
    nodo: Any = context
    if ruta in (context or {}):
        return context[ruta]
    for parte in ruta.split("."):
        if isinstance(nodo, dict):
            if parte not in nodo:
                return None
            nodo = nodo[parte]
        elif isinstance(nodo, (list, tuple)):
            if not parte.lstrip("-").isdigit():
                return None
            indice = int(parte)
            if not -len(nodo) <= indice < len(nodo):
                return None
            nodo = nodo[indice]
        else:
            return None
    return nodo
