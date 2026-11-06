"""Ingreso con SSO corporativo (OIDC, código de autorización + PKCE).

Funciona con Entra ID, ADFS o Keycloak. El id_token se valida con las llaves
publicadas por el proveedor (JWKS): firma, emisor, audiencia, vencimiento y nonce.
El MFA lo exige el proveedor; para el step-up se vuelve a pedir autenticación con
max_age=0 y se verifica el claim acr/amr.
El estado (state, nonce, verificador PKCE) viaja en una cookie corta firmada.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt


class ErrorOIDC(PermissionError):
    pass


class ClienteOIDC:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_uri: str, *,
                 claim_grupos: str = "groups", acr_mfa: list[str] | None = None, clave_estado: str):
        self.issuer, self.client_id, self.secret = issuer.rstrip("/"), client_id, client_secret
        self.redirect_uri, self.claim_grupos = redirect_uri, claim_grupos
        self.acr_mfa = set(acr_mfa or ["mfa"])
        self._clave_estado = clave_estado
        self._meta: dict | None = None
        self._jwks: jwt.PyJWKClient | None = None

    def meta(self) -> dict:
        if self._meta is None:
            r = httpx.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
            r.raise_for_status()
            self._meta = r.json()
            self._jwks = jwt.PyJWKClient(self._meta["jwks_uri"])
        return self._meta

    def iniciar(self, *, stepup: bool = False) -> tuple[str, str]:
        """Devuelve (url de redirección, cookie de estado firmada)."""
        verificador = secrets.token_urlsafe(48)
        reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        p = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
             "scope": "openid profile email", "state": state, "nonce": nonce,
             "code_challenge": reto, "code_challenge_method": "S256"}
        if stepup:
            p.update(prompt="login", max_age="0", acr_values=" ".join(sorted(self.acr_mfa)))
        cookie = jwt.encode({"s": state, "n": nonce, "v": verificador, "u": stepup, "exp": int(time.time()) + 600},
                            self._clave_estado, algorithm="HS256")
        return f"{self.meta()['authorization_endpoint']}?{urlencode(p)}", cookie

    def completar(self, code: str, state: str, cookie: str | None) -> dict:
        if not cookie:
            raise ErrorOIDC("falta el estado del ingreso")
        try:
            est = jwt.decode(cookie, self._clave_estado, algorithms=["HS256"])
        except jwt.PyJWTError:
            raise ErrorOIDC("estado de ingreso inválido o vencido") from None
        if not secrets.compare_digest(est["s"], state or ""):
            raise ErrorOIDC("state no coincide")
        r = httpx.post(self.meta()["token_endpoint"], timeout=10, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": self.client_id, "client_secret": self.secret, "code_verifier": est["v"]})
        if r.status_code != 200:
            raise ErrorOIDC("el proveedor rechazó el código de autorización")
        idt = r.json()["id_token"]
        llave = self._jwks.get_signing_key_from_jwt(idt).key  # type: ignore[union-attr]
        claims = jwt.decode(idt, llave, algorithms=["RS256", "ES256", "PS256"], audience=self.client_id,
                            issuer=self.meta()["issuer"], options={"require": ["exp", "iat", "sub", "nonce"]})
        if not secrets.compare_digest(claims["nonce"], est["n"]):
            raise ErrorOIDC("nonce no coincide")
        amr = set(claims.get("amr") or [])
        mfa = claims.get("acr") in self.acr_mfa or bool(amr & {"mfa", "otp", "hwk", "swk", "fido"})
        if est.get("u") and not mfa:
            raise ErrorOIDC("el proveedor no confirmó el segundo factor")
        grupos = claims.get(self.claim_grupos) or []
        if isinstance(grupos, str):
            grupos = [grupos]
        return {"login": (claims.get("preferred_username") or claims.get("upn") or claims["sub"]).split("@")[0],
                "nombre": claims.get("name", ""), "correo": claims.get("email", ""), "grupos": grupos,
                "mfa": mfa, "stepup": bool(est.get("u"))}
