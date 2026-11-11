-- Generado desde src/opscentral/almacen.py (no editar a mano)

CREATE TABLE audit_evento (
	id BIGSERIAL NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	evento VARCHAR(60) NOT NULL, 
	actor VARCHAR(120) NOT NULL, 
	datos JSONB NOT NULL, 
	hash_prev VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (hash)
);

CREATE INDEX ix_audit_evento_actor ON audit_evento (actor);

CREATE INDEX ix_audit_evento_evento ON audit_evento (evento);

CREATE TABLE ejec_bloqueo_destino (
	destino VARCHAR(120) NOT NULL, 
	ejecucion_id VARCHAR(32) NOT NULL, 
	desde TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (destino)
);

CREATE TABLE ejec_ejecucion (
	id VARCHAR(32) NOT NULL, 
	accion VARCHAR(60) NOT NULL, 
	motor VARCHAR(20) NOT NULL, 
	clase VARCHAR(1) NOT NULL, 
	destino VARCHAR(120) NOT NULL, 
	ambiente VARCHAR(10), 
	params JSONB NOT NULL, 
	cambio VARCHAR(40), 
	motivo TEXT, 
	solicitante VARCHAR(120) NOT NULL, 
	estado VARCHAR(24) NOT NULL, 
	huella VARCHAR(64) NOT NULL, 
	token_aprobacion TEXT, 
	aprobador VARCHAR(120), 
	comentario TEXT, 
	creada TIMESTAMP WITH TIME ZONE NOT NULL, 
	actualizada TIMESTAMP WITH TIME ZONE NOT NULL, 
	iniciada TIMESTAMP WITH TIME ZONE, 
	terminada TIMESTAMP WITH TIME ZONE, 
	resultado JSONB, 
	error TEXT, 
	evidencia VARCHAR(400), 
	evidencia_sha256 VARCHAR(64), 
	origen VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_ejec_ejecucion_destino ON ejec_ejecucion (destino);

CREATE INDEX ix_ejec_ejecucion_solicitante ON ejec_ejecucion (solicitante);

CREATE INDEX ix_ejec_ejecucion_accion ON ejec_ejecucion (accion);

CREATE INDEX ix_ejec_ejecucion_cambio ON ejec_ejecucion (cambio);

CREATE INDEX ix_ejec_ejecucion_estado ON ejec_ejecucion (estado);

CREATE TABLE ejec_linea (
	id BIGSERIAL NOT NULL, 
	ejecucion_id VARCHAR(32) NOT NULL, 
	n INTEGER NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	texto TEXT NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (ejecucion_id, n)
);

CREATE TABLE estado_actual (
	objeto VARCHAR(200) NOT NULL, 
	tipo VARCHAR(40) NOT NULL, 
	datos JSONB NOT NULL, 
	origen VARCHAR(40) NOT NULL, 
	actualizado TIMESTAMP WITH TIME ZONE NOT NULL, 
	error TEXT, 
	intentado TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (objeto)
);

CREATE INDEX ix_estado_actual_tipo ON estado_actual (tipo);

CREATE TABLE estado_refresco (
	objeto VARCHAR(200) NOT NULL, 
	ultimo TIMESTAMP WITH TIME ZONE NOT NULL, 
	trabajo_id BIGINT, 
	PRIMARY KEY (objeto)
);

CREATE TABLE evento_episodio (
	id VARCHAR(32) NOT NULL, 
	abierto TIMESTAMP WITH TIME ZONE NOT NULL, 
	ultimo_evento TIMESTAMP WITH TIME ZONE NOT NULL, 
	cerrado TIMESTAMP WITH TIME ZONE, 
	activo_raiz VARCHAR(120), 
	activos JSONB NOT NULL, 
	titulo TEXT NOT NULL, 
	severidad INTEGER NOT NULL, 
	estado VARCHAR(12) NOT NULL, 
	causa_probable TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE evento_zabbix (
	eventid VARCHAR(40) NOT NULL, 
	host VARCHAR(200) NOT NULL, 
	activo VARCHAR(120), 
	tipo_activo VARCHAR(20), 
	severidad INTEGER NOT NULL, 
	nombre TEXT NOT NULL, 
	estado VARCHAR(10) NOT NULL, 
	inicio TIMESTAMP WITH TIME ZONE NOT NULL, 
	fin TIMESTAMP WITH TIME ZONE, 
	episodio_id VARCHAR(32), 
	en_mantenimiento BOOLEAN NOT NULL, 
	datos JSONB NOT NULL, 
	PRIMARY KEY (eventid)
);

CREATE INDEX ix_evento_zabbix_activo ON evento_zabbix (activo);

CREATE INDEX ix_evento_zabbix_episodio_id ON evento_zabbix (episodio_id);

CREATE TABLE ml_feedback (
	id BIGSERIAL NOT NULL, 
	prediccion_id BIGINT NOT NULL, 
	login VARCHAR(120) NOT NULL, 
	valor VARCHAR(12) NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE ml_prediccion (
	id BIGSERIAL NOT NULL, 
	serie VARCHAR(200) NOT NULL, 
	activo VARCHAR(120), 
	modelo VARCHAR(10) NOT NULL, 
	version VARCHAR(20) NOT NULL, 
	calculada TIMESTAMP WITH TIME ZONE NOT NULL, 
	datos JSONB NOT NULL, 
	vigente BOOLEAN NOT NULL, 
	en_sombra BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_ml_prediccion_serie ON ml_prediccion (serie);

CREATE TABLE ops_intento_ingreso (
	login VARCHAR(120) NOT NULL, 
	fallidos INTEGER NOT NULL, 
	bloqueado_hasta TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (login)
);

CREATE TABLE ops_sesion (
	huella VARCHAR(64) NOT NULL, 
	login VARCHAR(120) NOT NULL, 
	nombre VARCHAR(200), 
	perfiles JSONB NOT NULL, 
	grupos JSONB NOT NULL, 
	ip VARCHAR(64), 
	creada TIMESTAMP WITH TIME ZONE NOT NULL, 
	ultima_actividad TIMESTAMP WITH TIME ZONE NOT NULL, 
	vence TIMESTAMP WITH TIME ZONE NOT NULL, 
	mfa_en TIMESTAMP WITH TIME ZONE, 
	cerrada BOOLEAN NOT NULL, 
	PRIMARY KEY (huella)
);

CREATE INDEX ix_ops_sesion_login ON ops_sesion (login);

CREATE TABLE ops_trabajo (
	id BIGSERIAL NOT NULL, 
	cola VARCHAR(20) NOT NULL, 
	tipo VARCHAR(60) NOT NULL, 
	payload JSONB NOT NULL, 
	estado VARCHAR(12) NOT NULL, 
	intentos INTEGER NOT NULL, 
	max_intentos INTEGER NOT NULL, 
	ejecutar_desde TIMESTAMP WITH TIME ZONE NOT NULL, 
	tomado_por VARCHAR(80), 
	tomado_en TIMESTAMP WITH TIME ZONE, 
	clave_unica VARCHAR(200), 
	error TEXT, 
	creado TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_trabajo_clave ON ops_trabajo (clave_unica, estado);

CREATE INDEX ix_trabajo_cola_estado ON ops_trabajo (cola, estado, ejecutar_desde);

CREATE TABLE ops_usuario_demo (
	login VARCHAR(60) NOT NULL, 
	nombre VARCHAR(200) NOT NULL, 
	perfil VARCHAR(10) NOT NULL, 
	creado TIMESTAMP WITH TIME ZONE NOT NULL, 
	ultimo_ingreso TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (login)
);

CREATE TABLE serie_punto_1h (
	serie VARCHAR(200) NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE NOT NULL, 
	valor FLOAT NOT NULL, 
	PRIMARY KEY (serie, ts)
);

CREATE TABLE snap_snapshot (
	id VARCHAR(32) NOT NULL, 
	base VARCHAR(30) NOT NULL, 
	cambio VARCHAR(40) NOT NULL, 
	momento VARCHAR(10) NOT NULL, 
	tomado TIMESTAMP WITH TIME ZONE NOT NULL, 
	datos JSONB NOT NULL, 
	huella VARCHAR(64) NOT NULL, 
	ejecucion_id VARCHAR(32), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_snap_base_cambio ON snap_snapshot (base, cambio, momento);
