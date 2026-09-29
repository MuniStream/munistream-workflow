"""
Vigencia de una entidad.

Hasta ahora las entidades no guardaban fecha de vencimiento: solo podía deducirse
de `data["vigencia_anos"]`, que apenas una parte declara y que no sabe nada de
prórrogas ni revocaciones. `LegalEntity.valid_until` la convierte en un dato de
primera clase; lo de aquí lo calcula al emitir y lo interpreta al leer.

Vive aparte del selector de entidades a propósito: estas reglas cambian con la
normativa de cada trámite, no con la interfaz que las muestra.

Sobre el parseo de la vigencia solicitada: cada trámite pide la duración a su
manera —un `select` de años, un texto libre "6 meses", un número suelto— y la
guarda con una clave distinta (`vigencia`, `duracion_solicitada`,
`periodo_concesion`...). En vez de obligar a cada workflow a normalizar, aquí se
interpreta ese texto: se saca el número y la unidad (años/meses/días) y se suma a
la fecha de emisión. Un valor que no se entiende no inventa vencimiento: devuelve
None y la entidad nace sin vigencia declarada.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


# Claves de `data` donde los trámites depositan la vigencia solicitada. El orden
# importa: gana la primera que traiga un valor interpretable. `vigencia_anos` va
# aparte (es un entero de años ya normalizado que varios trámites fijan estático).
_CLAVES_VIGENCIA = (
    "validity",
    "duracion_solicitada",
    "duracion_autorizacion",
    "duracion_proyecto",
    "periodo_concesion",
    "periodo_prorroga_solicitado",
    "periodo_prorroga_anos",
    "periodo_instalacion",
    "periodo_recoleccion",
    "duracion",
    "plazo",
)

# Palabras que denotan cada unidad. Se normaliza sin acentos antes de comparar.
_UNIDADES = {
    "dia": "dias", "dias": "dias", "day": "dias", "days": "dias",
    "semana": "semanas", "semanas": "semanas", "week": "semanas", "weeks": "semanas",
    "mes": "meses", "meses": "meses", "month": "meses", "months": "meses",
    "ano": "anos", "anos": "anos", "year": "anos", "years": "anos",
}


def _sin_acentos(texto: str) -> str:
    return (
        texto.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    )


def parse_duracion(valor: Any, unidad_default: str = "anos") -> Optional[Dict[str, Any]]:
    """
    Interpreta una vigencia solicitada como `{cantidad, unidad}`.

    Acepta un entero/decimal (se asume `unidad_default`, años por convención) o un
    texto tipo "5 años", "30 días", "6 meses", "1 año". Si no hay número, o el
    número no es positivo, devuelve None: sin una cantidad válida no hay periodo.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):  # bool es subclase de int; nunca es una duración.
        return None
    if isinstance(valor, (int, float)):
        cantidad = int(valor)
        return {"cantidad": cantidad, "unidad": unidad_default} if cantidad > 0 else None

    if not isinstance(valor, str):
        return None
    texto = _sin_acentos(valor.strip())
    if not texto:
        return None

    m = re.search(r"(\d+)", texto)
    if not m:
        return None
    cantidad = int(m.group(1))
    if cantidad <= 0:
        return None

    unidad = unidad_default
    for palabra, canon in _UNIDADES.items():
        if re.search(rf"\b{palabra}\b", texto):
            unidad = canon
            break
    return {"cantidad": cantidad, "unidad": unidad}


def sumar_periodo(desde: datetime, cantidad: int, unidad: str) -> Optional[datetime]:
    """Suma un periodo (`anos`/`meses`/`semanas`/`dias`) a una fecha."""
    if not isinstance(desde, datetime) or not cantidad or cantidad <= 0:
        return None
    if unidad == "dias":
        return desde + timedelta(days=cantidad)
    if unidad == "semanas":
        return desde + timedelta(weeks=cantidad)
    if unidad == "meses":
        total = desde.month - 1 + cantidad
        ano = desde.year + total // 12
        mes = total % 12 + 1
        # Recorte de día para meses cortos (31 de enero + 1 mes -> 28/29 feb).
        import calendar
        dia = min(desde.day, calendar.monthrange(ano, mes)[1])
        return desde.replace(year=ano, month=mes, day=dia)
    # anos (default)
    try:
        return desde.replace(year=desde.year + cantidad)
    except ValueError:
        # 29 de febrero hacia un año no bisiesto.
        return desde.replace(year=desde.year + cantidad, day=28)


def sumar_dias_habiles(desde: datetime, dias: int) -> Optional[datetime]:
    """
    Suma días hábiles (omite sábados y domingos) a una fecha.

    No conoce los días festivos oficiales —eso exige un calendario que cambia cada
    año y por entidad federativa—, así que es una aproximación por fin de semana.
    Suficiente para vigencias cortas como la de la guía de pesca (3 días hábiles);
    si en el futuro se requiere exactitud, aquí es donde entra el calendario DOF.
    """
    if not isinstance(desde, datetime) or not dias or dias <= 0:
        return None
    fecha = desde
    restantes = dias
    while restantes > 0:
        fecha = fecha + timedelta(days=1)
        if fecha.weekday() < 5:  # 0-4 = lunes a viernes
            restantes -= 1
    return fecha


def calcular_vencimiento(desde: Optional[datetime], anos: Any) -> Optional[datetime]:
    """
    Fecha de vencimiento a partir de una fecha de emisión y los años de vigencia.

    Se mantiene por compatibilidad (lo usa la inferencia de entidades viejas).
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
    return sumar_periodo(desde, anos, "anos")


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
            "until": guardada,
            "expired": guardada < datetime.utcnow(),
            "source": "stored",
        }

    datos = getattr(entidad, "data", None) or {}
    derivada = calcular_vencimiento(getattr(entidad, "created_at", None), datos.get("vigencia_anos"))
    if derivada is None:
        return None
    return {
        "until": derivada,
        "expired": derivada < datetime.utcnow(),
        "source": "derived",
    }


def vencimiento_inicial(
    data: Optional[Dict[str, Any]], emitida: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Fecha de vencimiento con la que nace una entidad.

    Prioridad (gana la primera que resuelva):
      1. `valid_until` explícito (datetime o ISO) — hay resoluciones cuya vigencia
         no sale de una cuenta: una prórroga con fecha fija, una fecha del oficio.
      2. `vigencia_anos` entero — el atajo histórico que varios trámites fijan.
      3. La vigencia solicitada en texto (`vigencia`, `duracion_solicitada`, ...),
         interpretada como número + unidad y sumada a la fecha de emisión.

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

    # `vigencia_anos` entero tiene prioridad sobre el texto libre: es el dato ya
    # normalizado (y a veces fijo por normativa, p. ej. permisos a 4 años).
    por_anos = calcular_vencimiento(emitida, datos.get("vigencia_anos"))
    if por_anos is not None:
        return por_anos

    for clave in _CLAVES_VIGENCIA:
        parsed = parse_duracion(datos.get(clave))
        if parsed:
            vence = sumar_periodo(emitida, parsed["cantidad"], parsed["unidad"])
            if vence is not None:
                return vence

    return None
