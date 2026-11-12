import { useState } from "react";
import { SelectBuscable } from "./SelectBuscable";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";
import { CampoAmbiente, Clase, Consola, Modal, OpcionesActivo, Resultado, useAccion, useSegundoFactor, useSesion } from "./comun";
import type { Caso, EspParam, Evaluacion, Ejecucion, Inventario } from "./tipos";

type Props = {
  accion: string;
  iniciales?: Record<string, unknown>;
  ocultos?: string[]; // parámetros que fija la pantalla (no se muestran)
  etiqueta?: string;
  compacto?: boolean;
  alTerminar?: (e: Ejecucion) => void;
};

const NOMBRES: Record<string, string> = {
  base: "Base", cambio: "Referencia", momento: "Momento", esquema: "Esquema", usuario: "Usuario",
  sid: "SID", serial: "Serial#", inst_id: "Instancia", motivo: "Motivo", host: "Servidor", hosts: "Servidores",
  dominios: "Dominios", tablespace: "Tablespace", diskgroup: "Diskgroup", tamano_gb: "Tamaño (GB)",
  maximo_gb: "Máximo (GB)", propietario: "Propietario", objeto: "Objeto", tipo_objeto: "Tipo de objeto",
  justificacion: "Justificación", operacion: "Operación", ambiente: "Ambiente", sql_id: "SQL_ID", tabla: "Tabla",
  job: "Job", habilitar: "Habilitar",
};
const TIPOS_OBJETO = ["PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "VIEW", "TYPE", "TYPE BODY",
  "SYNONYM", "MATERIALIZED VIEW"];

const RE_REFERENCIA = /^[A-Za-z0-9][A-Za-z0-9_\-/.]{2,39}$/;
const RE_CLAVE_JIRA = /^[A-Z][A-Z0-9_]{1,19}-[0-9]{1,9}$/;

/** Formulario genérico: se construye con el esquema de parámetros del catálogo.
 *  Flujo: (caso opcional de Jira) → evaluar → confirmar → ejecutar directo → consola en vivo.
 *  Cada ejecución queda en el log de ejecuciones con su evidencia. */
export default function Accion({ accion, iniciales = {}, ocultos = [], etiqueta, compacto, alTerminar }: Props) {
  const acc = useAccion(accion);
  const { yo, avisar } = useSesion();
  const qc = useQueryClient();
  const conSegundoFactor = useSegundoFactor();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const salud = useQuery({ queryKey: ["salud"], queryFn: () => api.get<{ jira?: boolean }>("/api/salud"), staleTime: 300_000 });
  const [valores, setValores] = useState<Record<string, unknown>>(() => ({ ...iniciales }));
  const [ev, setEv] = useState<Evaluacion | null>(null);
  const [cambio, setCambio] = useState("");
  const [motivo, setMotivo] = useState("");
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [ejec, setEjec] = useState<Ejecucion | null>(null);
  const [final, setFinal] = useState<Ejecucion | null>(null);
  const [caso, setCaso] = useState<Caso | null>(null);
  const [errorCaso, setErrorCaso] = useState("");
  const [buscandoCaso, setBuscandoCaso] = useState(false);
  const [abrirCaso, setAbrirCaso] = useState(false);

  if (!acc) return null;
  const v = { ...valores, ...iniciales }; // lo que fija la pantalla manda
  const puede = acc.permiso === "ejecuta" && acc.estado === "implementada";
  // La referencia del caso va en el parámetro 'cambio' si la acción lo declara (snapshots); si no, aparte.
  const refEnParams = "cambio" in acc.parametros;
  const referencia = refEnParams ? String(v.cambio ?? "") : cambio;
  const ponerReferencia = (x: string) => (refEnParams ? setValores((p) => ({ ...p, cambio: x })) : setCambio(x));
  const verCaso = puede && !("cambio" in iniciales);

  async function traerCaso() {
    const clave = referencia.trim().toUpperCase();
    setErrorCaso("");
    if (!RE_CLAVE_JIRA.test(clave)) {
      setErrorCaso("Escribe una clave de Jira (PROYECTO-123) para traer el caso.");
      return;
    }
    setBuscandoCaso(true);
    try {
      const c = await api.get<Caso>(`/api/casos/${encodeURIComponent(clave)}?accion=${encodeURIComponent(accion)}`);
      setCaso(c);
      ponerReferencia(c.clave);
      // Completa solo lo que está vacío: lo que escribió la persona o fija la pantalla manda.
      setValores((p) => {
        const n = { ...p };
        for (const [k, x] of Object.entries(c.sugeridos)) {
          if (k === "cambio" || !(k in acc!.parametros) || k in iniciales) continue;
          if (n[k] === undefined || n[k] === "") n[k] = x;
        }
        return n;
      });
      const m = c.sugeridos.motivo ?? c.sugeridos.justificacion;
      if (m && !motivo) setMotivo(String(m));
    } catch (e) {
      setCaso(null);
      setErrorCaso(mensajeError(e));
    } finally {
      setBuscandoCaso(false);
    }
  }

  function params() {
    const p: Record<string, unknown> = {};
    for (const [k, esp] of Object.entries(acc!.parametros)) {
      const x = v[k];
      if (x === undefined || x === "") continue;
      p[k] = esp.tipo === "entero" ? Number(x) : x;
    }
    // Lo que fija la pantalla y no está declarado en el catálogo (acciones con parámetros libres,
    // p. ej. instalación o dominio de Capa Media) también viaja.
    if (acc!.parametros_libres) {
      for (const [k, x] of Object.entries(iniciales)) {
        if (!(k in acc!.parametros) && x !== undefined && x !== "" && !(Array.isArray(x) && x.length === 0)) p[k] = x;
      }
    }
    return p;
  }

  async function revisar() {
    setError("");
    setOcupado(true);
    try {
      const r = await api.post<Evaluacion>(`/api/acciones/${accion}/evaluar`, { params: params() });
      if (!r.permitido) {
        setError(r.motivo);
        return;
      }
      if (refEnParams) setCambio(String(v.cambio ?? ""));
      if (!motivo) setMotivo(String(v.motivo ?? v.justificacion ?? ""));
      setEv(r);
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setOcupado(false);
    }
  }

  async function ejecutar(): Promise<void> {
    setError("");
    setOcupado(true);
    try {
      const r = await conSegundoFactor(() => api.post<Ejecucion>(`/api/acciones/${accion}/ejecuciones`, {
        params: params(), cambio: cambio || null, motivo: motivo || null,
      }));
      if (!r) return;
      setEv(null);
      setFinal(null);
      setEjec(r);
      qc.invalidateQueries({ queryKey: ["inicio"] });
      qc.invalidateQueries({ queryKey: ["ejecuciones"] });
      if (r.estado === "pendiente_aprobacion") avisar("Solicitud enviada: queda pendiente de aprobación.");
      else avisar(`Ejecución ${r.id} en curso: queda registrada en el log de ejecuciones.`);
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setOcupado(false);
    }
  }

  async function terminado() {
    if (!ejec) return;
    const e = await api.get<Ejecucion>(`/api/ejecuciones/${ejec.id}`);
    setFinal(e);
    qc.invalidateQueries();
    alTerminar?.(e);
  }

  const visibles = Object.entries(acc.parametros).filter(([k]) => !ocultos.includes(k) && !(k in iniciales));
  const claseEf = ev?.clase ?? acc.clase;

  return (
    <div className={compacto ? "" : "card"}>
      {!compacto && (
        <div className="fila entre" style={{ marginBottom: 10 }}>
          <div>
            <b>{acc.nombre}</b>
            <div className="muted small mono-sm">{acc.id}</div>
          </div>
          <div className="fila">
            <Clase c={acc.clase} />
            {acc.clase_prod && acc.clase_prod !== acc.clase && <span className="tag t-crit">PROD: {acc.clase_prod}</span>}
          </div>
        </div>
      )}
      {visibles.length > 0 && (
        <div className="fgrid">
          {visibles.map(([k, esp]) => (
            <Campo key={k} nombre={k} esp={esp} valor={v[k]} inv={inv.data} disabled={!puede}
                   cambiar={(x) => setValores((p) => ({ ...p, [k]: x }))} />
          ))}
        </div>
      )}
      {verCaso && !abrirCaso && !referencia && (
        <div className="mt">
          <button type="button" className="btn ghost sm" onClick={() => setAbrirCaso(true)}>+ Asociar un caso (opcional)</button>
        </div>
      )}
      {verCaso && (abrirCaso || referencia) && (
        <div className="mt">
          <label className="lb" htmlFor={`caso-${accion}`}>Caso asociado (opcional: Jira, incidente, cambio, tarea…)</label>
          <div className="fila">
            <input id={`caso-${accion}`} className="f" style={{ flex: 1, minWidth: 180 }} value={referencia} maxLength={40}
                   placeholder="OPS-123 o cualquier referencia" onChange={(e) => { ponerReferencia(e.target.value.toUpperCase().trim()); setCaso(null); }} />
            {salud.data?.jira && (
              <button type="button" className="btn ghost" disabled={buscandoCaso || !referencia} onClick={traerCaso}>
                {buscandoCaso ? "Consultando…" : "Traer de Jira"}
              </button>
            )}
          </div>
          {salud.data && !salud.data.jira && referencia && (
            <div className="muted small mt">Jira no está configurado: la referencia queda registrada como texto libre.</div>
          )}
          {referencia && !RE_REFERENCIA.test(referencia) && <div className="err-campo">3 a 40 caracteres: letras, números, - _ . /</div>}
          {errorCaso && <div className="err-campo">{errorCaso}</div>}
          {caso && <TarjetaCaso c={caso} />}
        </div>
      )}
      {acc.estado !== "implementada" && <div className="info mt">Catalogada: se implementa en una etapa posterior.</div>}
      {acc.permiso === "ve" && <div className="info mt">Tu perfil puede ver esta acción, pero no ejecutarla.</div>}
      {error && <div className="critbox mt">{error}</div>}
      {puede && (
        <div className="btns mt">
          <button className={"btn " + (claseEf === "C" ? "red" : "pri")} disabled={ocupado} onClick={revisar}>
            {etiqueta ?? "Ejecutar"}
          </button>
        </div>
      )}

      {ejec && (
        <div className="mt">
          <div className="fila entre small" style={{ marginBottom: 6 }}>
            <span>Ejecución <span className="mono-sm">{ejec.id}</span> en <b>{ejec.destino}</b> ({ejec.ambiente})</span>
            {ejec.estado === "pendiente_aprobacion" && <span className="tag t-warn">pendiente de aprobación</span>}
          </div>
          {ejec.estado !== "pendiente_aprobacion" && <Consola id={ejec.id} alTerminar={terminado} />}
          {final?.error && <div className="critbox mt">{final.error}</div>}
          {final?.resultado && <div className="mt"><Resultado r={final.resultado} /></div>}
        </div>
      )}

      {ev && (
        <Modal titulo={`Confirmar: ${acc.nombre}`} cerrar={() => setEv(null)}
               pie={<>
                 <button className="btn ghost" onClick={() => setEv(null)}>Volver</button>
                 <button className={"btn " + (ev.clase === "C" ? "red" : "pri")} disabled={ocupado} onClick={() => ejecutar()}>
                   {ev.requiere_aprobacion ? "Enviar a aprobación" : "Confirmar y ejecutar"}
                 </button>
               </>}>
          <div className="kv mt">
            <span>Destino</span><b>{ev.destino}</b>
            <span>Ambiente</span><b>{ev.ambiente}</b>
            <span>Clase efectiva</span><b><Clase c={ev.clase} /></b>
            <span>Quién</span><b>{yo.nombre} ({yo.login})</b>
          </div>
          {ev.controles.length > 0 && (
            <>
              <p className="small muted mt">Controles exigidos:</p>
              <div className="ctl">{ev.controles.map((c) => <span key={c} className="tag t-acc">{c}</span>)}</div>
            </>
          )}
          {ev.requiere_aprobacion && (
            <div className="warnbox mt">Requiere la aprobación de otra persona habilitada. La ejecución queda en espera
              hasta entonces y verifica que no hayas cambiado nada respecto a lo aprobado.</div>
          )}
          {ev.requiere_segundo_factor && !yo.mfa_reciente && (
            <div className="warnbox mt">Se pedirá segundo factor al confirmar.</div>
          )}
          {!ev.requiere_aprobacion && (
            <div className="info mt">Ejecución directa con tu permiso de ejecución: queda registrada en el log de
              ejecuciones con su evidencia.</div>
          )}
          {!refEnParams && (
            <>
              <label className="lb mt">Caso asociado {ev.requiere_cambio ? "(lo exige la configuración de este ambiente)" : "(opcional)"}</label>
              <input className="f" value={cambio} maxLength={40} placeholder="OPS-123 o cualquier referencia"
                     onChange={(e) => setCambio(e.target.value.toUpperCase().trim())} />
            </>
          )}
          {caso && <TarjetaCaso c={caso} />}
          {(ev.clase === "C" || ev.clase === "O") && !("motivo" in acc.parametros) && !("justificacion" in acc.parametros) && (
            <>
              <label className="lb mt">Motivo {ev.controles.includes("motivo obligatorio") ? "(obligatorio)" : "(opcional)"}</label>
              <input className="f" value={motivo} onChange={(e) => setMotivo(e.target.value)} />
            </>
          )}
          <p className="small muted mt">Parámetros enviados:</p>
          <pre className="console json">{JSON.stringify(params(), null, 2)}</pre>
          {error && <div className="critbox mt">{error}</div>}
        </Modal>
      )}
    </div>
  );
}

function TarjetaCaso({ c }: { c: Caso }) {
  return (
    <div className="caso-box">
      <div className="fila entre">
        <b>{c.clave} · {c.resumen}</b>
        <a className="small" href={c.url} target="_blank" rel="noreferrer">Abrir en Jira ↗</a>
      </div>
      <div className="kv small">
        <span>Tipo / estado</span><b>{c.tipo || "—"} · {c.estado || "—"}</b>
        <span>Prioridad</span><b>{c.prioridad || "—"}</b>
        <span>Asignado</span><b>{c.asignado}</b>
        {Object.keys(c.sugeridos).filter((k) => k !== "cambio").length > 0 && (
          <><span>Completado</span><b>{Object.entries(c.sugeridos).filter(([k]) => k !== "cambio" && k !== "justificacion")
            .map(([k, x]) => `${NOMBRES[k] ?? k}: ${String(x)}`).join(" · ")}</b></>
        )}
      </div>
      {c.descripcion && <div className="caso-desc muted">{c.descripcion}</div>}
    </div>
  );
}

function Campo({ nombre, esp, valor, cambiar, inv, disabled }: {
  nombre: string; esp: EspParam; valor: unknown; cambiar: (v: unknown) => void; inv?: Inventario; disabled: boolean;
}) {
  const id = `p-${nombre}`;
  const etq = (NOMBRES[nombre] ?? nombre) + (esp.requerido ? " *" : "");
  const s = (valor ?? esp.defecto ?? "") as string;
  let control;
  if (esp.tipo === "base" && inv) {
    control = (
      <SelectBuscable id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        <OpcionesActivo items={inv.bases} />
      </SelectBuscable>
    );
  } else if (esp.tipo === "enum" || esp.tipo === "tipo_objeto") {
    const ops = esp.valores ?? TIPOS_OBJETO;
    control = (
      <SelectBuscable id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        {ops.map((o) => <option key={o}>{o}</option>)}
      </SelectBuscable>
    );
  } else if (esp.tipo === "booleano") {
    control = <input id={id} type="checkbox" checked={Boolean(valor)} disabled={disabled} onChange={(e) => cambiar(e.target.checked)} />;
  } else if (nombre === "host" && inv) {
    control = (
      <SelectBuscable id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        <OpcionesActivo items={inv.servidores} />
      </SelectBuscable>
    );
  } else {
    control = (
      <input id={id} className="f" type={esp.tipo === "entero" ? "number" : "text"} value={s} disabled={disabled}
             min={esp.tipo === "entero" ? esp.min : undefined}
             onChange={(e) => cambiar(["cambio", "identificador", "sql_id"].includes(esp.tipo) ? e.target.value.toUpperCase() : e.target.value)} />
    );
  }
  const campo = (
    <div>
      <label className="lb" htmlFor={id}>{etq}</label>
      {control}
    </div>
  );
  // Base y servidor llevan al lado el selector de ambiente (filtro global): primero el ambiente, luego el nombre.
  if (inv && !disabled && (esp.tipo === "base" || nombre === "host")) {
    return (
      <>
        <CampoAmbiente id={`${id}-amb`} items={esp.tipo === "base" ? inv.bases : inv.servidores} alCambiar={() => cambiar("")} />
        {campo}
      </>
    );
  }
  return campo;
}
