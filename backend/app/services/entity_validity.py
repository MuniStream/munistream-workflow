"""
Vigencia de una entidad.

Hasta ahora las entidades no guardaban fecha de vencimiento: solo podía deducirse
de `data["vigencia_anos"]`, que apenas una parte declara y que no sabe nada de
prórrogas ni revocaciones. `LegalEntity.valid_until` la convierte en un dato de
primera clase; lo de aquí lo calcula al emitir y lo interpreta al leer.

Vive aparte del selector de entidades a propósito: estas reglas cambian con la
normativa de cada trámite, no con la interfaz que las muestra.
"""

from datetime import datetime
from typing import Any, Dict, Optional


def calcular_vencimiento(desde: Optional[datetime], anos: Any) -> Optional[datetime]:
    """
    Fecha de vencimiento a partir de una fecha de emisión y los años de vigencia.

    Devuelve None cuando no hay años declarados: sin ese dato no hay vencimiento
    que calcular, e inventar uno sería peor que no tenerlo porque el ciudadano
    tomaría decisiones con él.
    """
    if not isinstance(desde, datetime) or not anos:
        return None
    try:
        anos = int(anos)
    except (TypeError, ValueError):
        return None
    if anos <= 0:
        return None
    try:
        return desde.replace(year=desde.year + anos)
    except ValueError:
        # 29 de febrero hacia un año no bisiesto.
        return desde.replace(year=desde.year + anos, day=28)


def vigencia_de(entidad) -> Optional[Dict[str, Any]]:
    """
    Vigencia de la entidad, con su procedencia.

    `valid_until` es el dato; los años declarados son solo una inferencia para
    las entidades emitidas antes de que ese campo existiera. Por eso el guardado
    manda: una prórroga o una revocación mueven la fecha sin tocar los años.

    `origen` viaja hasta la tarjeta para que la interfaz pueda matizar una fecha
    deducida en vez de presentarla como si fuera oficial.
    """
    guardada = getattr(entidad, "valid_until", None)
    if isinstance(guardada, datetime):
        return {
            "hasta": guardada,
            "vencida": guardada < datetime.utcnow(),
            "origen": "guardada",
        }

    datos = getattr(entidad, "data", None) or {}
    derivada = calcular_vencimiento(getattr(entidad, "created_at", None), datos.get("vigencia_anos"))
    if derivada is None:
        return None
    return {
        "hasta": derivada,
        "vencida": derivada < datetime.utcnow(),
        "origen": "derivada",
    }


def vencimiento_inicial(
    data: Optional[Dict[str, Any]], emitida: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Fecha de vencimiento con la que nace una entidad.

    Se lee del `data` que el workflow ya arma, sin obligar a tocar ninguno: los
    que declaran `vigencia_anos` empiezan a fijar la fecha solos, y un
    `valid_until` explícito la gana porque hay resoluciones cuya vigencia no sale
    de una cuenta de años.

    Nunca lanza: emitir la entidad es lo que importa, y una vigencia mal escrita
    en un workflow no puede impedir que el trámite termine. Se devuelve None y la
    entidad queda sin vencimiento declarado.
    """
    datos = data or {}
    emitida = emitida or datetime.utcnow()

    explicito = datos.get("valid_until")
    if isinstance(explicito, datetime):
        return explicito
    if isinstance(explicito, str) and explicito.strip():
        try:
            return datetime.fromisoformat(explicito.strip()[:19])
        except ValueError:
            return None

    return calcular_vencimiento(emitida, datos.get("vigencia_anos"))
