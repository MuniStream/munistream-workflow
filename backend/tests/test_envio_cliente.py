"""A submission replayed from the offline outbox is processed only once."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.envio_cliente import EnvioCliente


def test_id_de_cliente_es_unico():
    campo = EnvioCliente.model_fields["client_submission_id"]
    # Indexed(str, unique=True) is what turns a replay into DuplicateKeyError.
    indices = getattr(campo.annotation, "_indexed", None)
    assert indices is not None and indices[1].get("unique") is True
