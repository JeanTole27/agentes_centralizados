// ======================================================================== VENTANA DE CAMBIO (com.vc.*)
// Diseño: demo-ops-central.html (V.ventana). Pasos de Capa Media y Base de Datos en orden, con responsable
// por equipo, encadenados por el número de cambio. Cada paso de acción se ejecuta con su formulario normal
// (queda en el log con la referencia del cambio) y el paso se completa al terminar con éxito; los manuales se
// marcan con una nota. Al cerrar se consolida el resumen (y se comenta en Jira si el cambio es una clave).
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, mensajeError } from "./api";
import Accion from "./Accion";
import { SelectBuscable } from "./SelectBuscable";
import { Cabecera, Cargando, EstadoEj, Modal, OpcionesActivo, Vacio, fecha, useCatalogo, useInventario, useSesion } from "./comun";
import type { Ejecucion } from "./tipos";

type Paso = {
  orden: number; equipo: "BD" | "CM" | "AMBOS"; titulo: string; accion: string; params: Record<string, unknown>;
  nota: string | null; estado: "pendiente" | "hecho" | "omitido"; ejecucion_id: string | null; hecho_por: string | null;
  hecho_en: string | null; comentario: string | null; actual: boolean;
  ejecucion?: { id: string; estado: string; evidencia_sha256: string | null } | null;
};
type VentanaT = {
  id: string; cambio: string; titulo: string; plantilla: string; ambiente: string | null; base: string | null;
  hosts: string | null; dominios: string | null; estado: string; creada_por: string; creada: string;
  cerrada: string | null; cerrada_por: string | null; resumen: string | null; total_pasos?: number; hechos: number;
  pasos?: Paso[]; paso_actual?: number | null;
};
type Plantilla = { id: string; nombre: string; descripcion: string; requiere: string[]; pasos: { equipo: string; titulo: string; accion: string }[] };

const EQUIPO: Record<string, string> = { BD: "Base de Datos", CM: "Capa Media", AMBOS: "Ambos" };
const ESTADO_V: Record<string, [string, string]> = {
  en_curso: ["En curso", "t-warn"], cerrada: ["Cerrada", "t-ok"], cerrada_incompleta: ["Cerrada incompleta", "t-crit"],
};

function TagEquipo({ e }: { e: string }) {
  return <span className={"tag " + (e === "BD" ? "t-acc" : e === "CM" ? "t-warn" : "")}>{EQUIPO[e] ?? e}</span>;
}

/** ¿Puede esta persona operar un paso de ese equipo? (lo mismo valida la API) */
function useOperaEquipo() {
  const { yo } = useSesion();
  const p = new Set(yo.perfiles);
  return (e: string) => p.has("lid") || p.has("aut") || (e === "AMBOS" ? p.has("dba") || p.has("cm") : p.has(e === "BD" ? "dba" : "cm"));
}

export function Ventana() {
  const cat = useCatalogo();
  const [sel, setSel] = useState<string | null>(null);
  const [abrir, setAbrir] = useState(false);
  const lista = useQuery({ queryKey: ["ventanas"], queryFn: () => api.get<VentanaT[]>("/api/ventanas"), refetchInterval: 15_000 });
  const puedeAbrir = cat.data?.find((a) => a.id === "com.vc.abrir")?.permiso === "ejecuta";
  const vs = lista.data ?? [];
  const actual = sel ?? vs.find((v) => v.estado === "en_curso")?.id ?? null;

  return (
    <>
      <Cabecera titulo="Ventana de cambio"
        sub="Pasos de Capa Media y Base de Datos en un solo lugar, en orden y con evidencia única. Todos ven el avance en tiempo real.">
        {puedeAbrir && <button className="btn pri" onClick={() => setAbrir(true)}>+ Abrir ventana</button>}
      </Cabecera>
      <Cargando q={lista} />
      {lista.data && vs.length === 0 && (
        <div className="card"><Vacio>No hay ventanas de cambio todavía.{puedeAbrir ? " Ábrela con el número de cambio y una plantilla de pasos." : ""}</Vacio></div>
      )}
      {vs.length > 0 && (
        <div className="card">
          <div className="tbl-wrap"><table className="tbl-std compacta">
            <thead><tr><th>Cambio</th><th>Título</th><th>Ambiente</th><th>Avance</th><th>Estado</th><th>Abierta</th></tr></thead>
            <tbody>{vs.map((v) => (
              <tr key={v.id} className={"click" + (actual === v.id ? " sel" : "")} onClick={() => setSel(v.id)}>
                <td className="mono-sm"><b>{v.cambio}</b></td><td>{v.titulo}</td><td>{v.ambiente ?? "—"}</td>
                <td style={{ minWidth: 120 }}><div className="bar"><i style={{ width: `${(v.hechos / Math.max(v.total_pasos ?? 1, 1)) * 100}%` }} /></div>
                  <span className="muted small">{v.hechos} de {v.total_pasos}</span></td>
                <td><span className={"tag " + (ESTADO_V[v.estado]?.[1] ?? "")}>{ESTADO_V[v.estado]?.[0] ?? v.estado}</span></td>
                <td className="small">{fecha(v.creada)} · {v.creada_por}</td>
              </tr>))}</tbody>
          </table></div>
        </div>
      )}
      {actual && <DetalleVentana key={actual} id={actual} />}
      {abrir && <AbrirVentana cerrar={() => setAbrir(false)} abierta={(id) => { setAbrir(false); setSel(id); }} />}
    </>
  );
}

function DetalleVentana({ id }: { id: string }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { avisar } = useSesion();
  const cat = useCatalogo();
  const opera = useOperaEquipo();
  const q = useQuery({ queryKey: ["ventana", id], queryFn: () => api.get<VentanaT>(`/api/ventanas/${id}`), refetchInterval: 10_000 });
  const [nota, setNota] = useState("");
  const [omitir, setOmitir] = useState(false);
  const [cerrar, setCerrar] = useState(false);
  const [ocupado, setOcupado] = useState(false);
  const v = q.data;
  if (!v) return <Cargando q={q} />;
  const pasos = v.pasos ?? [];
  const cur = pasos.find((p) => p.actual);
  const abierta = v.estado === "en_curso";
  const puedePaso = cat.data?.find((a) => a.id === "com.vc.paso")?.permiso === "ejecuta";
  const puedeCerrar = cat.data?.find((a) => a.id === "com.vc.cerrar")?.permiso === "ejecuta";

  function refrescar(nv?: VentanaT) {
    if (nv) qc.setQueryData(["ventana", id], nv);
    qc.invalidateQueries({ queryKey: ["ventanas"] });
    qc.invalidateQueries({ queryKey: ["ventana", id] });
  }
  async function post(ruta: string, cuerpo: unknown, ok: string) {
    setOcupado(true);
    try { refrescar(await api.post<VentanaT>(ruta, cuerpo)); avisar(ok); setNota(""); setOmitir(false); }
    catch (e) { avisar(mensajeError(e)); }
    finally { setOcupado(false); }
  }
  async function vincular(e: Ejecucion) {
    if (!cur) return;
    await post(`/api/ventanas/${id}/pasos/${cur.orden}/vincular`, { ejecucion_id: e.id },
      e.estado === "exitosa" ? `Paso ${cur.orden} completado.` : `La ejecución terminó ${e.estado}: el paso sigue pendiente.`);
  }

  return (
    <>
      <div className="card">
        <h3>Ventana de cambio · <span className="mono">{v.cambio}</span>
          <span className="sub fila" style={{ gap: 6 }}>{v.ambiente && <span className={"tag " + (v.ambiente === "PROD" ? "t-crit" : "")}>{v.ambiente}</span>}
            <span className={"tag " + (ESTADO_V[v.estado]?.[1] ?? "")}>{ESTADO_V[v.estado]?.[0] ?? v.estado}</span></span></h3>
        <p className="small" style={{ marginTop: 0 }}><b>{v.titulo}</b> · paso {Math.min((v.paso_actual ?? pasos.length), pasos.length)} de {pasos.length}
          {cur ? <> · turno de <b>{EQUIPO[cur.equipo]}</b></> : abierta ? " · todos los pasos completos" : ""}</p>
        <div className="bar"><i style={{ width: `${(v.hechos / Math.max(pasos.length, 1)) * 100}%` }} /></div>
      </div>
      <div className="g21">
        <div className="steps">
          {pasos.map((p) => {
            const cls = p.estado !== "pendiente" ? "done" : p.actual && abierta ? "cur" : "";
            return (
              <div key={p.orden} className={"step " + cls}>
                <div className="n">{p.estado === "hecho" ? "✓" : p.estado === "omitido" ? "–" : p.orden}</div>
                <div className="grow">
                  <div><TagEquipo e={p.equipo} /> <b>{p.titulo}</b></div>
                  <div className="meta"><span className="mono-sm">{p.accion === "manual" ? "paso manual" : p.accion}</span>
                    {Object.keys(p.params).length > 0 && <> · {Object.entries(p.params).filter(([k]) => k !== "cambio").map(([, x]) => String(x)).join(" · ")}</>}</div>
                  {p.nota && <div className="meta">{p.nota}</div>}
                  {p.comentario && <div className="meta">{p.estado === "pendiente" ? "⚠ " : "Nota: "}{p.comentario}</div>}
                </div>
                <div style={{ textAlign: "right" }}>
                  {p.estado === "hecho" && <span className="tag t-ok" title={p.hecho_en ?? ""}>✓ {p.hecho_por}</span>}
                  {p.estado === "omitido" && <span className="tag">omitido · {p.hecho_por}</span>}
                  {p.ejecucion && <div><button className="lnk small" onClick={() => nav("/ejecuciones?id=" + p.ejecucion!.id)}>
                    {p.ejecucion.id}</button> <EstadoEj e={p.ejecucion.estado} /></div>}
                  {p.estado === "pendiente" && !p.actual && <span className="muted small">bloqueado</span>}
                  {p.estado === "pendiente" && p.actual && abierta && !opera(p.equipo) && <span className="muted small">esperando a {EQUIPO[p.equipo]}</span>}
                </div>
              </div>
            );
          })}
        </div>
        <div>
          {abierta && cur && (
            <div className="card">
              <h3>Paso {cur.orden} · {cur.titulo} <span className="sub">{EQUIPO[cur.equipo]}</span></h3>
              {!puedePaso || !opera(cur.equipo) ? (
                <Vacio>Este paso lo ejecuta {EQUIPO[cur.equipo]}{cur.equipo !== "AMBOS" ? " o el líder" : ""}. Aquí verás el avance en cuanto termine.</Vacio>
              ) : cur.accion === "manual" ? (
                <>
                  {cur.nota && <div className="info small">{cur.nota}</div>}
                  <label className="lb mt">Nota (qué se hizo, log o adjunto)
                    <textarea className="f" rows={3} value={nota} maxLength={1000} onChange={(e) => setNota(e.target.value)} /></label>
                  <div className="btns mt">
                    <button className="btn pri" disabled={ocupado} onClick={() => post(`/api/ventanas/${id}/pasos/${cur.orden}/marcar`, { estado: "hecho", nota }, `Paso ${cur.orden} marcado como hecho.`)}>Marcar como hecho</button>
                  </div>
                </>
              ) : (
                <Accion key={`${id}-${cur.orden}-${cur.ejecucion_id ?? ""}`} accion={cur.accion} compacto iniciales={cur.params}
                        ocultos={Object.keys(cur.params)} referencia={v.cambio} alTerminar={vincular} />
              )}
              {puedePaso && opera(cur.equipo) && (
                <div className="mt">
                  {!omitir ? <button className="btn sm ghost" onClick={() => setOmitir(true)}>Omitir este paso…</button> : (
                    <div className="fila" style={{ gap: 6, flexWrap: "wrap" }}>
                      <input className="f" style={{ flex: 1, minWidth: 180 }} placeholder="Motivo (obligatorio)" value={nota} maxLength={500} onChange={(e) => setNota(e.target.value)} />
                      <button className="btn sm" disabled={ocupado || nota.trim().length < 3}
                              onClick={() => post(`/api/ventanas/${id}/pasos/${cur.orden}/marcar`, { estado: "omitido", nota }, `Paso ${cur.orden} omitido.`)}>Omitir</button>
                      <button className="btn sm ghost" onClick={() => { setOmitir(false); setNota(""); }}>Cancelar</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <div className="card">
            <h3>Resumen</h3>
            <div className="kv">
              <span>Cambio</span><span className="mono">{v.cambio}</span>
              <span>Ambiente</span><span>{v.ambiente ?? "—"}</span>
              {v.base && <><span>Base</span><span className="mono">{v.base}</span></>}
              {v.hosts && <><span>Servidores</span><span className="mono">{v.hosts}</span></>}
              {v.dominios && <><span>Dominios</span><span>{v.dominios}</span></>}
              <span>Abrió</span><span>{v.creada_por} · {fecha(v.creada)}</span>
              {v.cerrada && <><span>Cerró</span><span>{v.cerrada_por} · {fecha(v.cerrada)}</span></>}
              <span>Participantes</span><span>{Array.from(new Set(pasos.map((p) => p.hecho_por).filter(Boolean))).join(", ") || "—"}</span>
            </div>
            <div className="btns mt">
              <button className="btn sm" onClick={() => nav(`/incidentes?clave=${encodeURIComponent(v.cambio)}`)}>Ver el caso y sus ejecuciones</button>
              {abierta && puedeCerrar && <button className={"btn sm " + (cur ? "" : "pri")} onClick={() => setCerrar(true)}>Cerrar ventana</button>}
            </div>
            {v.resumen && <pre className="console mt" style={{ maxHeight: 220 }}>{v.resumen}</pre>}
          </div>
        </div>
      </div>
      {cerrar && (
        <Modal titulo={`Cerrar ventana ${v.cambio}`} cerrar={() => setCerrar(false)}
               pie={<button className="btn pri" disabled={ocupado || (Boolean(cur) && nota.trim().length < 3)}
                            onClick={async () => { await post(`/api/ventanas/${id}/cerrar`, { nota }, "Ventana cerrada con su resumen."); setCerrar(false); }}>Cerrar ventana</button>}>
          {cur ? <>
            <div className="warnbox">Quedan pasos pendientes desde el {cur.orden}. La ventana queda como <b>cerrada incompleta</b>.</div>
            <label className="lb mt">Motivo (obligatorio)<textarea className="f" rows={3} value={nota} maxLength={500} onChange={(e) => setNota(e.target.value)} /></label>
          </> : <p>Todos los pasos están completos. Se guarda el resumen con quién hizo cada paso y sus ejecuciones; si {v.cambio} es una clave de Jira, se comenta en el caso.</p>}
        </Modal>
      )}
    </>
  );
}

function AbrirVentana({ cerrar, abierta }: { cerrar: () => void; abierta: (id: string) => void }) {
  const { avisar } = useSesion();
  const qc = useQueryClient();
  const inv = useInventario();
  const pls = useQuery({ queryKey: ["ventanas-plantillas"], queryFn: () => api.get<Plantilla[]>("/api/ventanas/plantillas") });
  const [f, setF] = useState({ cambio: "", plantilla: "despliegue_bd_cm", titulo: "", ambiente: "", base: "", hosts: "", dominios: "" });
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const pl = pls.data?.find((p) => p.id === f.plantilla);
  const poner = (k: keyof typeof f, x: string) => setF((p) => ({ ...p, [k]: x }));
  const bases = (inv.data?.bases ?? []).filter((b) => !f.ambiente || b.ambiente === f.ambiente);
  const servidores = (inv.data?.servidores ?? []).filter((s) => !f.ambiente || s.ambiente === f.ambiente);
  const ambientes = Array.from(new Set((inv.data?.bases ?? []).map((b) => b.ambiente).concat((inv.data?.servidores ?? []).map((s) => s.ambiente)))).filter(Boolean).sort();
  const faltan = (pl?.requiere ?? []).filter((r) => !(f as any)[r]);

  async function enviar() {
    setOcupado(true); setError("");
    try {
      const v = await api.post<VentanaT>("/api/ventanas", f);
      qc.invalidateQueries({ queryKey: ["ventanas"] });
      avisar(`Ventana ${v.id} abierta para ${v.cambio}.`);
      abierta(v.id);
    } catch (e) { setError(mensajeError(e)); }
    finally { setOcupado(false); }
  }

  return (
    <Modal titulo="Abrir ventana de cambio" cerrar={cerrar}
           pie={<button className="btn pri" disabled={ocupado || !f.cambio.trim() || faltan.length > 0} onClick={enviar}>{ocupado ? "Abriendo…" : "Abrir ventana"}</button>}>
      <div className="fgrid">
        <label className="lb">Número de cambio
          <input className="f" value={f.cambio} maxLength={40} placeholder="CHG0012345 o clave de Jira" onChange={(e) => poner("cambio", e.target.value.toUpperCase().trim())} /></label>
        <label className="lb">Plantilla
          <SelectBuscable className="f" value={f.plantilla} onChange={(e) => poner("plantilla", e.target.value)}>
            {(pls.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
          </SelectBuscable></label>
        <label className="lb">Título (opcional)
          <input className="f" value={f.titulo} maxLength={200} placeholder={pl?.nombre} onChange={(e) => poner("titulo", e.target.value)} /></label>
        <label className="lb">Ambiente
          <SelectBuscable className="f" value={f.ambiente} onChange={(e) => setF((p) => ({ ...p, ambiente: e.target.value, base: "", hosts: "" }))}>
            <option value="">— elegir —</option>{ambientes.map((a) => <option key={a} value={a}>{a}</option>)}
          </SelectBuscable></label>
        {pl?.requiere.includes("base") && (
          <label className="lb">Base
            <SelectBuscable className="f" value={f.base} onChange={(e) => poner("base", e.target.value)}>
              <option value="">— elegir —</option><OpcionesActivo items={bases} />
            </SelectBuscable></label>
        )}
        {pl?.requiere.includes("hosts") && (
          <>
            <label className="lb">Servidores (separados por coma)
              <input className="f" list="vc-servidores" value={f.hosts} placeholder="AP104SBCC04,AP23SBCC04" onChange={(e) => poner("hosts", e.target.value.toUpperCase().replace(/\s+/g, ""))} />
              <datalist id="vc-servidores">{servidores.map((s) => <option key={s.id} value={s.id} />)}</datalist></label>
            <label className="lb">Dominios (opcional; vacío = todos)
              <input className="f" value={f.dominios} placeholder="atgcprod,serviciosprod" onChange={(e) => poner("dominios", e.target.value.replace(/\s+/g, ""))} /></label>
          </>
        )}
      </div>
      {pl && (
        <div className="mt">
          <p className="muted small">{pl.descripcion}</p>
          <ol className="small" style={{ paddingLeft: 18, margin: 0 }}>
            {pl.pasos.map((p, i) => <li key={i}><TagEquipo e={p.equipo} /> {p.titulo} <span className="muted mono-sm">{p.accion === "manual" ? "manual" : p.accion}</span></li>)}
          </ol>
        </div>
      )}
      {error && <div className="critbox mt">{error}</div>}
    </Modal>
  );
}
