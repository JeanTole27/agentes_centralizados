"""Almacén de evidencia: un archivo JSON por ejecución, con su SHA-256.

Desarrollo: carpeta local. Producción: almacén de objetos con retención (S3 Object
Lock o recurso compartido WORM). La huella queda en la ejecución y en la auditoría,
así cualquier alteración posterior del archivo se detecta.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class Evidencia:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self.raiz.mkdir(parents=True, exist_ok=True)

    def guardar(self, ejecucion_id: str, contenido: dict) -> tuple[str, str]:
        datos = json.dumps(contenido, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")
        sha = hashlib.sha256(datos).hexdigest()
        carpeta = self.raiz / ejecucion_id[:8]
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"{ejecucion_id}.json"
        if ruta.exists():
            raise FileExistsError(f"la evidencia de {ejecucion_id} ya existe y no se sobrescribe")
        ruta.write_bytes(datos)
        return str(ruta.relative_to(self.raiz).as_posix()), sha

    def verificar(self, ruta_rel: str, sha: str) -> bool:
        p = self.raiz / ruta_rel
        return p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == sha

    def leer(self, ruta_rel: str) -> dict:
        return json.loads((self.raiz / ruta_rel).read_text(encoding="utf-8"))
