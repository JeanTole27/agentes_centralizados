"""Casos de Jira (opcionales) para completar una ejecución.

Una ejecución NO depende de un caso: puede asociarse a una clave de Jira, a otra
referencia libre o a ninguna. Si la persona asocia una clave de Jira, la API trae
el caso (solo lectura) y propone valores para los parámetros de la acción:

- Los campos configurados en [jira.campos] (parámetro -> campo de Jira, p. ej. un customfield).
- Las bases, servidores y dominios del inventario que aparecen en el resumen, la
  descripción, las etiquetas o los componentes del caso.
- El motivo: "<CLAVE>: <resumen>".

La persona revisa y confirma: lo propuesto no se ejecuta solo.
Cliente adaptado de oracle-agent-kiro-master/mcp_servers/jira_mcp (API REST v3 de Jira Cloud).
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from .catalogo import Catalogo
from .inventario import Inventario
from .secretos import Secretos

RE_CLAVE_JIRA = re.compile(r"^[A-Z][A-Z0-9_]{1,19}-[0-9]{1,9}$")
CAMPOS_BASE = ["summary", "status", "priority", "assignee", "reporter", "description", "issuetype", "project",
               "created", "updated", "labels", "components"]


class JiraNoConfigurado(RuntimeError):
    """[jira] url está vacío o falta la credencial."""


class ErrorJira(RuntimeError):
    """Jira respondió con error o no se pudo contactar."""


# ------------------------------------------------------------------ ADF (Atlassian Document Format) -> texto
def adf_a_texto(nodo: Any) -> str:
    if not nodo:
        return ""
    if isinstance(nodo, str):
        return nodo
    if isinstance(nodo, list):
        return "".join(adf_a_texto(n) for n in nodo)
    t = nodo.get("type")
    if t == "text":
        return nodo.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return "@" + nodo.get("attrs", {}).get("text", "")
    if t == "inlineCard":
        return nodo.get("attrs", {}).get("url", "")
    hijos = adf_a_texto(nodo.get("content", []))
    if t in ("paragraph", "heading", "blockquote", "codeBlock", "listItem", "tableRow"):
        return hijos.rstrip("\n") + "\n"
    return hijos


def _valor(v: Any) -> Any:
    """Valor legible de un campo de Jira (select, usuario, opción, lista o escalar)."""
    if v is None:
        return None
    if isinstance(v, dict):
        if v.get("type") == "doc":
            return adf_a_texto(v).strip()
        return v.get("value", v.get("displayName", v.get("name", v.get("key"))))
    if isinstance(v, list):
        return [_valor(x) for x in v]
    return v


class ClienteJira:
    def __init__(self, url: str, usuario: str, token: str, *, timeout_s: int = 20, verificar_tls: bool | str = True,
                 transporte: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self._http = httpx.Client(base_url=self.url, auth=(usuario, token), timeout=timeout_s, verify=verificar_tls,
                                  headers={"Accept": "application/json"}, transport=transporte)

    def _pedir(self, metodo: str, ruta: str, **kw) -> dict:
        try:
            r = self._http.request(metodo, ruta, **kw)
        except httpx.HTTPError as e:
            raise ErrorJira(f"no se pudo contactar a Jira: {e}") from None
        if r.status_code == 404:
            raise KeyError("el caso no existe en Jira o no tienes acceso")
        if r.status_code >= 400:
            try:
                d = r.json()
                detalle = "; ".join(d.get("errorMessages") or []) or str(d.get("errors") or d)
            except ValueError:
                detalle = r.text[:300]
            raise ErrorJira(f"Jira respondió {r.status_code}: {detalle}")
        return r.json() if r.content else {}

    def issue(self, clave: str, campos: list[str]) -> dict:
        return self._pedir("GET", f"/rest/api/3/issue/{clave}", params={"fields": ",".join(campos)})

    def buscar(self, jql: str, campos: list[str], maximo: int) -> list[dict]:
        d = self._pedir("POST", "/rest/api/3/search/jql", json={"jql": jql, "maxResults": maximo, "fields": campos})
        return d.get("issues", [])

    # ------------------------------------------------------------------ escritura (flujo del caso)
    def comentar(self, clave: str, parrafos: list[str]) -> dict:
        cuerpo = {"type": "doc", "version": 1,
                  "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in parrafos if p]}
        return self._pedir("POST", f"/rest/api/3/issue/{clave}/comment", json={"body": cuerpo})

    def adjuntar(self, clave: str, nombre: str, contenido: bytes, tipo: str = "application/zip") -> list:
        return self._pedir("POST", f"/rest/api/3/issue/{clave}/attachments",  # type: ignore[return-value]
                           headers={"X-Atlassian-Token": "no-check"}, files={"file": (nombre, contenido, tipo)})

    def transiciones(self, clave: str) -> list[dict]:
        return self._pedir("GET", f"/rest/api/3/issue/{clave}/transitions").get("transitions", [])

    def transicionar(self, clave: str, transicion_id: str) -> None:
        self._pedir("POST", f"/rest/api/3/issue/{clave}/transitions", json={"transition": {"id": str(transicion_id)}})


class ServicioCasos:
    def __init__(self, cfg, secretos: Secretos, inventario: Inventario, catalogo: Catalogo,
                 transporte: httpx.BaseTransport | None = None):
        self.c = cfg["jira"]
        self.sec, self.inv, self.cat = secretos, inventario, catalogo
        self._transporte = transporte
        self._cliente: ClienteJira | None = None

    @property
    def habilitado(self) -> bool:
        return bool(self.c.get("url"))

    def cliente(self) -> ClienteJira:
        if not self.habilitado:
            raise JiraNoConfigurado("Jira no está configurado ([jira] url): el caso queda como referencia libre")
        if self._cliente is None:
            try:
                cred = self.sec.leer(self.c["credencial_ref"], motivo="consultar casos de Jira")
            except KeyError:
                raise JiraNoConfigurado(f"falta la credencial de Jira en el gestor de secretos "
                                        f"({self.c['credencial_ref']})") from None
            self._cliente = ClienteJira(self.c["url"], cred["usuario"], cred["token"],
                                        timeout_s=self.c.get("timeout_s", 20),
                                        verificar_tls=self.c.get("verificar_tls", True), transporte=self._transporte)
        return self._cliente

    def _campos(self) -> list[str]:
        return CAMPOS_BASE + [f for f in (self.c.get("campos") or {}).values() if f not in CAMPOS_BASE]

    # -------------------------------------------------------------- lecturas
    def buscar(self, texto: str = "") -> list[dict]:
        texto = (texto or "").strip()
        if not texto:
            jql = self.c["jql_abiertos"]
        elif RE_CLAVE_JIRA.match(texto.upper()):
            jql = f'key = "{texto.upper()}"'
        else:
            limpio = re.sub(r'["\\]', " ", texto)[:100]
            jql = f'text ~ "{limpio}" ORDER BY updated DESC'
        filas = self.cliente().buscar(jql, CAMPOS_BASE, int(self.c.get("max_resultados", 25)))
        return [self._resumen(f, completo=False) for f in filas]

    def caso(self, clave: str, accion_id: str | None = None) -> dict:
        clave = clave.strip().upper()
        if not RE_CLAVE_JIRA.match(clave):
            raise ValueError("clave de Jira inválida (PROYECTO-123)")
        raw = self.cliente().issue(clave, self._campos())
        out = self._resumen(raw, completo=True)
        out["sugeridos"] = self.sugerir(raw, accion_id)
        return out

    def _resumen(self, raw: dict, *, completo: bool) -> dict:
        f = raw.get("fields", {})
        out = {
            "clave": raw.get("key"), "resumen": f.get("summary") or "",
            "estado": _valor(f.get("status")) or "", "tipo": _valor(f.get("issuetype")) or "",
            "prioridad": _valor(f.get("priority")) or "", "asignado": _valor(f.get("assignee")) or "Sin asignar",
            "proyecto": (f.get("project") or {}).get("key", ""), "actualizado": (f.get("updated") or "")[:16],
            "url": f"{self.c['url'].rstrip('/')}/browse/{raw.get('key')}",
        }
        if completo:
            out.update({"descripcion": adf_a_texto(f.get("description")).strip(),
                        "informante": _valor(f.get("reporter")) or "", "creado": (f.get("created") or "")[:16],
                        "etiquetas": f.get("labels") or [],
                        "componentes": [c.get("name") for c in f.get("components") or []]})
        return out

    # -------------------------------------------------------------- completar parámetros
    def sugerir(self, raw: dict, accion_id: str | None = None) -> dict[str, Any]:
        f = raw.get("fields", {})
        sug: dict[str, Any] = {}
        for param, campo in (self.c.get("campos") or {}).items():
            v = _valor(f.get(campo))
            if v not in (None, "", []):
                sug[param] = v
        texto = " ".join([f.get("summary") or "", adf_a_texto(f.get("description")),
                          " ".join(f.get("labels") or []), " ".join(c.get("name", "") for c in f.get("components") or [])])
        palabras = {p.upper() for p in re.findall(r"[A-Za-z0-9_$#.\-]+", texto)}
        palabras |= {p.strip(".-") for p in palabras}
        bases = sorted(a.id for a in self.inv.activos.values() if a.tipo == "base" and a.id in palabras)
        servidores = sorted(a.id for a in self.inv.activos.values() if a.tipo == "servidor" and a.id in palabras)
        dominios = sorted(a.id for a in self.inv.activos.values() if a.tipo == "dominio" and a.id.upper() in palabras)
        if bases:
            sug.setdefault("base", bases[0])
        if servidores:
            sug.setdefault("host", servidores[0])
            sug.setdefault("hosts", ",".join(servidores))
        if dominios:
            sug.setdefault("dominio", dominios[0])
            sug.setdefault("dominios", ",".join(dominios))
        resumen = (f.get("summary") or "").strip()
        if resumen:
            sug.setdefault("motivo", f"{raw.get('key')}: {resumen}"[:400])
            sug.setdefault("justificacion", sug["motivo"])
        sug["cambio"] = raw.get("key")
        if accion_id and accion_id in self.cat:
            acc = self.cat[accion_id]
            permitidos = set(acc.parametros) | {"motivo", "cambio"}
            sug = {k: v for k, v in sug.items() if k in permitidos}
            # 'hosts' y 'host' son alternativos: se deja el que la acción declara
            if "hosts" in sug and "host" in sug:
                sug.pop("host")
        return sug

    # -------------------------------------------------------------- escritura (flujo integrado)
    def _clave(self, clave: str) -> str:
        clave = (clave or "").strip().upper()
        if not RE_CLAVE_JIRA.match(clave):
            raise ValueError("clave de Jira inválida (PROYECTO-123)")
        return clave

    def comentar_ejecucion(self, clave: str, e: dict, quien: str) -> dict:
        """Comenta en el caso el resultado de una ejecución de Ops Central (qué, dónde, quién, resultado)."""
        clave = self._clave(clave)
        res = e.get("resultado") or {}
        resumen = res.get("resumen") if isinstance(res.get("resumen"), dict) else None
        parrafos = [f"Ops Central · {e['accion']} en {e['destino']} ({e.get('ambiente') or 's/a'})",
                    f"Estado: {e['estado']} · solicitó {e['solicitante']} · {str(e.get('terminada') or e.get('creada'))[:19]} UTC",
                    f"Motivo: {e['motivo']}" if e.get("motivo") else "",
                    f"Resumen: {', '.join(f'{k}={v}' for k, v in resumen.items())}" if resumen else "",
                    f"Error: {str(e['error'])[:500]}" if e.get("error") else "",
                    f"Evidencia SHA-256: {e['evidencia_sha256']}" if e.get("evidencia_sha256") else "",
                    f"Publicado por {quien} desde Ops Central (ejecución {e['id']})."]
        self.cliente().comentar(clave, parrafos)
        return {"clave": clave, "comentado": True}

    def publicar_evidencia(self, clave: str, nombre: str, contenido: bytes, parrafos: list[str]) -> dict:
        """Adjunta el ZIP de evidencia al caso y deja un comentario con el resultado."""
        clave = self._clave(clave)
        c = self.cliente()
        adj = c.adjuntar(clave, nombre, contenido)
        c.comentar(clave, parrafos)
        return {"clave": clave, "adjunto": nombre, "adjuntos": len(adj or []), "comentado": True}

    def transiciones(self, clave: str) -> list[dict]:
        return [{"id": t.get("id"), "nombre": t.get("name"), "a": (t.get("to") or {}).get("name")}
                for t in self.cliente().transiciones(self._clave(clave))]

    def transicionar(self, clave: str, transicion_id: str) -> dict:
        clave = self._clave(clave)
        ids = {t["id"] for t in self.transiciones(clave)}
        if str(transicion_id) not in ids:
            raise ValueError("esa transición no está disponible para el caso en su estado actual")
        self.cliente().transicionar(clave, transicion_id)
        return {"clave": clave, "transicion": transicion_id}
