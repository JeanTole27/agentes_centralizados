import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";
import { Clase, Consola, Modal, Resultado, useAccion, useSegundoFactor, useSesion } from "./comun";
import type { EspParam, Evaluacion, Ejecucion, Inventario } from "./tipos";

type Props = {
  accion: string;
  iniciales?: Record<string, unknown>;
  ocultos?: string[]; // parámetros que fija la pantalla (no se muestran)
  etiqueta?: string;
  compacto?: boolean;
  alTerminar?: (e: Ejecucion) => void;
};

const NOMBRES: Record<string, string> = {
  base: "Base", cambio: "N.º de cambio", momento: "Momento", esquema: "Esquema", usuario: "Usuario",
  sid: "SID", serial: "Serial#", inst_id: "Instancia", motivo: "Motivo", host: "Servidor", hosts: "Servidores",
  dominios: "Dominios", tablespace: "Tablespace", diskgroup: "Diskgroup", tamano_gb: "Tamaño (GB)",
  maximo_gb: "Máximo (GB)", propietario: "Propietario", objeto: "Objeto", tipo_objeto: "Tipo de objeto",
  justificacion: "Justificación", operacion: "Operación", ambiente: "Ambiente", sql_id: "SQL_ID", tabla: "Tabla",
  job: "Job", habilitar: "Habilitar",
};
const TIPOS_OBJETO = ["PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER", "VIEW", "TYPE", "TYPE BODY",
  "SYNONYM", "MATERIALIZED VIEW"];

/** Formulario genérico: se construye con el esquema de parámetros del catálogo.
 *  Flujo: evaluar → confirmar con los controles exigidos → ejecutar → consola en vivo. */
export default function Accion({ accion, iniciales = {}, ocultos = [], etiqueta, compacto, alTerminar }: Props) {
  const acc = useAccion(accion);
  const { yo, avisar } = useSesion();
  const qc = useQueryClient();
  const conSegundoFactor = useSegundoFactor();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const [valores, setValores] = useState<Record<string, unknown>>(() => ({ ...iniciales }));
  const [ev, setEv] = useState<Evaluacion | null>(null);
  const [cambio, setCambio] = useState("");
  const [motivo, setMotivo] = useState("");
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [ejec, setEjec] = useState<Ejecucion | null>(null);
  const [final, setFinal] = useState<Ejecucion | null>(null);

  if (!acc) return null;
  const v = { ...valores, ...iniciales }; // lo que fija la pantalla manda
  const puede = acc.permiso === "ejecuta" && acc.estado === "implementada";

  function params() {
    const p: Record<string, unknown> = {};
    for (const [k, esp] of Object.entries(acc!.parametros)) {
      const x = v[k];
      if (x === undefined || x === "") continue;
      p[k] = esp.tipo === "entero" ? Number(x) : x;
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
      setCambio(String(v.cambio ?? ""));
      setMotivo(String(v.motivo ?? v.justificacion ?? ""));
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
          {(ev.requiere_cambio || cambio) && !("cambio" in acc.parametros) && (
            <>
              <label className="lb mt">N.º de cambio o ticket {ev.requiere_cambio && "(obligatorio)"}</label>
              <input className="f" value={cambio} placeholder="CHG0012345" onChange={(e) => setCambio(e.target.value.toUpperCase())} />
            </>
          )}
          {(ev.clase === "C" || ev.clase === "O") && !("motivo" in acc.parametros) && !("justificacion" in acc.parametros) && (
            <>
              <label className="lb mt">Motivo {ev.clase === "C" && "(obligatorio)"}</label>
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

function Campo({ nombre, esp, valor, cambiar, inv, disabled }: {
  nombre: string; esp: EspParam; valor: unknown; cambiar: (v: unknown) => void; inv?: Inventario; disabled: boolean;
}) {
  const id = `p-${nombre}`;
  const etq = (NOMBRES[nombre] ?? nombre) + (esp.requerido ? " *" : "");
  const s = (valor ?? esp.defecto ?? "") as string;
  let control;
  if (esp.tipo === "base" && inv) {
    control = (
      <select id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        {inv.bases.map((b) => <option key={b.id} value={b.id}>{b.id} · {b.ambiente}</option>)}
      </select>
    );
  } else if (esp.tipo === "enum" || esp.tipo === "tipo_objeto") {
    const ops = esp.valores ?? TIPOS_OBJETO;
    control = (
      <select id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        {ops.map((o) => <option key={o}>{o}</option>)}
      </select>
    );
  } else if (esp.tipo === "booleano") {
    control = <input id={id} type="checkbox" checked={Boolean(valor)} disabled={disabled} onChange={(e) => cambiar(e.target.checked)} />;
  } else if (nombre === "host" && inv) {
    control = (
      <select id={id} className="f" value={s} disabled={disabled} onChange={(e) => cambiar(e.target.value)}>
        <option value="">— elegir —</option>
        {inv.servidores.map((b) => <option key={b.id} value={b.id}>{b.id} · {b.ambiente}</option>)}
      </select>
    );
  } else {
    control = (
      <input id={id} className="f" type={esp.tipo === "entero" ? "number" : "text"} value={s} disabled={disabled}
             min={esp.tipo === "entero" ? esp.min : undefined}
             onChange={(e) => cambiar(["cambio", "identificador", "sql_id"].includes(esp.tipo) ? e.target.value.toUpperCase() : e.target.value)} />
    );
  }
  return (
    <div>
      <label className="lb" htmlFor={id}>{etq}</label>
      {control}
    </div>
  );
}
