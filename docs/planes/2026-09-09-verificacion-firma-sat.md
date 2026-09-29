# Verificación real de la firma con e.firma del SAT — Plan de implementación

> **Para quien ejecute esto (humano o agente):** usa `superpowers:subagent-driven-development`
> o `superpowers:executing-plans` para llevarlo tarea por tarea. Los pasos usan
> casillas (`- [ ]`) para seguimiento.

**Objetivo:** que una firma digital en MuniStream signifique algo — que el backend
verifique criptográficamente que el documento fue firmado por la e.firma del
funcionario esperado, y conserve la evidencia para probarlo después.

**Arquitectura:** la llave privada nunca sale del dispositivo de quien firma; el
navegador firma y envía solo firma + certificado. El servidor define una
serialización canónica del documento, persiste su digest al *solicitar* la firma,
y al recibirla verifica la firma contra ese digest, valida el certificado contra
las raíces del SAT y ata la identidad del certificado al funcionario autenticado.

**Stack:** Python 3.11, FastAPI, Beanie/MongoDB, `cryptography` (ya en
`requirements.txt`), pytest.

**Spec:** la propuesta de diseño discutida en la conversación del 2026-09-09,
resumida en la sección "Contexto" de este documento.

## Restricciones globales

- La e.firma del SAT es siempre **`.cer` + `.key` + contraseña**. No hay tokens ni
  HSM: el insumo es un archivo. El diseño debe asumirlo.
- **Nada de llave privada ni contraseña puede llegar al backend.** Ni en un campo
  de formulario, ni en logs, ni en telemetría. Cualquier tarea que lo introduzca
  está mal y debe rechazarse en revisión.
- El `.key` del SAT es **PKCS#8 cifrado en DER** con esquemas PBE viejos
  (`pbeWithSHA1And3-KeyTripleDES-CBC`). WebCrypto no puede descifrarlo: el
  descifrado ocurre en JavaScript y solo entonces se importa a WebCrypto. Este
  plan **no** toca esa parte (vive en el portal), pero condiciona el formato de la
  firma que llega.
- La firma que produce el navegador es **RSASSA-PKCS#1 v1.5 con SHA-256**, no PSS.
- Ningún endpoint puede aceptar un `signature_valid` que venga del cliente.
- El idioma del código y los comentarios sigue el del repositorio.

## Contexto: qué hay hoy

- `backend/app/workflows/operators/signer_operator.py` (~línea 225) marca
  `signature_valid: True` con el comentario
  `# For now, assume valid since we're doing client-side validation`. **No se
  verifica nada.**
- `backend/app/services/signature/signature_verifier.py` **sí** tiene un
  `verify_signature()` funcional… pero su tabla de algoritmos mapea
  `"RSA-SHA256"` a `padding.PSS`. Como el navegador firma con PKCS#1 v1.5,
  cablearlo tal cual **rechazaría toda firma legítima**. Hay que arreglarlo antes
  de conectarlo.
- `verify_certificate_chain()` solo comprueba fechas de vigencia y tiene un
  `TODO: Implement full chain verification`. No hay raíces del SAT ni CRL.
- `_prepare_signable_data()` ya produce un payload estable con `data_hash`
  (arreglado en el PR #43), pero el hash **no se compara nunca** al recibir la firma.

## Alcance

Este plan cubre **la verificación del lado del servidor**, que es donde está todo
el valor: hoy la firma no se verifica, así que da igual dónde se haya firmado.

**Fuera de alcance, como plan aparte:** el origen aislado para la página de firma
(subdominio propio, CSP estricta, puente `postMessage` que solo deja pasar digest
hacia adentro y firma hacia afuera). Es contención de un riesgo de XSS que ya
existe, y no depende de este trabajo.

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `backend/app/services/signature/canonical.py` | **Nuevo.** Serialización canónica y digest. Una sola responsabilidad, sin dependencias del dominio. |
| `backend/app/services/signature/signature_verifier.py` | Modificar: corregir el padding y añadir verificación contra un digest persistido. |
| `backend/app/services/signature/sat_trust.py` | **Nuevo.** Raíces del SAT, validación de cadena, vigencia al momento de firmar y revocación por CRL. |
| `backend/app/services/signature/identity.py` | **Nuevo.** Extracción de RFC/CURP del sujeto del certificado y comparación con el firmante esperado. |
| `backend/app/models/signature_evidence.py` | **Nuevo.** Documento Beanie con la evidencia de cada firma. |
| `backend/app/workflows/operators/signer_operator.py` | Modificar: sustituir el "assume valid" por la verificación real. |
| `backend/tests/test_signature_canonical.py` | Nuevo. |
| `backend/tests/test_signature_verification.py` | Nuevo. |
| `backend/tests/test_sat_trust.py` | Nuevo. |
| `backend/tests/test_signature_identity.py` | Nuevo. |

Las tareas 1 a 4 son puras y no requieren Mongo ni Keycloak. La 5 y la 6 sí tocan
la base.

---

### Tarea 1: Serialización canónica y digest

Sin bytes deterministas no hay nada que firmar: si el servidor no puede
reproducir exactamente la secuencia que el navegador firmó, la verificación
siempre fallará.

**Archivos:**
- Crear: `backend/app/services/signature/canonical.py`
- Test: `backend/tests/test_signature_canonical.py`

**Interfaces:**
- Produce: `canonical_bytes(payload: dict) -> bytes` y
  `canonical_digest(payload: dict) -> str` (hex SHA-256). Las tareas 2 y 6 las usan.

- [ ] **Paso 1: escribir la prueba que falla**

```python
# backend/tests/test_signature_canonical.py
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature.canonical import canonical_bytes, canonical_digest


def test_el_orden_de_las_claves_no_altera_los_bytes():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}

    assert canonical_bytes(a) == canonical_bytes(b)


def test_los_bytes_no_llevan_espacios_superfluos():
    assert canonical_bytes({"a": 1, "b": 2}) == b'{"a":1,"b":2}'


def test_se_serializa_en_utf8_sin_escapes_ascii():
    """El acento debe viajar como UTF-8, no como \\u00f3, o el hash del
    navegador y el del backend no coincidirán."""
    assert canonical_bytes({"n": "validación"}) == '{"n":"validación"}'.encode("utf-8")


def test_el_digest_es_sha256_hexadecimal():
    digest = canonical_digest({"a": 1})

    assert len(digest) == 64
    assert digest == canonical_digest({"a": 1})


def test_un_cambio_real_mueve_el_digest():
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})
```

- [ ] **Paso 2: correr la prueba y verla fallar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_canonical.py -q -p no:warnings"`
Se espera: FAIL con `ModuleNotFoundError: No module named 'app.services.signature.canonical'`

- [ ] **Paso 3: implementación mínima**

```python
# backend/app/services/signature/canonical.py
"""
Serialización canónica del documento a firmar.

El navegador y el backend tienen que producir exactamente la misma secuencia de
bytes o la verificación falla siempre. Fijamos aquí las tres decisiones que lo
determinan: claves ordenadas, sin espacios y UTF-8 sin escapes ASCII.
"""

import hashlib
import json
from typing import Any, Dict

# Versión del formato canónico. Va dentro del payload firmado, de modo que un
# cambio futuro en estas reglas no invalide en silencio las firmas anteriores.
CANONICAL_VERSION = "1"


def canonical_bytes(payload: Dict[str, Any]) -> bytes:
    """Bytes deterministas del payload."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def canonical_digest(payload: Dict[str, Any]) -> str:
    """SHA-256 hexadecimal de la forma canónica."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()
```

- [ ] **Paso 4: correr la prueba y verla pasar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_canonical.py -q -p no:warnings"`
Se espera: PASS (5 pruebas)

- [ ] **Paso 5: commit**

```bash
git add backend/app/services/signature/canonical.py backend/tests/test_signature_canonical.py
git commit -m "feat(firma): serialización canónica y digest del documento a firmar"
```

---

### Tarea 2: Corregir el padding y verificar contra un digest

`verify_signature()` mapea `"RSA-SHA256"` a `padding.PSS`. El navegador firma con
PKCS#1 v1.5, así que hoy rechazaría toda firma legítima. Esta tarea lo corrige y
añade el punto de entrada que usará el operador.

**Archivos:**
- Modificar: `backend/app/services/signature/signature_verifier.py`
- Test: `backend/tests/test_signature_verification.py`

**Interfaces:**
- Consume: `canonical_bytes` de la tarea 1.
- Produce: `SignatureVerifier.verify_against_digest(digest_hex, signature_base64, certificate_pem, algorithm) -> bool`. La tarea 6 la usa.

- [ ] **Paso 1: escribir la prueba que falla**

Genera un par de llaves y un certificado autofirmado en la propia prueba, para no
depender de material real del SAT.

```python
# backend/tests/test_signature_verification.py
import base64
import hashlib
import os
import sys
from datetime import datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature.signature_verifier import SignatureVerifier


def _par_de_llaves():
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRUEBA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    return llave, pem


def _firmar_pkcs1(llave, datos: bytes) -> str:
    """Como firma el navegador: RSASSA-PKCS#1 v1.5, no PSS."""
    firma = llave.sign(datos, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(firma).decode()


@pytest.mark.asyncio
async def test_acepta_una_firma_pkcs1_del_navegador():
    llave, pem = _par_de_llaves()
    datos = b'{"a":1}'

    ok = await SignatureVerifier().verify_signature(
        data=datos,
        signature_base64=_firmar_pkcs1(llave, datos),
        certificate_pem=pem,
        algorithm="RSA-SHA256",
    )

    assert ok is True


@pytest.mark.asyncio
async def test_rechaza_una_firma_de_otra_llave():
    llave_a, _ = _par_de_llaves()
    _, pem_b = _par_de_llaves()
    datos = b'{"a":1}'

    ok = await SignatureVerifier().verify_signature(
        data=datos,
        signature_base64=_firmar_pkcs1(llave_a, datos),
        certificate_pem=pem_b,
        algorithm="RSA-SHA256",
    )

    assert ok is False


@pytest.mark.asyncio
async def test_rechaza_si_los_datos_cambiaron():
    llave, pem = _par_de_llaves()

    ok = await SignatureVerifier().verify_signature(
        data=b'{"a":2}',
        signature_base64=_firmar_pkcs1(llave, b'{"a":1}'),
        certificate_pem=pem,
        algorithm="RSA-SHA256",
    )

    assert ok is False


@pytest.mark.asyncio
async def test_verifica_contra_el_digest_persistido():
    """El operador guarda un digest al solicitar la firma; la verificación se
    hace contra ese, no contra un payload recalculado después."""
    llave, pem = _par_de_llaves()
    datos = b'{"a":1}'
    digest = hashlib.sha256(datos).hexdigest()

    ok = await SignatureVerifier().verify_against_digest(
        digest_hex=digest,
        signature_base64=_firmar_pkcs1(llave, datos),
        certificate_pem=pem,
        algorithm="RSA-SHA256",
    )

    assert ok is True


@pytest.mark.asyncio
async def test_rechaza_un_digest_que_no_corresponde():
    llave, pem = _par_de_llaves()

    ok = await SignatureVerifier().verify_against_digest(
        digest_hex=hashlib.sha256(b'{"a":2}').hexdigest(),
        signature_base64=_firmar_pkcs1(llave, b'{"a":1}'),
        certificate_pem=pem,
        algorithm="RSA-SHA256",
    )

    assert ok is False
```

- [ ] **Paso 2: correr la prueba y verla fallar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_verification.py -q -p no:warnings"`
Se espera: los tres primeros FAIL porque PSS no valida una firma PKCS#1 v1.5, y
los dos últimos FAIL con `AttributeError: 'SignatureVerifier' object has no
attribute 'verify_against_digest'`.

- [ ] **Paso 3: implementación mínima**

En `signature_verifier.py`, cambia la tabla de algoritmos:

```python
        self.supported_algorithms = {
            # El navegador (WebCrypto, RSASSA-PKCS1-v1_5) y las herramientas del
            # SAT producen PKCS#1 v1.5. Verificar con PSS rechazaría toda firma
            # legítima.
            "RSA-SHA256": (padding.PKCS1v15, hashes.SHA256),
            "RSA-SHA512": (padding.PKCS1v15, hashes.SHA512),
            "ECDSA-SHA256": (None, hashes.SHA256),
            "ECDSA-SHA384": (None, hashes.SHA384)
        }
```

y sustituye la construcción del objeto de padding dentro de `verify_signature`:

```python
                padding_obj = padding_type()
```

Añade el método nuevo:

```python
    async def verify_against_digest(
        self,
        digest_hex: str,
        signature_base64: str,
        certificate_pem: str,
        algorithm: str = "RSA-SHA256"
    ) -> bool:
        """
        Verifica la firma contra un digest que ya estaba guardado.

        Se verifica contra el digest persistido al *solicitar* la firma, nunca
        contra un payload recalculado en el momento de recibirla: si el contexto
        cambió entre una cosa y la otra, la firma dejaría de corresponder a lo
        que el funcionario vio y aceptó.
        """
        from .canonical import canonical_bytes  # noqa: F401  (documenta el origen)

        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem.encode())
            public_key = certificate.public_key()
            if algorithm not in self.supported_algorithms:
                logger.error(f"Unsupported signature algorithm: {algorithm}")
                return False
            padding_type, hash_algorithm = self.supported_algorithms[algorithm]
            if not isinstance(public_key, rsa.RSAPublicKey):
                logger.error("Certificate does not contain RSA public key")
                return False

            public_key.verify(
                base64.b64decode(signature_base64),
                bytes.fromhex(digest_hex),
                padding_type(),
                Prehashed(hash_algorithm()),
            )
            return True
        except InvalidSignature:
            logger.warning("Signature verification failed - invalid signature")
            return False
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False
```

Añade el import que necesita `Prehashed` al principio del archivo:

```python
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
```

- [ ] **Paso 4: correr las pruebas y verlas pasar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_verification.py -q -p no:warnings"`
Se espera: PASS (5 pruebas)

- [ ] **Paso 5: commit**

```bash
git add backend/app/services/signature/signature_verifier.py backend/tests/test_signature_verification.py
git commit -m "fix(firma): verificar PKCS#1 v1.5 y contra el digest persistido"
```

---

### Tarea 3: Identidad — atar el certificado al funcionario

Sin esto, **cualquier e.firma válida del país** sirve para firmar cualquier
trámite. Es la comprobación de mayor valor por línea de código de todo el plan.

**Archivos:**
- Crear: `backend/app/services/signature/identity.py`
- Test: `backend/tests/test_signature_identity.py`

**Interfaces:**
- Produce: `extract_rfc(certificate_pem: str) -> Optional[str]` y
  `certificate_belongs_to(certificate_pem: str, expected_rfc: str) -> bool`.
  La tarea 6 las usa.

- [ ] **Paso 1: escribir la prueba que falla**

El RFC en la e.firma viaja en el OID `2.5.4.45` (`x500UniqueIdentifier`) del
sujeto, normalmente como `RFC / CURP`. El `serialNumber` también lo lleva en
algunos certificados.

```python
# backend/tests/test_signature_identity.py
import os
import sys
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ObjectIdentifier

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature.identity import certificate_belongs_to, extract_rfc

OID_X500_UNIQUE_IDENTIFIER = ObjectIdentifier("2.5.4.45")


def _certificado(unique_identifier: str) -> str:
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "DOLORES GARCIA CASILLAS"),
        x509.NameAttribute(OID_X500_UNIQUE_IDENTIFIER, unique_identifier),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_extrae_el_rfc_del_sujeto():
    pem = _certificado("GACD800101ABC / GACD800101MDFRSL09")

    assert extract_rfc(pem) == "GACD800101ABC"


def test_extrae_el_rfc_cuando_viene_solo():
    assert extract_rfc(_certificado("GACD800101ABC")) == "GACD800101ABC"


def test_el_rfc_se_compara_sin_importar_may_o_espacios():
    pem = _certificado("  gacd800101abc / GACD800101MDFRSL09 ")

    assert certificate_belongs_to(pem, "GACD800101ABC") is True


def test_rechaza_el_certificado_de_otra_persona():
    pem = _certificado("XAXX010101000 / XAXX010101HDFRSL01")

    assert certificate_belongs_to(pem, "GACD800101ABC") is False


def test_falla_cerrado_si_el_certificado_no_trae_rfc():
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SIN RFC")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=1))
        .not_valid_after(datetime.utcnow() + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    assert extract_rfc(pem) is None
    assert certificate_belongs_to(pem, "GACD800101ABC") is False


def test_falla_cerrado_si_no_se_espera_ningun_rfc():
    """Un firmante sin RFC registrado no puede pasar la comprobación."""
    pem = _certificado("GACD800101ABC")

    assert certificate_belongs_to(pem, "") is False
    assert certificate_belongs_to(pem, None) is False
```

- [ ] **Paso 2: correr la prueba y verla fallar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_identity.py -q -p no:warnings"`
Se espera: FAIL con `ModuleNotFoundError: No module named 'app.services.signature.identity'`

- [ ] **Paso 3: implementación mínima**

```python
# backend/app/services/signature/identity.py
"""
Identidad del certificado: a quién pertenece la e.firma que firmó.

Sin esta comprobación, cualquier e.firma válida del país sirve para firmar
cualquier trámite: la firma sería criptográficamente correcta y aun así no
probaría que firmó quien debía.
"""

import logging
from typing import Optional

from cryptography import x509
from cryptography.x509.oid import ObjectIdentifier

logger = logging.getLogger(__name__)

# En la e.firma el RFC viaja en x500UniqueIdentifier, normalmente como
# "RFC / CURP". Algunos certificados lo repiten en serialNumber.
OID_X500_UNIQUE_IDENTIFIER = ObjectIdentifier("2.5.4.45")
OID_SERIAL_NUMBER = ObjectIdentifier("2.5.4.5")


def extract_rfc(certificate_pem: str) -> Optional[str]:
    """RFC del sujeto, o None si el certificado no lo trae."""
    try:
        cert = x509.load_pem_x509_certificate(certificate_pem.encode())
    except Exception as e:
        logger.warning(f"No se pudo leer el certificado: {e}")
        return None

    for oid in (OID_X500_UNIQUE_IDENTIFIER, OID_SERIAL_NUMBER):
        for atributo in cert.subject.get_attributes_for_oid(oid):
            valor = str(atributo.value or "")
            rfc = valor.split("/")[0].strip().upper()
            if rfc:
                return rfc
    return None


def certificate_belongs_to(certificate_pem: str, expected_rfc: Optional[str]) -> bool:
    """
    True solo si el RFC del certificado coincide con el esperado.

    Falla cerrado: sin RFC en el certificado, o sin un RFC esperado con el que
    comparar, la respuesta es False.
    """
    if not expected_rfc:
        return False
    rfc = extract_rfc(certificate_pem)
    if not rfc:
        return False
    return rfc == expected_rfc.strip().upper()
```

- [ ] **Paso 4: correr las pruebas y verlas pasar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signature_identity.py -q -p no:warnings"`
Se espera: PASS (6 pruebas)

- [ ] **Paso 5: commit**

```bash
git add backend/app/services/signature/identity.py backend/tests/test_signature_identity.py
git commit -m "feat(firma): atar el certificado al RFC del firmante esperado"
```

---

### Tarea 4: Confianza en el certificado — cadena del SAT, vigencia y revocación

**Archivos:**
- Crear: `backend/app/services/signature/sat_trust.py`
- Crear: `backend/app/services/signature/sat_roots/README.md`
- Test: `backend/tests/test_sat_trust.py`

**Interfaces:**
- Produce: `SatTrustStore.validate(certificate_pem: str, signed_at: datetime) -> TrustResult`,
  donde `TrustResult` es un dataclass con `valid: bool` y `reason: Optional[str]`.
  La tarea 6 la usa.

**Nota para quien lo implemente:** los certificados raíz e intermedios del SAT se
descargan del portal del SAT y se versionan en `sat_roots/`. **No** los busques en
tiempo de ejecución: una dependencia de red en el camino de validación convierte
una caída del SAT en una caída de tu sistema. El README de esa carpeta debe
documentar de dónde salió cada archivo y cuándo.

- [ ] **Paso 1: escribir la prueba que falla**

```python
# backend/tests/test_sat_trust.py
import os
import sys
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature.sat_trust import SatTrustStore


def _ca():
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC DE PRUEBA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=3650))
        .not_valid_after(datetime.utcnow() + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(llave, hashes.SHA256())
    )
    return llave, cert


def _emitir(ca_llave, ca_cert, desde, hasta):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FIRMANTE")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(ca_cert.subject)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(desde).not_valid_after(hasta)
        .sign(ca_llave, hashes.SHA256())
    )
    return cert, cert.public_bytes(serialization.Encoding.PEM).decode()


def test_acepta_un_certificado_emitido_por_una_raiz_de_confianza():
    ca_llave, ca_cert = _ca()
    _, pem = _emitir(ca_llave, ca_cert,
                     datetime.utcnow() - timedelta(days=10),
                     datetime.utcnow() + timedelta(days=10))
    store = SatTrustStore(roots=[ca_cert], revoked_serials=set())

    assert store.validate(pem, signed_at=datetime.utcnow()).valid is True


def test_rechaza_un_certificado_de_una_raiz_desconocida():
    ca_llave, ca_cert = _ca()
    _, ca_ajena = _ca()
    _, pem = _emitir(ca_llave, ca_cert,
                     datetime.utcnow() - timedelta(days=10),
                     datetime.utcnow() + timedelta(days=10))
    store = SatTrustStore(roots=[ca_ajena], revoked_serials=set())

    resultado = store.validate(pem, signed_at=datetime.utcnow())
    assert resultado.valid is False
    assert "raíz" in resultado.reason


def test_la_vigencia_se_evalua_al_momento_de_firmar():
    """
    Una firma hecha cuando el certificado estaba vigente sigue siendo válida
    aunque el certificado ya haya expirado. Todos expiran a los cuatro años.
    """
    ca_llave, ca_cert = _ca()
    _, pem = _emitir(ca_llave, ca_cert,
                     datetime.utcnow() - timedelta(days=100),
                     datetime.utcnow() - timedelta(days=10))
    store = SatTrustStore(roots=[ca_cert], revoked_serials=set())

    firmado_a_tiempo = datetime.utcnow() - timedelta(days=50)
    assert store.validate(pem, signed_at=firmado_a_tiempo).valid is True


def test_rechaza_una_firma_hecha_fuera_de_la_vigencia():
    ca_llave, ca_cert = _ca()
    _, pem = _emitir(ca_llave, ca_cert,
                     datetime.utcnow() - timedelta(days=100),
                     datetime.utcnow() - timedelta(days=10))
    store = SatTrustStore(roots=[ca_cert], revoked_serials=set())

    resultado = store.validate(pem, signed_at=datetime.utcnow())
    assert resultado.valid is False
    assert "vigencia" in resultado.reason


def test_rechaza_un_certificado_revocado():
    ca_llave, ca_cert = _ca()
    cert, pem = _emitir(ca_llave, ca_cert,
                        datetime.utcnow() - timedelta(days=10),
                        datetime.utcnow() + timedelta(days=10))
    store = SatTrustStore(roots=[ca_cert], revoked_serials={cert.serial_number})

    resultado = store.validate(pem, signed_at=datetime.utcnow())
    assert resultado.valid is False
    assert "revocado" in resultado.reason


def test_rechaza_un_certificado_sin_uso_de_llave_para_firmar():
    """
    Un sello digital (CSD) sirve para facturación, no para firmar actos de
    autoridad. La diferencia está en la extensión KeyUsage.
    """
    ca_llave, ca_cert = _ca()
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SELLO")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(ca_cert.subject)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=10))
        .not_valid_after(datetime.utcnow() + timedelta(days=10))
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_llave, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    store = SatTrustStore(roots=[ca_cert], revoked_serials=set())

    resultado = store.validate(pem, signed_at=datetime.utcnow())
    assert resultado.valid is False
    assert "uso de llave" in resultado.reason


def test_acepta_un_certificado_con_uso_de_llave_para_firmar():
    ca_llave, ca_cert = _ca()
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EFIRMA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre).issuer_name(ca_cert.subject)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow() - timedelta(days=10))
        .not_valid_after(datetime.utcnow() + timedelta(days=10))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=True,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_llave, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    store = SatTrustStore(roots=[ca_cert], revoked_serials=set())

    assert store.validate(pem, signed_at=datetime.utcnow()).valid is True


def test_falla_cerrado_sin_raices_cargadas():
    """Un almacén vacío no puede aprobar nada: sería confiar en cualquiera."""
    ca_llave, ca_cert = _ca()
    _, pem = _emitir(ca_llave, ca_cert,
                     datetime.utcnow() - timedelta(days=10),
                     datetime.utcnow() + timedelta(days=10))
    store = SatTrustStore(roots=[], revoked_serials=set())

    assert store.validate(pem, signed_at=datetime.utcnow()).valid is False
```

- [ ] **Paso 2: correr la prueba y verla fallar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_sat_trust.py -q -p no:warnings"`
Se espera: FAIL con `ModuleNotFoundError: No module named 'app.services.signature.sat_trust'`

- [ ] **Paso 3: implementación mínima**

```python
# backend/app/services/signature/sat_trust.py
"""
Confianza en el certificado del firmante.

Comprueba tres cosas independientes: que lo emitió una autoridad en la que
confiamos, que estaba vigente **en el momento de firmar** (no ahora), y que no
estaba revocado.

Falla cerrado en todos los casos: sin raíces cargadas no se aprueba nada.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Set

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)


@dataclass
class TrustResult:
    valid: bool
    reason: Optional[str] = None


class SatTrustStore:
    """Almacén de raíces del SAT y lista de revocación."""

    def __init__(self, roots: List[x509.Certificate], revoked_serials: Set[int]):
        self.roots = roots
        self.revoked_serials = revoked_serials

    def validate(self, certificate_pem: str, signed_at: datetime) -> TrustResult:
        try:
            cert = x509.load_pem_x509_certificate(certificate_pem.encode())
        except Exception as e:
            return TrustResult(False, f"certificado ilegible: {e}")

        if cert.serial_number in self.revoked_serials:
            return TrustResult(False, "el certificado está revocado")

        if not (cert.not_valid_before <= signed_at <= cert.not_valid_after):
            return TrustResult(
                False,
                "la firma cae fuera de la vigencia del certificado",
            )

        if not self._issued_by_trusted_root(cert):
            return TrustResult(False, "el certificado no proviene de una raíz de confianza")

        if not self._allows_signing(cert):
            return TrustResult(
                False,
                "el certificado no declara uso de llave para firmar "
                "(un sello digital sirve para facturar, no para firmar actos de autoridad)",
            )

        return TrustResult(True)

    @staticmethod
    def _allows_signing(cert: x509.Certificate) -> bool:
        """
        KeyUsage debe permitir firmar. Es lo que separa una e.firma de un CSD.

        Un certificado sin la extensión no se aprueba: falla cerrado.
        """
        try:
            usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            return False
        return bool(usage.digital_signature or usage.content_commitment)

    def _issued_by_trusted_root(self, cert: x509.Certificate) -> bool:
        for root in self.roots:
            if cert.issuer != root.subject:
                continue
            try:
                root.public_key().verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    cert.signature_hash_algorithm,
                )
                return True
            except InvalidSignature:
                continue
            except Exception as e:
                logger.warning(f"Error al validar contra una raíz: {e}")
                continue
        return False


def load_default_trust_store() -> SatTrustStore:
    """
    Almacén construido desde `sat_roots/` y la CRL en disco.

    Se lee de disco a propósito: una consulta en red dentro del camino de
    validación convertiría una caída del SAT en una caída de este sistema.

    Si la carpeta está vacía devuelve un almacén sin raíces, que rechaza todo.
    Es deliberado: es preferible rechazar firmas a aceptarlas sin comprobar de
    quién vienen, y el fallo es ruidoso en lugar de silencioso.
    """
    import pathlib

    directorio = pathlib.Path(__file__).parent / "sat_roots"
    raices = []
    for archivo in sorted(directorio.glob("*.cer")) + sorted(directorio.glob("*.pem")):
        try:
            contenido = archivo.read_bytes()
            try:
                raices.append(x509.load_pem_x509_certificate(contenido))
            except ValueError:
                raices.append(x509.load_der_x509_certificate(contenido))
        except Exception as e:
            logger.error(f"No se pudo cargar la raíz {archivo.name}: {e}")

    if not raices:
        logger.error(
            "No hay raíces del SAT cargadas en %s: se rechazará toda firma",
            directorio,
        )

    revocados = set()
    for archivo in sorted(directorio.glob("*.crl")):
        try:
            crl = x509.load_der_x509_crl(archivo.read_bytes())
            revocados.update(r.serial_number for r in crl)
        except Exception as e:
            logger.error(f"No se pudo cargar la CRL {archivo.name}: {e}")

    return SatTrustStore(roots=raices, revoked_serials=revocados)
```

Añade también la prueba de que el cargador falla cerrado, en el mismo archivo de
pruebas:

```python
def test_el_cargador_por_defecto_rechaza_si_no_hay_raices(tmp_path, monkeypatch):
    """Una carpeta vacía no puede traducirse en 'confiar en cualquiera'."""
    from app.services.signature import sat_trust

    monkeypatch.setattr(
        sat_trust, "__file__", str(tmp_path / "sat_trust.py")
    )
    store = sat_trust.load_default_trust_store()

    assert store.roots == []
```

```markdown
<!-- backend/app/services/signature/sat_roots/README.md -->
# Raíces y AC intermedias del SAT

Los certificados de esta carpeta se descargan del portal del SAT y **se versionan
aquí a propósito**: una consulta en red dentro del camino de validación
convertiría una caída del SAT en una caída de este sistema.

Por cada archivo hay que anotar de dónde salió y en qué fecha se descargó, y
revisar la lista cuando el SAT publique una AC nueva.

| Archivo | Origen | Descargado |
|---|---|---|
| _(pendiente de poblar al ejecutar esta tarea)_ | | |
```

- [ ] **Paso 4: correr las pruebas y verlas pasar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_sat_trust.py -q -p no:warnings"`
Se espera: PASS (9 pruebas)

- [ ] **Paso 5: commit**

```bash
git add backend/app/services/signature/sat_trust.py backend/app/services/signature/sat_roots/README.md backend/tests/test_sat_trust.py
git commit -m "feat(firma): validar cadena del SAT, vigencia al firmar y revocación"
```

---

### Tarea 5: Evidencia de la firma

Con llaves en archivo nunca se puede probar *técnicamente* que firmó el titular y
no alguien que copió sus dos archivos. Los controles compensatorios son lo que
sostiene el caso, y solo sirven si quedan registrados.

**Archivos:**
- Crear: `backend/app/models/signature_evidence.py`
- Modificar: `backend/app/core/database.py` (registrar el documento en Beanie)

**Interfaces:**
- Produce: el documento `SignatureEvidence`. La tarea 6 lo escribe.

- [ ] **Paso 1: escribir el modelo**

```python
# backend/app/models/signature_evidence.py
"""
Evidencia de cada firma aceptada.

Con e.firma en archivo no se puede probar criptográficamente que firmó el
titular y no alguien con copia de sus archivos. Lo que sí se puede es dejar
registrado el conjunto de circunstancias que sostiene el caso: qué se firmó, con
qué certificado, quién estaba autenticado y cuándo.

Es un registro de solo-anexar: nada de este documento se actualiza después.
"""

from datetime import datetime
from typing import Optional

from beanie import Document
from pydantic import Field


class SignatureEvidence(Document):
    instance_id: str
    signature_field: str
    # Digest canónico de lo que se firmó, tal y como se persistió al solicitar
    # la firma.
    document_digest: str
    signature_base64: str
    certificate_pem: str
    algorithm: str
    # Identidad: RFC del certificado y del funcionario autenticado en la sesión
    # desde la que llegó la firma. Que coincidan es la comprobación; guardarlos
    # es la evidencia.
    certificate_rfc: Optional[str] = None
    authenticated_user_id: Optional[str] = None
    authenticated_rfc: Optional[str] = None
    signed_at: datetime
    verified_at: datetime = Field(default_factory=datetime.utcnow)
    # Sello de tiempo RFC 3161. Sin él no se puede probar *cuándo* se firmó una
    # vez que el certificado expire, y todos expiran a los cuatro años.
    timestamp_token_base64: Optional[str] = None

    class Settings:
        name = "signature_evidence"
        indexes = ["instance_id", "certificate_rfc", "signed_at"]
```

- [ ] **Paso 2: registrar el documento en Beanie**

Sin esto el modelo no tiene colección y los guardados fallan en tiempo de
ejecución. En `backend/app/core/database.py`, junto a los demás imports de
modelos (alrededor de la línea 25):

```python
from ..models.signature_evidence import SignatureEvidence
```

y dentro de la lista `document_models=[...]` que se pasa a `init_beanie`
(alrededor de la línea 50), añade una línea más:

```python
            SignatureEvidence,
```

- [ ] **Paso 3: verificar que el backend arranca con el modelo registrado**

Ejecuta: `docker restart backend-conapesca && docker logs backend-conapesca --tail 30`
Se espera: arranque sin errores de Beanie y el healthcheck en `healthy`.

- [ ] **Paso 4: commit**

```bash
git add backend/app/models/signature_evidence.py backend/app/core/database.py
git commit -m "feat(firma): documento de evidencia de firma"
```

---

### Tarea 6: Sustituir el "assume valid" del operador

La tarea que cierra el círculo. Hasta aquí nada cambia el comportamiento en
producción; a partir de aquí, una firma que no verifique se rechaza.

**Archivos:**
- Modificar: `backend/app/workflows/operators/signer_operator.py` (bloque
  `if signature_data:` que hoy imprime `Signature validation completed`)
- Test: `backend/tests/test_signer_operator_verification.py`

**Interfaces:**
- Consume: `canonical_digest` (tarea 1), `verify_against_digest` (tarea 2),
  `certificate_belongs_to` (tarea 3), `SatTrustStore.validate` (tarea 4),
  `SignatureEvidence` (tarea 5).

- [ ] **Paso 1: escribir la prueba que falla**

```python
# backend/tests/test_signer_operator_verification.py
"""
El operador no puede dar por buena una firma sin verificarla.

Antes marcaba `signature_valid: True` en cuanto encontraba algo en el campo del
contexto, con el comentario "assume valid since we're doing client-side
validation". El cliente no es una fuente de verdad sobre su propia firma.
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.signer_operator import SignerOperator


def _operador():
    return SignerOperator(task_id="sign_document", context_fields_to_sign=["x"])


@pytest.mark.asyncio
async def test_rechaza_una_firma_que_no_verifica():
    operador = _operador()

    resultado = await operador._verify_submitted_signature(
        digest_hex="a" * 64,
        signature_data={"signature": "bm8tdmFsaWRv", "certificate": "no-es-un-pem"},
        expected_rfc="GACD800101ABC",
    )

    assert resultado.valid is False


@pytest.mark.asyncio
async def test_rechaza_aunque_el_cliente_afirme_que_es_valida():
    """La afirmación del cliente no puede sustituir a la verificación."""
    operador = _operador()

    resultado = await operador._verify_submitted_signature(
        digest_hex="a" * 64,
        signature_data={
            "signature": "bm8tdmFsaWRv",
            "certificate": "no-es-un-pem",
            "signature_valid": True,
            "valid": True,
        },
        expected_rfc="GACD800101ABC",
    )

    assert resultado.valid is False


@pytest.mark.asyncio
async def test_rechaza_si_no_hay_digest_persistido():
    """Sin digest guardado no hay nada contra qué verificar: falla cerrado."""
    operador = _operador()

    resultado = await operador._verify_submitted_signature(
        digest_hex=None,
        signature_data={"signature": "x", "certificate": "y"},
        expected_rfc="GACD800101ABC",
    )

    assert resultado.valid is False
```

- [ ] **Paso 2: correr la prueba y verla fallar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/test_signer_operator_verification.py -q -p no:warnings"`
Se espera: FAIL con `AttributeError: 'SignerOperator' object has no attribute '_verify_submitted_signature'`

- [ ] **Paso 3: implementar `_verify_submitted_signature`**

Devuelve el mismo `TrustResult` de la tarea 4, para que haya un solo tipo de
resultado en toda la cadena de verificación.

```python
    async def _verify_submitted_signature(
        self,
        digest_hex: Optional[str],
        signature_data: Any,
        expected_rfc: Optional[str],
    ) -> "TrustResult":
        """
        Verifica una firma recibida. Se detiene en la primera comprobación que
        falle y devuelve la razón.

        Lo que venga dentro de `signature_data` afirmando validez se ignora por
        completo: el cliente no es fuente de verdad sobre su propia firma.
        """
        from ...services.signature.identity import certificate_belongs_to
        from ...services.signature.sat_trust import TrustResult
        from ...services.signature.signature_verifier import SignatureVerifier

        if not digest_hex:
            return TrustResult(False, "no hay digest persistido contra el que verificar")

        if not isinstance(signature_data, dict):
            return TrustResult(False, "la firma no trae la estructura esperada")

        firma = signature_data.get("signature")
        certificado = signature_data.get("certificate")
        algoritmo = signature_data.get("algorithm") or "RSA-SHA256"
        if not firma or not certificado:
            return TrustResult(False, "falta la firma o el certificado")

        trust = self._trust_store.validate(certificado, signed_at=datetime.utcnow())
        if not trust.valid:
            return trust

        if not certificate_belongs_to(certificado, expected_rfc):
            return TrustResult(
                False,
                "el certificado no corresponde al funcionario que procesó la firma",
            )

        verificada = await SignatureVerifier().verify_against_digest(
            digest_hex=digest_hex,
            signature_base64=firma,
            certificate_pem=certificado,
            algorithm=algoritmo,
        )
        if not verificada:
            return TrustResult(False, "la firma no corresponde al documento")

        return TrustResult(True)
```

Añade al principio del archivo los imports que faltan (`Optional`, `Any` de
`typing`) y, en `__init__`, la construcción del almacén de confianza:

```python
        # Almacén de raíces del SAT. Cargarlo en el constructor y no por firma
        # evita releer y reparsear los certificados en cada verificación.
        from ...services.signature.sat_trust import load_default_trust_store
        self._trust_store = load_default_trust_store()
```

- [ ] **Paso 4: sustituir el "assume valid" por la llamada**

En el bloque `if signature_data:` del método `execute`, borra estas dos líneas:

```python
                # For now, assume valid since we're doing client-side validation
                # Real validation would require crypto libraries
                print(f"   ✅ Signature validation completed")
```

y pon en su lugar:

```python
                pendiente = context.get(f"_signature_pending_{self.signature_field}") or {}
                expected_rfc = await self._resolve_signer_rfc(context)
                resultado = await self._verify_submitted_signature(
                    digest_hex=pendiente.get("data_hash"),
                    signature_data=signature_data,
                    expected_rfc=expected_rfc,
                )
                if not resultado.valid:
                    await self.log_error(
                        "Firma rechazada",
                        error=None,
                        details={"razon": resultado.reason},
                    )
                    self.state.error_message = f"Firma rechazada: {resultado.reason}"
                    return TaskResult(
                        status=TaskStatus.FAILED,
                        error=f"Firma rechazada: {resultado.reason}",
                    )

                await SignatureEvidence(
                    instance_id=getattr(self, "_instance_id", None) or context.get("instance_id"),
                    signature_field=self.signature_field,
                    document_digest=pendiente["data_hash"],
                    signature_base64=signature_data["signature"],
                    certificate_pem=signature_data["certificate"],
                    algorithm=signature_data.get("algorithm") or "RSA-SHA256",
                    certificate_rfc=extract_rfc(signature_data["certificate"]),
                    authenticated_user_id=context.get("user_id"),
                    authenticated_rfc=expected_rfc,
                    signed_at=datetime.utcnow(),
                ).insert()
```

con estos imports en el archivo:

```python
from ...models.signature_evidence import SignatureEvidence
from ...services.signature.identity import extract_rfc
```

`_resolve_signer_rfc(context)` es un método nuevo que devuelve el RFC del
funcionario autenticado. El operador ya tiene `_resolve_signer_name(context, ...)`
para resolver el nombre real del firmante desde el contexto; sigue ese mismo
patrón para obtener el RFC del perfil, y devuelve `None` si no lo encuentra —
`certificate_belongs_to` falla cerrado ante un RFC esperado vacío, que es el
comportamiento correcto.

- [ ] **Paso 5: correr las pruebas y verlas pasar**

Ejecuta: `docker exec backend-conapesca sh -c "cd /app && python -m pytest tests/ -q -p no:warnings"`
Se espera: PASS en toda la suite

- [ ] **Paso 6: commit**

```bash
git add backend/app/workflows/operators/signer_operator.py backend/tests/test_signer_operator_verification.py
git commit -m "fix(firma): verificar la firma en vez de darla por buena"
```

---

## Sello de tiempo RFC 3161

Queda deliberadamente fuera de las tareas anteriores porque exige una decisión de
negocio previa: **con qué TSA se contrata**. Sin sello de tiempo no se puede
probar *cuándo* se firmó una vez que el certificado expire, y todos expiran a los
cuatro años, así que hay que resolverlo antes de que estas firmas tengan que
sostenerse en el tiempo. El campo `timestamp_token_base64` de `SignatureEvidence`
ya lo contempla.

## Lo que este plan no resuelve, y hay que decirlo

Con llaves en archivo **nunca** se podrá probar técnicamente que firmó el
funcionario y no alguien que copió sus dos archivos y conoce la contraseña. Es una
propiedad del insumo —el SAT emite archivos—, no de la implementación. Lo que este
plan construye son los controles compensatorios que sostienen el caso: firma
verificada contra lo que realmente se mostró, certificado de una raíz confiable y
no revocado, identidad atada al funcionario, y evidencia registrada.

Presentarlo como no repudio pleno sería incorrecto.

## Plan siguiente, aparte

**Origen aislado para la página de firma.** Subdominio propio con bundle mínimo,
CSP estricta, sin analítica ni terceros, comunicándose con el portal por
`postMessage`: entra el digest, sale la firma. Con eso, un XSS en cualquier otra
parte del portal no alcanza la llave. Es contención de un riesgo que ya existe y
no depende de este trabajo.

**Envoltura en formato estándar.** Hoy la firma se guarda como un JSON propio.
Envolverla en **PAdES** cuando el producto final es un PDF, o **XAdES**/**CAdES**
si no lo es, permitiría que otra dependencia, un auditor o un juzgado la
verifiquen con herramientas normales en lugar de tener que confiar en la palabra
de MuniStream. Depende de tener resuelto el sello de tiempo.
