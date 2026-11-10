# -*- coding: utf-8 -*-
"""Inventario central: servidores (desde la linea base) + configuracion por modulo.

Archivos:
  inventario/servidores.json            -> generado con herramientas/importar_inventario.py
  inventario/configuracion_modulos.json -> dominios WebLogic, usuarios WL, frmweb (editable)
  inventario/hechos/<HOST>.json         -> informacion recolectada por SSH (modulo recoleccion)
"""
import os
import threading

from .config import escribir_json, leer_json, nombre_seguro, ruta


class Inventario(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.recargar()

    # ------------------------------------------------------------------ carga
    def recargar(self):
        with self._lock:
            srv = leer_json(ruta("inventario", "servidores.json"), {}) or {}
            self.meta = {k: v for k, v in srv.items() if k != "servidores"}
            self.servidores = srv.get("servidores", {})
            self.modulos = leer_json(ruta("inventario", "configuracion_modulos.json"), {}) or {}

    # ------------------------------------------------------------ servidores
    def ambientes_prod(self):
        return [a.lower() for a in self.cfg.get("ambientes_productivos", ["prod", "drp"])]

    def es_prod(self, hostname):
        info = self.servidores.get(hostname, {})
        ambs = [a.lower() for a in info.get("ambientes", [])]
        if not ambs:
            # host no inventariado: se trata como productivo por seguridad
            return True
        return any(a in self.ambientes_prod() for a in ambs)

    def host(self, hostname, modulo=None):
        """Datos de conexion de un host. El modulo puede sobreescribir ssh_ip/ssh_port."""
        nombre_seguro(hostname, "hostname")
        base = dict(self.servidores.get(hostname, {}))
        extra = {}
        if modulo == "orquestacion":
            extra = self.orquestacion().get(hostname, {})
        elif modulo == "frmweb":
            extra = self.frmweb().get(hostname, {})
        if not base and not extra:
            raise KeyError("host no existe en el inventario: %s" % hostname)
        ip = extra.get("ssh_ip") or base.get("ip")
        if not ip:
            raise KeyError("host sin IP en el inventario: %s" % hostname)
        return {
            "hostname": hostname,
            "ip": ip,
            "ssh_port": int(extra.get("ssh_port") or base.get("ssh_port") or 22),
            "ssh_usuario": extra.get("ssh_usuario") or base.get("ssh_usuario") or "",
            "ambientes": base.get("ambientes", []),
            "es_prod": self.es_prod(hostname),
        }

    def listar(self, ambiente=None, q=None):
        out = []
        q = (q or "").strip().lower()
        for h, info in sorted(self.servidores.items()):
            ambs = info.get("ambientes", [])
            if ambiente and ambiente not in ambs:
                continue
            doms = [d.get("nombre", "") for d in info.get("dominios", [])]
            if q and q not in h.lower() and q not in info.get("ip", "") and not any(q in d.lower() for d in doms):
                continue
            hechos = self.hechos(h)
            out.append({
                "hostname": h,
                "ip": info.get("ip"),
                "ips_alternas": info.get("ips_alternas", []),
                "ambientes": ambs,
                "dominios": doms,
                "es_prod": self.es_prod(h),
                "ssh_usuario": info.get("ssh_usuario") or self.cfg.get("ssh", {}).get("usuario"),
                "habilitado": info.get("habilitado", True),
                "modulos": self.modulos_de_host(h),
                "recolectado": (hechos or {}).get("_recolectado"),
                "so": (hechos or {}).get("so"),
            })
        return out

    def modulos_de_host(self, h):
        m = []
        if h in self.orquestacion():
            m.append("orquestacion")
        if any(d.get("host") == h for d in self.usuarios_wl().values()):
            m.append("usuarios_wl")
        if h in self.frmweb():
            m.append("frmweb")
        return m

    def resumen(self):
        por_amb = {}
        dominios = set()
        for info in self.servidores.values():
            for a in info.get("ambientes", []):
                por_amb[a] = por_amb.get(a, 0) + 1
            for d in info.get("dominios", []):
                dominios.add(d.get("nombre"))
        orq = self.orquestacion()
        from .modulos import instalaciones_de
        insts = [i for v in orq.values() for i in instalaciones_de(v)]
        return {
            "servidores": len(self.servidores),
            "dominios_inventario": len(dominios),
            "por_ambiente": por_amb,
            "orq_hosts": len(orq),
            "orq_instalaciones": len(insts),
            "orq_dominios": sum(len(i.get("dominios", [])) for i in insts),
            "orq_managed_servers": sum(len(d.get("servers", [])) for i in insts for d in i.get("dominios", [])),
            "usuarios_wl_dominios": len(self.usuarios_wl()),
            "frmweb_hosts": len(self.frmweb()),
            "hechos": len([f for f in os.listdir(ruta("inventario", "hechos")) if f.endswith(".json")])
            if os.path.isdir(ruta("inventario", "hechos")) else 0,
            "meta": self.meta,
        }

    # ---------------------------------------------------------------- hechos
    def hechos(self, hostname):
        try:
            nombre_seguro(hostname, "hostname")
        except ValueError:
            return None
        return leer_json(ruta("inventario", "hechos", hostname + ".json"), None)

    def guardar_hechos(self, hostname, datos):
        nombre_seguro(hostname, "hostname")
        escribir_json(ruta("inventario", "hechos", hostname + ".json"), datos)

    # --------------------------------------------------------------- modulos
    def orquestacion(self):
        return self.modulos.get("orquestacion", {}).get("hosts", {})

    def usuarios_wl(self):
        return self.modulos.get("usuarios_wl", {}).get("dominios", {})

    def frmweb(self):
        return self.modulos.get("frmweb", {}).get("hosts", {})
