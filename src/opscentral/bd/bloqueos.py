"""Árbol de sesiones bloqueantes (F3, RF-BLQ).

A partir de las filas de GV$SESSION arma los árboles: la raíz es la sesión que
bloquea y no está bloqueada por nadie. Calcula cuántas sesiones afecta (de forma
transitiva), la espera máxima y detecta ciclos (interbloqueo).
Para el perfil Capa Media filtra a las sesiones cuyo `machine` es de sus servidores.
"""
from __future__ import annotations

Id = tuple[int, int]


def _id(f: dict) -> Id:
    return (int(f.get("inst_id") or 1), int(f["sid"]))


def _padre(f: dict) -> Id | None:
    if f.get("blocking_session") is None:
        return None
    return (int(f.get("blocking_instance") or f.get("inst_id") or 1), int(f["blocking_session"]))


def arbol(filas: list[dict]) -> dict:
    nodos = {_id(f): dict(f) for f in filas}
    hijos: dict[Id, list[Id]] = {}
    for i, f in nodos.items():
        p = _padre(f)
        if p is not None:
            hijos.setdefault(p, []).append(i)
            if p not in nodos:  # la raíz no vino en la consulta: se crea un nodo mínimo
                nodos[p] = {"inst_id": p[0], "sid": p[1], "username": None, "incompleto": True}

    raices = [i for i, f in nodos.items() if _padre(f) is None and i in hijos]
    ciclos: list[list[Id]] = []
    en_ciclo: set[Id] = set()
    for i in nodos:
        camino, actual = [], i
        while actual is not None and actual not in camino:
            camino.append(actual)
            actual = _padre(nodos[actual]) if actual in nodos else None
        if actual is not None and actual in camino:
            ciclo = camino[camino.index(actual):]
            if not set(ciclo) & en_ciclo:
                ciclos.append(ciclo)
                en_ciclo |= set(ciclo)

    def bajar(i: Id, prof: int, vistos: set) -> dict:
        vistos.add(i)
        n = dict(nodos[i])
        n["nivel"] = prof
        n["hijos"] = [bajar(h, prof + 1, vistos) for h in sorted(hijos.get(i, [])) if h not in vistos]
        return n

    def contar(n: dict) -> tuple[int, int]:
        total, espera = 0, 0
        for h in n["hijos"]:
            t, e = contar(h)
            total += 1 + t
            espera = max(espera, int(h.get("seconds_in_wait") or 0), e)
        return total, espera

    arboles = []
    for r in raices:
        t = bajar(r, 0, set())
        afectadas, espera = contar(t)
        arboles.append({"raiz": {k: v for k, v in t.items() if k != "hijos"}, "arbol": t,
                        "afectadas": afectadas, "espera_max_s": espera,
                        "objetos": sorted({h.get("objeto") for h in _aplanar(t) if h.get("objeto")})})
    arboles.sort(key=lambda a: (-a["afectadas"], -a["espera_max_s"]))
    return {"arboles": arboles, "ciclos": [[{"inst_id": a, "sid": b} for a, b in c] for c in ciclos],
            "total_bloqueadas": sum(a["afectadas"] for a in arboles)}


def _aplanar(n: dict):
    yield n
    for h in n.get("hijos", []):
        yield from _aplanar(h)


def filtrar_por_servidores(filas: list[dict], servidores: set[str]) -> list[dict]:
    """Capa Media solo ve sesiones que vienen de sus servidores (02 §4.2 nota ³)."""
    s = {x.upper() for x in servidores}

    def es_suya(f):
        m = str(f.get("machine") or "").upper().split(".")[0].split("\\")[-1]
        return m in s

    suyas = [f for f in filas if es_suya(f)]
    ids = {_id(f) for f in suyas}
    # se conserva la raíz que bloquea a una sesión suya, para que entienda quién la bloquea
    padres = {_padre(f) for f in suyas if _padre(f)}
    return [f for f in filas if _id(f) in ids or _id(f) in padres]
