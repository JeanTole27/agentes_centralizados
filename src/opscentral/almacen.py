"""Almacén de Ops Central (04 §4).

El mismo código corre sobre SQLite (desarrollo) y PostgreSQL 16 (QA/PROD).
En PostgreSQL los nombres de tabla se agrupan por esquema lógico con prefijo
(ops_, ejec_, estado_, serie_, evento_, ml_, audit_), y las tablas de series,
eventos y auditoría se particionan por mes con pg_partman (db/postgres/).
La interfaz SOLO lee de aquí: nunca consulta las bases ni los servidores en vivo.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, MetaData, String, Table,
                        Text, UniqueConstraint, create_engine, event)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

JSONV = JSON().with_variant(JSONB(), "postgresql")
ID_AUTO = BigInteger().with_variant(Integer(), "sqlite")   # autoincremento en SQLite


def utc(dt: datetime | None = None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)


def TS(nombre: str, **kw) -> Column:
    return Column(nombre, DateTime(timezone=True), **kw)


meta = MetaData()

# ----------------------------------------------------------------- ops: sesiones
sesion = Table(
    "ops_sesion", meta,
    Column("huella", String(64), primary_key=True),
    Column("login", String(120), nullable=False, index=True),
    Column("nombre", String(200)),
    Column("perfiles", JSONV, nullable=False),
    Column("grupos", JSONV, nullable=False),
    Column("ip", String(64)),
    TS("creada", nullable=False), TS("ultima_actividad", nullable=False), TS("vence", nullable=False),
    TS("mfa_en"),
    Column("cerrada", Boolean, nullable=False, default=False),
)

intento_ingreso = Table(
    "ops_intento_ingreso", meta,
    Column("login", String(120), primary_key=True),
    Column("fallidos", Integer, nullable=False, default=0),
    TS("bloqueado_hasta"),
)

# Personas registradas en la demo (auth.proveedor = "demo"): nombre y perfil elegido, sin contraseña.
usuario_demo = Table(
    "ops_usuario_demo", meta,
    Column("login", String(60), primary_key=True),
    Column("nombre", String(200), nullable=False),
    Column("perfil", String(10), nullable=False),
    TS("creado", nullable=False), TS("ultimo_ingreso", nullable=False),
)

# ----------------------------------------------------------------- ejec: ejecuciones
ejecucion = Table(
    "ejec_ejecucion", meta,
    Column("id", String(32), primary_key=True),
    Column("accion", String(60), nullable=False, index=True),
    Column("motor", String(20), nullable=False),
    Column("clase", String(1), nullable=False),
    Column("destino", String(120), nullable=False, index=True),
    Column("ambiente", String(10)),
    Column("params", JSONV, nullable=False),
    Column("cambio", String(40), index=True),        # referencia del caso (opcional, p. ej. clave de Jira)
    Column("motivo", Text),
    Column("solicitante", String(120), nullable=False, index=True),
    Column("estado", String(24), nullable=False, index=True),
    Column("huella", String(64), nullable=False),
    Column("token_aprobacion", Text),
    Column("aprobador", String(120)),
    Column("comentario", Text),
    TS("creada", nullable=False), TS("actualizada", nullable=False), TS("iniciada"), TS("terminada"),
    Column("resultado", JSONV),
    Column("error", Text),
    Column("evidencia", String(400)),
    Column("evidencia_sha256", String(64)),
    Column("origen", String(20), nullable=False, default="persona"),   # persona | evento | planificador | asistente
)

linea = Table(
    "ejec_linea", meta,
    Column("id", ID_AUTO, primary_key=True, autoincrement=True),
    Column("ejecucion_id", String(32), nullable=False),
    Column("n", Integer, nullable=False),
    TS("ts", nullable=False),
    Column("texto", Text, nullable=False),
    UniqueConstraint("ejecucion_id", "n"),
)

bloqueo_destino = Table(
    "ejec_bloqueo_destino", meta,
    Column("destino", String(120), primary_key=True),
    Column("ejecucion_id", String(32), nullable=False),
    TS("desde", nullable=False),
)

# ----------------------------------------------------------------- cola de trabajos
trabajo = Table(
    "ops_trabajo", meta,
    Column("id", ID_AUTO, primary_key=True, autoincrement=True),
    Column("cola", String(20), nullable=False),
    Column("tipo", String(60), nullable=False),
    Column("payload", JSONV, nullable=False),
    Column("estado", String(12), nullable=False, default="pendiente"),  # pendiente | tomado | hecho | fallido
    Column("intentos", Integer, nullable=False, default=0),
    Column("max_intentos", Integer, nullable=False, default=1),
    TS("ejecutar_desde", nullable=False),
    Column("tomado_por", String(80)),
    TS("tomado_en"),
    Column("clave_unica", String(200)),
    Column("error", Text),
    TS("creado", nullable=False),
    Index("ix_trabajo_cola_estado", "cola", "estado", "ejecutar_desde"),
    Index("ix_trabajo_clave", "clave_unica", "estado"),
)

# ----------------------------------------------------------------- audit (solo agregar, cadena de hash)
auditoria = Table(
    "audit_evento", meta,
    Column("id", ID_AUTO, primary_key=True, autoincrement=True),
    TS("ts", nullable=False),
    Column("evento", String(60), nullable=False, index=True),
    Column("actor", String(120), nullable=False, index=True),
    Column("datos", JSONV, nullable=False),
    Column("hash_prev", String(64), nullable=False),
    Column("hash", String(64), nullable=False, unique=True),
)

# ----------------------------------------------------------------- estado actual (lo que lee la interfaz)
estado_actual = Table(
    "estado_actual", meta,
    Column("objeto", String(200), primary_key=True),      # p.ej. bd:CORPRD:bloqueos
    Column("tipo", String(40), nullable=False, index=True),
    Column("datos", JSONV, nullable=False),
    Column("origen", String(40), nullable=False),          # recoleccion | evento | refresco | ejecucion
    TS("actualizado", nullable=False),                     # último dato bueno (o primer intento si nunca hubo)
    Column("error", Text),
    TS("intentado"),                                       # último intento de recolección, exitoso o no
)

# Columnas agregadas después de la primera versión: crear_esquema las añade a tablas existentes.
COLUMNAS_NUEVAS = [("estado_actual", "intentado", "TIMESTAMP")]

refresco = Table(
    "estado_refresco", meta,
    Column("objeto", String(200), primary_key=True),
    TS("ultimo", nullable=False),
    Column("trabajo_id", BigInteger),
)

snapshot = Table(
    "snap_snapshot", meta,
    Column("id", String(32), primary_key=True),
    Column("base", String(30), nullable=False),
    Column("cambio", String(40), nullable=False),
    Column("momento", String(10), nullable=False),         # ANTES | DESPUES | FINAL
    TS("tomado", nullable=False),
    Column("datos", JSONV, nullable=False),
    Column("huella", String(64), nullable=False),
    Column("ejecucion_id", String(32)),
    Index("ix_snap_base_cambio", "base", "cambio", "momento"),
)

# ----------------------------------------------------------------- eventos de Zabbix y episodios
# Ajustes del inventario hechos desde la interfaz (alta, cambio, baja) y vínculos con Zabbix.
# Se superponen al archivo generado (config/inventario.json): regenerarlo no los borra.
inventario_ajuste = Table(
    "ops_inventario_ajuste", meta,
    Column("clave", String(200), primary_key=True),        # tipo:id, p. ej. base:ABBYY
    Column("tipo", String(20), nullable=False),
    Column("id", String(160), nullable=False),
    Column("operacion", String(10), nullable=False),       # alta | cambio | baja
    Column("datos", JSONV, nullable=False),
    Column("por", String(120), nullable=False),
    TS("actualizado", nullable=False),
)

evento_zbx = Table(
    "evento_zabbix", meta,
    Column("eventid", String(40), primary_key=True),
    Column("host", String(200), nullable=False),
    Column("activo", String(120), index=True),
    Column("tipo_activo", String(20)),
    Column("severidad", Integer, nullable=False),
    Column("nombre", Text, nullable=False),
    Column("estado", String(10), nullable=False),          # PROBLEM | RESOLVED
    TS("inicio", nullable=False),
    TS("fin"),
    Column("episodio_id", String(32), index=True),
    Column("en_mantenimiento", Boolean, nullable=False, default=False),
    Column("datos", JSONV, nullable=False),
)

episodio = Table(
    "evento_episodio", meta,
    Column("id", String(32), primary_key=True),
    TS("abierto", nullable=False), TS("ultimo_evento", nullable=False), TS("cerrado"),
    Column("activo_raiz", String(120)),
    Column("activos", JSONV, nullable=False),
    Column("titulo", Text, nullable=False),
    Column("severidad", Integer, nullable=False),
    Column("estado", String(12), nullable=False),          # abierto | cerrado
    Column("causa_probable", Text),
)

# ----------------------------------------------------------------- series y ML
serie_punto = Table(
    "serie_punto_1h", meta,
    Column("serie", String(200), primary_key=True),        # p.ej. fs:AP23SBCC04:/oracle
    TS("ts", primary_key=True),
    Column("valor", Float, nullable=False),
)

prediccion = Table(
    "ml_prediccion", meta,
    Column("id", ID_AUTO, primary_key=True, autoincrement=True),
    Column("serie", String(200), nullable=False, index=True),
    Column("activo", String(120)),
    Column("modelo", String(10), nullable=False),          # M1 | M2 | M3
    Column("version", String(20), nullable=False),
    TS("calculada", nullable=False),
    Column("datos", JSONV, nullable=False),
    Column("vigente", Boolean, nullable=False, default=True),
    Column("en_sombra", Boolean, nullable=False, default=True),
)

feedback_ml = Table(
    "ml_feedback", meta,
    Column("id", ID_AUTO, primary_key=True, autoincrement=True),
    Column("prediccion_id", BigInteger, nullable=False),
    Column("login", String(120), nullable=False),
    Column("valor", String(12), nullable=False),           # util | falsa_alarma
    TS("ts", nullable=False),
)


class Almacen:
    def __init__(self, url: str, echo: bool = False):
        kw = {}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        # Las filas de Oracle traen fechas y decimales: se serializan como texto en las columnas JSON.
        kw["json_serializer"] = lambda o: json.dumps(o, default=str, ensure_ascii=False)
        self.engine: Engine = create_engine(url, echo=echo, future=True, pool_pre_ping=True, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_con, _):
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        self.es_postgres = self.engine.dialect.name == "postgresql"

    def crear_esquema(self) -> None:
        meta.create_all(self.engine)
        from sqlalchemy import inspect, text
        insp = inspect(self.engine)
        for tabla, col, tipo in COLUMNAS_NUEVAS:
            if col not in {c["name"] for c in insp.get_columns(tabla)}:
                tipo_real = "TIMESTAMP WITH TIME ZONE" if self.es_postgres and tipo == "TIMESTAMP" else tipo
                with self.engine.begin() as con:
                    con.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo_real}"))

    @contextmanager
    def tx(self):
        with self.engine.begin() as con:
            yield con


def ddl_postgres() -> str:
    """DDL de PostgreSQL generado desde las mismas definiciones (para revisión del DBA de Postgres)."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable
    d = postgresql.dialect()
    partes = []
    for t in meta.sorted_tables:
        partes.append(str(CreateTable(t).compile(dialect=d)).strip() + ";")
        for ix in t.indexes:
            partes.append(str(CreateIndex(ix).compile(dialect=d)).strip() + ";")
    return "\n\n".join(partes) + "\n"
