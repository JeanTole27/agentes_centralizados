// ======================================================================== INCIDENTES Y CAMBIOS (com.itsm.*)
// Diseño: demo-ops-central.html (V.incidentes). Tickets de Jira de ambos equipos y, siempre, las referencias que
// ya usa Ops Central (cada número de cambio o incidente con sus ejecuciones y ventanas). El detalle junta el
// ticket, la línea de tiempo de ejecuciones con evidencia, las ventanas de cambio, los activos afectados con su
// semáforo, las alertas abiertas de Zabbix sobre esos activos y casos parecidos.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, mensajeError } from "./api";
import { Transiciones } from "./Jira";
import { Cabecera, Cargando, EstadoEj, Vacio, fecha, useCatalogo, useSesion } from "./comun";
import type { Ejecucion } from "./tipos";

type CasoJira = { clave: string; resumen: string; estado: string; tipo: string; prioridad: string; asignado: string; actualizado: string; url: string };
type Referencia = { clave: string; titulo: string; ejecuciones: number; fallidas: number; ventanas: number; ambiente: string | null; ultima: string; equipos: string[] };
type Lista = { jira: boolean; jira_error: string | null; casos: CasoJira[]; referencias: Referencia[] };
type Detalle = {
  clave: string; jira: boolean; jira_error: string | null;
  caso: (CasoJira & { descripcion: string; informante: string; creado: string; etiquetas: string[]; componentes: string[] }) | null;
  ejecuciones: Ejecucion[];
  ventanas: { id: string; titulo: string; estado: string; hechos: number; total_pasos: number; creada: string }[];
  activos: { id: string; tipo: string; ambiente: string; semaforo?: string; motivos?: string[] }[];
  episodios: { id: number; titulo: string; activo_raiz: string; severidad: number }[];
  similares: CasoJira[];
};

const SEM: Record<string, string> = { verde: "t-ok", amarillo: "t-warn", rojo: "t-crit" };

function tagTipo(tipo: string, prioridad: string) {
  const t = tipo.toLowerCase();
  const crit = /highest|critical|p1|p2|alta|blocker/i.test(prioridad);
  return <span className={"tag " + (t.includes("incid") || t.includes("bug") ? (crit ? "t-crit" : "t-warn") : "t-acc")}>{tipo || "—"}{prioridad ? ` · ${prioridad}` : ""}</span>;
}

export function Incidentes() {
  const [texto, setTexto] = useState("");
  const [buscar, setBuscar] = useState("");
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.search).get("clave"));
  const q = useQuery({ queryKey: ["incidentes", buscar], queryFn: () => api.get<Lista>(`/api/incidentes${buscar ? "?q=" + encodeURIComponent(buscar) : ""}`), refetchInterval: 60_000 });
  const d = q.data;
  const primera = d?.casos[0]?.clave ?? d?.referencias[0]?.clave ?? null;
  const actual = sel ?? primera;

  return (
    <>
      <Cabecera titulo="Incidentes y cambios"
        sub="Tickets de Jira de ambos equipos con lo que Ops Central sabe de cada uno: ejecuciones con evidencia, ventanas de cambio, activos afectados y alertas." />
      <div className="card">
        <form className="fila" style={{ gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); setBuscar(texto.trim()); }}>
          <input className="f" style={{ flex: 1, minWidth: 220, maxWidth: 420 }} value={texto} maxLength={100}
                 placeholder={d?.jira ? "Clave (OPS-123) o texto a buscar en Jira" : "Número de cambio o incidente"} onChange={(e) => setTexto(e.target.value)} />
          <button className="btn">Buscar</button>
          {buscar && <button type="button" className="btn ghost" onClick={() => { setTexto(""); setBuscar(""); }}>Limpiar</button>}
        </form>
        {d && !d.jira && <p className="muted small" style={{ marginBottom: 0 }}>Jira no está configurado ([jira] url): se muestran las referencias que ya usan las ejecuciones y ventanas de Ops Central.</p>}
        {d?.jira_error && <div className="warnbox mt">Jira no respondió: {d.jira_error}. Se muestran las referencias de Ops Central.</div>}
      </div>
      <Cargando q={q} />
      {d && (
        <div className="g21">
          <div>
            {d.jira && (
              <div className="card">
                <h3>Tickets de Jira <span className="sub">{buscar ? `“${buscar}”` : "abiertos"} · {d.casos.length}</span></h3>
                {d.casos.length === 0 ? <Vacio>Sin tickets{buscar ? " que coincidan" : " abiertos"}.</Vacio> : (
                  <div className="tbl-wrap"><table className="tbl-std compacta">
                    <thead><tr><th>Clave</th><th>Tipo</th><th>Título</th><th>Asignado</th><th>Estado</th></tr></thead>
                    <tbody>{d.casos.map((c) => (
                      <tr key={c.clave} className={"click" + (actual === c.clave ? " sel" : "")} onClick={() => setSel(c.clave)}>
                        <td className="mono-sm"><b>{c.clave}</b></td><td>{tagTipo(c.tipo, c.prioridad)}</td><td>{c.resumen}</td>
                        <td className="small">{c.asignado}</td><td className="small">{c.estado}</td>
                      </tr>))}</tbody>
                  </table></div>
                )}
              </div>
            )}
            <div className="card">
              <h3>Referencias en Ops Central <span className="sub">cambios e incidentes con ejecuciones o ventanas · {d.referencias.length}</span></h3>
              {d.referencias.length === 0 ? <Vacio>Ninguna ejecución ni ventana tiene todavía un número de caso asociado.</Vacio> : (
                <div className="tbl-wrap"><table className="tbl-std compacta">
                  <thead><tr><th>Referencia</th><th>Ambiente</th><th>Equipos</th><th className="num">Ejecuciones</th><th className="num">Ventanas</th><th>Última actividad</th></tr></thead>
                  <tbody>{d.referencias.map((r) => (
                    <tr key={r.clave} className={"click" + (actual === r.clave ? " sel" : "")} onClick={() => setSel(r.clave)}>
                      <td className="mono-sm"><b>{r.clave}</b>{r.titulo && <div className="muted small">{r.titulo}</div>}</td>
                      <td>{r.ambiente ?? "—"}</td><td className="small">{r.equipos.join(", ") || "—"}</td>
                      <td className="num">{r.ejecuciones}{r.fallidas > 0 && <span className="tag t-crit" style={{ marginLeft: 4 }}>{r.fallidas} fallida(s)</span>}</td>
                      <td className="num">{r.ventanas || "—"}</td><td className="small">{fecha(r.ultima)}</td>
                    </tr>))}</tbody>
                </table></div>
              )}
            </div>
          </div>
          <div>{actual ? <DetalleCaso key={actual} clave={actual} /> : <div className="card"><Vacio>Elige un ticket o una referencia.</Vacio></div>}</div>
        </div>
      )}
    </>
  );
}

function DetalleCaso({ clave }: { clave: string }) {
  const nav = useNavigate();
  const cat = useCatalogo();
  const { avisar } = useSesion();
  const q = useQuery({ queryKey: ["incidente", clave], queryFn: () => api.get<Detalle>(`/api/incidentes/${encodeURIComponent(clave)}`), retry: false });
  const [nota, setNota] = useState("");
  const [enviando, setEnviando] = useState(false);
  const puedeComentar = cat.data?.find((a) => a.id === "com.itsm.comentar")?.permiso === "ejecuta";
  const puedeTransicionar = cat.data?.find((a) => a.id === "com.itsm.transicion")?.permiso === "ejecuta";
  if (q.isError) return <div className="card"><Vacio>{mensajeError(q.error)}</Vacio></div>;
  const d = q.data;
  if (!d) return <Cargando q={q} />;
  const c = d.caso;
  const ejs = [...d.ejecuciones].sort((a, b) => String(a.creada).localeCompare(String(b.creada)));

  async function comentar() {
    setEnviando(true);
    try { await api.post(`/api/casos/${d!.clave}/nota`, { texto: nota }); avisar(`Comentario publicado en ${d!.clave}.`); setNota(""); }
    catch (e) { avisar(mensajeError(e)); }
    finally { setEnviando(false); }
  }

  return (
    <>
      <div className="card">
        <h3><span className="mono">{d.clave}</span> {c && <span className="tag">{c.estado}</span>}
          {c && <a className="sub" href={c.url} target="_blank" rel="noreferrer">abrir en Jira ↗</a>}</h3>
        {c ? (
          <>
            <p style={{ marginTop: 0 }}><b>{c.resumen}</b></p>
            <div className="fila small" style={{ gap: 6, flexWrap: "wrap" }}>{tagTipo(c.tipo, c.prioridad)}<span className="muted">asignado a {c.asignado} · informó {c.informante} · {fecha(c.creado)}</span></div>
            {c.descripcion && <p className="muted small mt" style={{ whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>{c.descripcion}</p>}
          </>
        ) : <p className="muted small" style={{ marginTop: 0 }}>{d.jira ? (d.jira_error ? `Sin datos de Jira: ${d.jira_error}` : "No es una clave de Jira: se muestra lo registrado en Ops Central.") : "Referencia de Ops Central (Jira no configurado)."}</p>}
        {d.activos.length > 0 && (
          <div className="mt small"><b>Activos afectados:</b>{" "}
            {d.activos.map((a) => <span key={a.id} className={"tag " + (SEM[a.semaforo ?? ""] ?? "")} title={(a.motivos ?? []).join("; ") || `${a.tipo} · ${a.ambiente}`} style={{ marginRight: 4 }}>{a.id}{a.semaforo ? ` · ${a.semaforo}` : ""}</span>)}
          </div>
        )}
        {c && (puedeComentar || puedeTransicionar) && (
          <div className="mt">
            {puedeComentar && (
              <div className="fila" style={{ gap: 6, flexWrap: "wrap" }}>
                <input className="f" style={{ flex: 1, minWidth: 200 }} placeholder="Comentario para el caso…" value={nota} maxLength={4000} onChange={(e) => setNota(e.target.value)} />
                <button className="btn sm" disabled={enviando || nota.trim().length < 3} onClick={comentar}>Comentar en Jira</button>
              </div>
            )}
            {puedeTransicionar && <div className="mt"><Transiciones clave={d.clave} /></div>}
          </div>
        )}
      </div>

      {d.episodios.length > 0 && (
        <div className="card">
          <h3>Alertas abiertas en esos activos <span className="sub">Zabbix</span></h3>
          {d.episodios.map((e) => (
            <div key={e.id} className="li"><span className={"dot d-" + (e.severidad >= 4 ? "crit" : "warn")} />
              <div className="grow small"><b>{e.titulo}</b><div className="muted">{e.activo_raiz}</div></div>
              <button className="btn sm" onClick={() => nav("/alertas")}>Ver</button></div>))}
        </div>
      )}

      {d.ventanas.length > 0 && (
        <div className="card">
          <h3>Ventanas de cambio</h3>
          {d.ventanas.map((v) => (
            <div key={v.id} className="li"><div className="grow small"><b>{v.titulo}</b>
              <div className="muted">{v.id} · {v.hechos} de {v.total_pasos} pasos · {v.estado.replace("_", " ")} · {fecha(v.creada)}</div>
              <div className="bar mt"><i style={{ width: `${(v.hechos / Math.max(v.total_pasos, 1)) * 100}%` }} /></div></div>
              <button className="btn sm" onClick={() => nav("/ventana")}>Abrir</button></div>))}
        </div>
      )}

      <div className="card">
        <h3>Línea de tiempo en Ops Central <span className="sub">{ejs.length} ejecución(es) con esta referencia</span></h3>
        {ejs.length === 0 ? <Vacio>Todavía no hay ejecuciones con la referencia {d.clave}. Al ejecutar una acción, asocia este caso para que aparezca aquí.</Vacio> : (
          <div className="list">
            {ejs.map((e) => (
              <button key={e.id} className="li li-btn" onClick={() => nav("/ejecuciones?id=" + e.id)} title="Ver salida y evidencia">
                <div className="grow"><b className="mono-sm">{e.accion}</b>
                  <div className="muted small">{e.destino} · {e.ambiente} · {e.solicitante} · {fecha(e.creada)}{e.evidencia_sha256 ? " · con evidencia" : ""}</div></div>
                <EstadoEj e={e.estado} />
              </button>))}
          </div>
        )}
      </div>

      {d.jira && (
        <div className="card">
          <h3>Casos parecidos <span className="sub">búsqueda por texto en Jira</span></h3>
          {d.similares.length === 0 ? <Vacio>Sin casos parecidos.</Vacio> : d.similares.map((s) => (
            <div key={s.clave} className="li"><div className="grow small"><b className="mono">{s.clave}</b> · {s.resumen}
              <div className="muted">{s.estado} · {s.asignado} · {s.actualizado}</div></div>
              <a className="btn sm" href={s.url} target="_blank" rel="noreferrer">Abrir ↗</a></div>))}
        </div>
      )}
    </>
  );
}
