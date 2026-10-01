"""Validación del campo `address` en modo mundial (domicilio en el extranjero).

El campo `address` nació anclado a México: exigía colonia y código postal, y el
portal resolvía el CP contra el catálogo de SEPOMEX. Para un domicilio en el
extranjero eso es imposible de satisfacer —hay países sin código postal, otros
con códigos alfanuméricos, y la colonia no tiene equivalente—, así que el campo
gana un modo mundial que pide el país y libera ciudad, estado/provincia y CP.

Lo que estas pruebas fijan es que el BACKEND espejee al portal. Si el portal
muestra el formulario mundial y el backend sigue exigiendo colonia y CP, el
solicitante llena lo que se le pide y el envío se rechaza sin salida posible.
"""
from app.workflows.operators.user_input import UserInputOperator

MX = {"calle": "Reforma", "no_ext": "10", "cp": "80000", "colonia": "Centro",
      "municipio": "Culiacán", "estado": "Sinaloa"}
# Japón no usa colonias; su código postal no encaja en el formato mexicano.
JP = {"pais": "Japón", "calle": "Chuo", "no_ext": "3", "municipio": "Tokio"}


def _operador(campo_domicilio, extra_fields=()):
    campos = [campo_domicilio, *extra_fields]
    return UserInputOperator(
        task_id="captura",
        name="Captura",
        form_config={"fields": campos},
        required_fields=[],
    )


def _errores(op, entrada):
    return [e for e in op._validate_input(entrada) if e.startswith("Domicilio")]


def test_modo_nacional_exige_colonia_y_cp():
    op = _operador({"name": "domicilio", "label": "Domicilio",
                    "type": "address", "required": True})
    assert _errores(op, {"domicilio": MX}) == []
    faltantes = _errores(op, {"domicilio": {**MX, "colonia": "", "cp": ""}})
    assert len(faltantes) == 2, faltantes


def test_modo_mundial_fijo_no_exige_colonia_ni_cp():
    op = _operador({"name": "domicilio", "label": "Domicilio", "type": "address",
                    "required": True, "international": True})
    assert _errores(op, {"domicilio": JP}) == []


def test_modo_mundial_fijo_exige_el_pais():
    op = _operador({"name": "domicilio", "label": "Domicilio", "type": "address",
                    "required": True, "international": True})
    sin_pais = {k: v for k, v in JP.items() if k != "pais"}
    errores = _errores(op, {"domicilio": sin_pais})
    assert len(errores) == 1 and "País" in errores[0], errores


def _op_condicional(cond):
    return _operador(
        {"name": "domicilio", "label": "Domicilio", "type": "address",
         "required": True, "international_if": cond},
        extra_fields=[{"name": "nacionalidad", "label": "Nacionalidad",
                       "type": "text", "required": False}],
    )


def test_condicional_por_negacion():
    op = _op_condicional({"field": "nacionalidad", "not_value": "México"})
    # Mexicano: sigue el modo nacional, y al domicilio japonés le faltan
    # colonia, código postal y estado.
    assert len(_errores(op, {"nacionalidad": "México", "domicilio": JP})) == 3
    # Extranjero: el mismo domicilio se acepta.
    assert _errores(op, {"nacionalidad": "Japón", "domicilio": JP}) == []


def test_condicional_por_valor_explicito():
    op = _op_condicional({"field": "nacionalidad", "value": ["Japón", "Estados Unidos"]})
    assert _errores(op, {"nacionalidad": "Japón", "domicilio": JP}) == []
    assert len(_errores(op, {"nacionalidad": "España", "domicilio": JP})) == 3


def test_sin_nacionalidad_no_se_asume_extranjero():
    """Con el campo de referencia vacío se conserva el modo nacional.

    Al revés, el ciudadano vería el formulario mundial —con selector de país—
    antes de haber declarado su nacionalidad.
    """
    op = _op_condicional({"field": "nacionalidad", "not_value": "México"})
    assert _errores(op, {"nacionalidad": "", "domicilio": MX}) == []
    assert len(_errores(op, {"nacionalidad": "", "domicilio": JP})) == 3


def test_region_only_mundial_pide_pais_y_ciudad():
    op = _operador({"name": "domicilio", "label": "Domicilio", "type": "address",
                    "required": True, "region_only": True, "international": True})
    assert _errores(op, {"domicilio": {"pais": "Japón", "municipio": "Tokio"}}) == []
    errores = _errores(op, {"domicilio": {"municipio": "Tokio"}})
    assert len(errores) == 1 and "País" in errores[0], errores
