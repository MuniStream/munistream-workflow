"""Las tarjetas del selector entienden alternativas `a|b` en `display_fields`.

Una entidad llama `nombre` a lo que otra llama `nombre_completo`, y la identidad
verificada de LlaveMX a veces solo trae el nombre en `name`. Los trámites lo
declaran como `"nombre|_entity_name"`, igual que en la confirmación. Sin soporte
aquí, la tarjeta descartaba el campo en silencio —se quedaba sin el dato que
identifica a la persona— y, cuando se rotulaba, salía "Nombre| entity name".
"""
from app.services.picker_options import resumen_de_candidata


class _Entidad:
    def __init__(self, **kw):
        self.entity_id = kw.pop("entity_id", "e1")
        self.entity_type = kw.pop("entity_type", "identidad_verificada")
        self.name = kw.pop("name", None)
        self.status = kw.pop("status", "active")
        self.valid_until = kw.pop("valid_until", None)
        self.data = kw.pop("data", {})


def _campos(entidad, display_fields):
    return {c["field"]: c["value"] for c in resumen_de_candidata(entidad, display_fields)["fields"]}


def test_toma_la_primera_alternativa_con_dato():
    e = _Entidad(name="Registro", data={"nombre": "Ana Ruiz"})
    assert _campos(e, ["nombre|_entity_name"]) == {"nombre": "Ana Ruiz"}


def test_cae_a_la_siguiente_cuando_la_primera_falta():
    e = _Entidad(name="Ana Ruiz", data={})
    assert _campos(e, ["nombre|_entity_name"]) == {"nombre": "Ana Ruiz"}


def test_una_alternativa_vacia_no_cuenta_como_dato():
    e = _Entidad(name="Ana Ruiz", data={"nombre": ""})
    assert _campos(e, ["nombre|_entity_name"]) == {"nombre": "Ana Ruiz"}


def test_la_etiqueta_es_la_primera_alternativa():
    e = _Entidad(name="Ana Ruiz", data={})
    campos = resumen_de_candidata(e, ["nombre|_entity_name"])["fields"]
    assert campos[0]["field"] == "nombre"


def test_sin_ninguna_alternativa_con_dato_el_campo_se_omite():
    e = _Entidad(name=None, data={})
    assert _campos(e, ["nombre|_entity_name"]) == {}


def test_sigue_funcionando_sin_alternativas():
    e = _Entidad(data={"curp": "RUAA900101MDFXXX01"})
    assert _campos(e, ["curp"]) == {"curp": "RUAA900101MDFXXX01"}
