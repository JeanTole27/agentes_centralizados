"""Token de aprobación firmado (cierra H3).

El token liga la aprobación a la huella exacta de lo que se va a ejecutar:
acción + destino + parámetros normalizados (+ número de cambio). El worker
recalcula la huella justo antes de ejecutar; si alguien alteró algo, no corre.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt

ALG = "HS256"
EMISOR = "ops-central"


class TokenInvalido(PermissionError):
    pass


def huella(accion_id: str, destino: str, params: dict, cambio: str | None = None) -> str:
    """SHA-256 de la forma canónica. Las claves se ordenan y no hay espacios variables."""
    canon = json.dumps({"accion": accion_id, "destino": (destino or "").upper(), "params": params,
                        "cambio": (cambio or "").upper()},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def emitir(clave: str, *, ejecucion_id: str, huella_hex: str, aprobador: str, solicitante: str,
           vigencia_min: int) -> str:
    t = datetime.now(timezone.utc)
    carga = {"iss": EMISOR, "sub": ejecucion_id, "jti": uuid.uuid4().hex, "iat": t,
             "exp": t + timedelta(minutes=vigencia_min), "huella": huella_hex,
             "aprobador": aprobador, "solicitante": solicitante}
    return jwt.encode(carga, clave, algorithm=ALG)


def verificar(clave: str, token: str, *, ejecucion_id: str, huella_hex: str) -> dict:
    try:
        carga = jwt.decode(token, clave, algorithms=[ALG], issuer=EMISOR,
                           options={"require": ["exp", "iat", "sub", "huella", "jti"]})
    except jwt.ExpiredSignatureError:
        raise TokenInvalido("la aprobación venció; se debe aprobar de nuevo") from None
    except jwt.PyJWTError as e:
        raise TokenInvalido(f"token de aprobación inválido: {e}") from None
    if carga["sub"] != ejecucion_id:
        raise TokenInvalido("el token pertenece a otra ejecución")
    if carga["huella"] != huella_hex:
        raise TokenInvalido("lo que se va a ejecutar no coincide con lo aprobado")
    if carga.get("aprobador") == carga.get("solicitante"):
        raise TokenInvalido("autoaprobación no permitida")
    return carga
