"""
Candidatas del selector de entidades, pedidas en vez de persistidas.

Hoy el picker mete el catálogo entero dentro del `form_config`, que el executor
mezcla al contexto y Mongo persiste — y además lo duplica en
`{task_id}_discovery_cache` y en un `pre_task_context_snapshots` por cada tarea
posterior. Medido: una lista de unas pocas embarcaciones ocupa 78,855 bytes, y
una entidad pesa ~107 KB de media. A 50 candidatas eso empuja la instancia
contra el tope de 16 MB de BSON, que es lo que la deja atorada.

Estas pruebas fijan las piezas puras del camino nuevo: encontrar la
configuración del requisito dentro del DAG, y construir el resumen de una
candidata con **solo lo que la tarjeta pinta**.

Los campos a mostrar se resuelven **en el servidor**. Hoy el backend prueba
`hasattr(entidad, campo)` y el frontend solo lee `data[campo]`, así que un
`display_fields` con `name` o con rutas con punto no pinta nada en la tarjeta.
Resolviendo aquí, la tarjeta solo tiene que renderizar lo que recibe.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.entity_validity import vigencia_de
from app.services.picker_options import encontrar_requisito, resumen_de_candidata


def _picker(task_id, requirements):
    return SimpleNamespace(task_id=task_id, requirements=requirements)


def _dag(*tareas):
    return SimpleNamespace(tasks={t.task_id: t for t in tareas})


def _entidad(**kwargs):
    base = dict(
        entity_id="embarcacion_registrada_abc",
        entity_type="embarcacion_registrada",
        name="Doña Petra",
        status="active",
        created_at=datetime(2024, 3, 1),
        data={"matricula": "MZT-1234", "puerto_base": "Mazatlán"},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Encontrar la configuración del requisito
# ---------------------------------------------------------------------------

def test_encuentra_el_requisito_por_store_as():
    dag = _dag(
        _picker("pick_required_docs", [
            {"entity_type": "representacion_legal", "store_as": "representacion_legal_ids"},
            {"entity_type": "embarcacion_registrada", "store_as": "embarcacion_ids"},
        ]),
    )

    task_id, requisito = encontrar_requisito(dag, "embarcacion_ids")

    assert task_id == "pick_required_docs"
    assert requisito["entity_type"] == "embarcacion_registrada"


def test_encuentra_el_requisito_aunque_haya_varios_pickers():
    """`credencial_rnpa` y `pesca_deportiva` tienen dos pickers en el mismo DAG."""
    dag = _dag(
        _picker("pick_pescador_rnpa", [{"entity_type": "pescador_rnpa", "store_as": "pescador_rnpa_ids"}]),
        _picker("pick_identidad", [{"entity_type": "identidad_verificada", "store_as": "identidad_ids"}]),
    )

    task_id, requisito = encontrar_requisito(dag, "identidad_ids")

    assert task_id == "pick_identidad"
    assert requisito["entity_type"] == "identidad_verificada"


def test_un_store_as_desconocido_no_encuentra_nada():
    dag = _dag(_picker("pick", [{"entity_type": "x", "store_as": "x_ids"}]))

    assert encontrar_requisito(dag, "no_existe_ids") is None


def test_las_tareas_que_no_son_pickers_se_ignoran():
    dag = _dag(
        SimpleNamespace(task_id="collect_datos"),
        _picker("pick", [{"entity_type": "x", "store_as": "x_ids"}]),
    )

    assert encontrar_requisito(dag, "x_ids")[0] == "pick"


# ---------------------------------------------------------------------------
# Resumen de una candidata
# ---------------------------------------------------------------------------

def test_el_resumen_lleva_lo_que_identifica_a_la_entidad():
    resumen = resumen_de_candidata(_entidad(), display_fields=[])

    assert resumen["entity_id"] == "embarcacion_registrada_abc"
    assert resumen["entity_type"] == "embarcacion_registrada"
    assert resumen["name"] == "Doña Petra"
    assert resumen["status"] == "active"


def test_el_resumen_no_arrastra_el_data_completo():
    """
    Es el punto entero del cambio: el catálogo deja de pesar. Si el `data`
    viajara, seguiríamos moviendo ~107 KB por candidata.
    """
    entidad = _entidad(data={"matricula": "MZT-1234", "foto_base64": "x" * 100_000})

    resumen = resumen_de_candidata(entidad, display_fields=["matricula"])

    assert "data" not in resumen
    assert "foto_base64" not in str(resumen)


def test_resuelve_los_campos_a_mostrar_desde_data():
    resumen = resumen_de_candidata(_entidad(), display_fields=["matricula", "puerto_base"])

    valores = {c["campo"]: c["valor"] for c in resumen["campos"]}
    assert valores == {"matricula": "MZT-1234", "puerto_base": "Mazatlán"}


def test_resuelve_campos_que_son_atributos_de_la_entidad():
    """
    El backend hoy prueba `hasattr` y el frontend solo `data[k]`, así que
    `display_fields: ["name"]` no pintaba nada en la tarjeta.
    """
    resumen = resumen_de_candidata(_entidad(), display_fields=["name"])

    assert resumen["campos"][0]["valor"] == "Doña Petra"


def test_resuelve_rutas_con_punto():
    """Catastro usa `clave_catastral_data.clave_catastral`; hoy no renderiza."""
    entidad = _entidad(data={"clave_catastral_data": {"clave_catastral": "001-234"}})

    resumen = resumen_de_candidata(entidad, display_fields=["clave_catastral_data.clave_catastral"])

    assert resumen["campos"][0]["valor"] == "001-234"


def test_los_campos_que_no_existen_se_omiten():
    """
    En guía de pesca, `fecha_arribo` y `fecha` son alternativas según el tipo de
    aviso: el que no aplique no debe dejar un renglón vacío en la tarjeta.
    """
    resumen = resumen_de_candidata(_entidad(), display_fields=["matricula", "fecha_arribo"])

    assert [c["campo"] for c in resumen["campos"]] == ["matricula"]


def test_el_orden_de_los_campos_respeta_el_configurado():
    resumen = resumen_de_candidata(_entidad(), display_fields=["puerto_base", "matricula"])

    assert [c["campo"] for c in resumen["campos"]] == ["puerto_base", "matricula"]


# ---------------------------------------------------------------------------
# Vigencia
# ---------------------------------------------------------------------------

def test_la_vigencia_se_deriva_de_los_anos_declarados():
    entidad = _entidad(created_at=datetime(2024, 3, 1), data={"vigencia_anos": 2})

    vig = vigencia_de(entidad)

    assert vig["hasta"].year == 2026
    assert vig["hasta"].month == 3


def test_sin_anos_de_vigencia_no_se_inventa_una_fecha():
    """
    Las entidades no guardan fecha de vencimiento; solo algunas traen
    `vigencia_anos`. Donde no hay dato, no se muestra nada — no se adivina.
    """
    assert vigencia_de(_entidad(data={"matricula": "MZT-1234"})) is None


def test_una_vigencia_ya_cumplida_se_marca_vencida():
    entidad = _entidad(created_at=datetime(2019, 1, 1), data={"vigencia_anos": 1})

    assert vigencia_de(entidad)["vencida"] is True


def test_una_vigencia_en_curso_no_se_marca_vencida():
    entidad = _entidad(created_at=datetime.utcnow().replace(year=datetime.utcnow().year - 1),
                       data={"vigencia_anos": 10})

    assert vigencia_de(entidad)["vencida"] is False


# ---------------------------------------------------------------------------
# Fecha de vencimiento como campo propio
# ---------------------------------------------------------------------------

def test_la_fecha_guardada_gana_sobre_la_derivada():
    """
    Con `valid_until` en la entidad no hay que derivar nada: es el dato, no una
    inferencia. Si además trae `vigencia_anos`, manda el guardado — una prórroga
    o una revocación cambian la fecha sin cambiar los años declarados.
    """
    entidad = _entidad(
        created_at=datetime(2024, 3, 1),
        valid_until=datetime(2030, 1, 15),
        data={"vigencia_anos": 2},
    )

    vig = vigencia_de(entidad)

    assert vig["hasta"] == datetime(2030, 1, 15)
    assert vig["origen"] == "guardada"


def test_sin_fecha_guardada_se_deriva_y_se_dice():
    """
    Las entidades anteriores a este campo no la tienen. Se deriva, pero el
    resumen declara que es derivada para que la interfaz pueda matizarlo.
    """
    entidad = _entidad(created_at=datetime(2024, 3, 1), data={"vigencia_anos": 2})

    assert vigencia_de(entidad)["origen"] == "derivada"


def test_una_fecha_guardada_ya_pasada_se_marca_vencida():
    entidad = _entidad(valid_until=datetime(2020, 1, 1), data={})

    assert vigencia_de(entidad)["vencida"] is True


def test_calcular_vencimiento_desde_los_anos_declarados():
    from app.services.entity_validity import calcular_vencimiento

    assert calcular_vencimiento(datetime(2024, 3, 1), 5) == datetime(2029, 3, 1)


def test_calcular_vencimiento_sin_anos_no_inventa_nada():
    from app.services.entity_validity import calcular_vencimiento

    assert calcular_vencimiento(datetime(2024, 3, 1), None) is None
    assert calcular_vencimiento(datetime(2024, 3, 1), 0) is None


def test_calcular_vencimiento_tolera_el_29_de_febrero():
    from app.services.entity_validity import calcular_vencimiento

    assert calcular_vencimiento(datetime(2024, 2, 29), 1) == datetime(2025, 2, 28)


# ---------------------------------------------------------------------------
# El vencimiento se fija al emitir la entidad
# ---------------------------------------------------------------------------

def test_el_vencimiento_inicial_sale_de_los_anos_declarados():
    """
    Los workflows ya declaran `vigencia_anos` en el `data` de la entidad que
    emiten. Con eso basta para fijar la fecha al crearla, sin tocar ninguno de
    los 25 workflows.
    """
    from app.services.entity_validity import vencimiento_inicial

    hasta = vencimiento_inicial({"vigencia_anos": 5}, emitida=datetime(2026, 1, 10))

    assert hasta == datetime(2031, 1, 10)


def test_un_vencimiento_explicito_gana_sobre_los_anos():
    """Una resolución puede fijar una fecha que no sale de una cuenta de años."""
    from app.services.entity_validity import vencimiento_inicial

    hasta = vencimiento_inicial(
        {"vigencia_anos": 5, "valid_until": datetime(2027, 6, 30)},
        emitida=datetime(2026, 1, 10),
    )

    assert hasta == datetime(2027, 6, 30)


def test_un_vencimiento_explicito_en_texto_iso_se_entiende():
    from app.services.entity_validity import vencimiento_inicial

    hasta = vencimiento_inicial({"valid_until": "2027-06-30"}, emitida=datetime(2026, 1, 10))

    assert hasta == datetime(2027, 6, 30)


def test_sin_vigencia_declarada_la_entidad_no_vence():
    """Una credencial sin vigencia declarada no debe nacer con una inventada."""
    from app.services.entity_validity import vencimiento_inicial

    assert vencimiento_inicial({"matricula": "MZT-1234"}, emitida=datetime(2026, 1, 10)) is None


def test_una_fecha_explicita_ilegible_no_tumba_la_emision():
    """
    Emitir la entidad es lo importante; una vigencia mal escrita en el workflow
    no puede impedir que el trámite termine.
    """
    from app.services.entity_validity import vencimiento_inicial

    assert vencimiento_inicial({"valid_until": "el año que viene"}, emitida=datetime(2026, 1, 10)) is None
