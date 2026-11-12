"""Inventario maestro y grafo servidor → dominio → base (H4).

Sirve para: saber el ambiente de un destino (¿es PROD?), asociar un evento de
Zabbix a su activo y agrupar alertas relacionadas en un episodio.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Activo:
    id: str
    tipo: str              # servidor | dominio | base
    ambiente: str
    datos: dict

    @property
    def clave(self) -> str:
        return f"{self.tipo}:{self.id}"


class Inventario:
    def __init__(self, datos: dict):
        self._armar(datos)

    def recargar(self, datos: dict) -> None:
        """Rearma el grafo en el mismo objeto (lo comparten la API, los workers y los servicios)."""
        nuevo = Inventario(datos)
        self.__dict__.update(nuevo.__dict__)

    def _armar(self, datos: dict) -> None:
        self.fuente = datos
        self.activos: dict[str, Activo] = {}
        self._por_zbx: dict[str, Activo] = {}
        self._vecinos: dict[str, set[str]] = defaultdict(set)
        for s in datos.get("servidores", []):
            self._agregar(Activo(s["id"].upper(), "servidor", s.get("ambiente", "").upper(), s))
        for d in datos.get("dominios", []):
            a = self._agregar(Activo(d["id"], "dominio", d.get("ambiente", "").upper(), d))
            for srv in d.get("servidores") or [d["servidor"]]:   # un dominio en clúster vive en varios nodos
                self._unir(a.clave, f"servidor:{srv.upper()}")
        for b in datos.get("bases", []):
            a = self._agregar(Activo(b["id"].upper(), "base", b.get("ambiente", "").upper(), b))
            if b.get("servidor"):
                self._unir(a.clave, f"servidor:{b['servidor'].upper()}")
        for r in datos.get("relaciones", []):
            self._unir(f"dominio:{r['dominio']}", f"base:{r['base'].upper()}")
        # Grupos RAC: varios alias del kit apuntan a la MISMA base por distintos nodos (PROD_TRON,
        # PROD_TRON1..3 con servicio TRON). Las consultas GV$ y DBA_ devuelven lo mismo desde cualquiera:
        # se leen una sola vez, en el representante, y los demás alias reutilizan ese dato.
        # Grupo = mismo servicio y ambiente; la recolección lo confirma con el DBID de V$DATABASE.
        grupos: dict[tuple[str, str], list[str]] = defaultdict(list)
        for a in self.activos.values():
            if a.tipo == "base" and a.datos.get("servicio"):
                grupos[(str(a.datos["servicio"]).upper(), a.ambiente)].append(a.id)
        self._grupo: dict[str, list[str]] = {}
        for ids in grupos.values():
            if len(ids) > 1:
                orden = sorted(ids, key=lambda x: (len(x), x))   # PROD_TRON antes que PROD_TRON1
                for i in ids:
                    self._grupo[i] = orden

    @classmethod
    def desde_archivo(cls, ruta: Path) -> "Inventario":
        return cls(json.loads(Path(ruta).read_text(encoding="utf-8")))

    def _agregar(self, a: Activo) -> Activo:
        self.activos[a.clave] = a
        zh = a.datos.get("zabbix_host")
        if zh:
            self._por_zbx[zh.upper()] = a
        return a

    def _unir(self, x: str, y: str) -> None:
        self._vecinos[x].add(y)
        self._vecinos[y].add(x)

    # -------------------------------------------------------------- consultas
    def base(self, nombre: str) -> Activo | None:
        return self.activos.get(f"base:{nombre.upper()}")

    def servidor(self, nombre: str) -> Activo | None:
        return self.activos.get(f"servidor:{nombre.upper()}")

    def buscar(self, nombre: str) -> Activo | None:
        for t in ("base", "servidor", "dominio"):
            a = self.activos.get(f"{t}:{nombre.upper() if t != 'dominio' else nombre}")
            if a:
                return a
        return None

    def ambiente_de(self, destino: str) -> str | None:
        """Ambiente de un destino; si es una lista de hosts, el más protegido."""
        orden = ["PROD", "DRP", "QA", "TEST", "DESA"]
        ambientes = []
        for parte in str(destino).replace(";", ",").split(","):
            a = self.buscar(parte.strip())
            if a and a.ambiente:
                ambientes.append(a.ambiente)
        if not ambientes:
            return None
        return min(ambientes, key=lambda x: orden.index(x) if x in orden else len(orden))

    def por_zabbix(self, host: str) -> Activo | None:
        return self._por_zbx.get(host.upper()) or self.buscar(host)

    def relacionados(self, clave: str, saltos: int = 2) -> set[str]:
        """Activos a ≤ N saltos en el grafo (servidor ↔ dominio ↔ base)."""
        vistos, frontera = {clave}, {clave}
        for _ in range(saltos):
            frontera = {v for f in frontera for v in self._vecinos.get(f, ())} - vistos
            vistos |= frontera
        return vistos

    def bases(self, ambiente: str | None = None) -> list[Activo]:
        return [a for a in self.activos.values() if a.tipo == "base" and (not ambiente or a.ambiente == ambiente)]

    def grupo_rac(self, base: str) -> list[str]:
        """Alias que son la misma base (representante primero); [base] si no comparte servicio."""
        return list(self._grupo.get(base.upper(), [base.upper()]))

    def representante(self, base: str) -> str:
        return self.grupo_rac(base)[0]

    def hosts_zabbix(self, tipo: str | None = None) -> dict[str, Activo]:
        """Nombre del host en Zabbix → activo (solo los que tienen zabbix_host)."""
        return {h: a for h, a in self._por_zbx.items() if tipo is None or a.tipo == tipo}

    def bases_en(self, servidor: str) -> list[Activo]:
        """Bases alojadas en un servidor (una alerta del host de BD en Zabbix afecta a estas bases)."""
        return sorted((self.activos[c] for c in self._vecinos.get(f"servidor:{servidor.upper()}", ())
                       if c.startswith("base:")), key=lambda a: a.id)

    def servidor_por_ip(self, ip: str) -> Activo | None:
        for a in self.activos.values():
            if a.tipo == "servidor" and a.datos.get("ip") == ip:
                return a
        return None

    def servidores_de_equipo(self, equipo: str) -> set[str]:
        return {a.id for a in self.activos.values() if a.tipo == "servidor" and a.datos.get("equipo") == equipo}
