import { useEffect, useRef, useState } from "react";
import { SelectBuscable } from "./SelectBuscable";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";
import Accion from "./Accion";
import { ComentarEnJira, EvidenciaSnapshot } from "./Jira";
import {
  ActualizandoVista, Antiguedad, Cabecera, Cargando, Clase, Consola, EstadoEj, Modal, Resultado, TablaFilas, Vacio, fecha,
  CampoAmbiente, OpcionesActivo, useAccion, useAmbiente, useCatalogo, useEnAmbiente, useInventario, useSegundoFactor, useSesion,
} from "./comun";
import type { Ejecucion, EstadoObj, Inventario } from "./tipos";

// ======================================================================== INICIO
export function Inicio() {
  const nav = useNavigate();
  const { yo } = useSesion();
  const [todas, setTodas] = useState(false);
  const q = useQuery({ queryKey: ["inicio", todas], queryFn: () => api.get<any>(`/api/inicio${todas ? "?todas=true" : ""}`), refetchInterval: 30_000 });
  if (!q.data) return <Cargando q={q} />;
  const k = q.data.kpis;
  const sem = k.bases ?? {};
  const ultimas: (Ejecucion & { veces?: number })[] = q.data.ultimas;
  return (
    <>
      <Cabecera titulo={`Hola, ${yo.nombre.split(" ")[0]}`} sub="Lo que necesita tu atención ahora. Todo sale del almacén de Ops Central." />
      <div className="kpis">
        {yo.menu.some((g) => g.pantallas.some((p) => p.id === "aprobaciones")) && (
          <Kpi t="Por aprobar" v={k.pendientes_aprobacion} c={k.pendientes_aprobacion ? "warn" : ""} ir={() => nav("/aprobaciones")} />
        )}
        <Kpi t="En curso" v={k.en_curso} ir={() => nav("/ejecuciones")} />
        <Kpi t="Fallidas 24 h" v={k.fallidas_24h} c={k.fallidas_24h ? "crit" : ""} ir={() => nav("/ejecuciones")} />
        <Kpi t="Exitosas 24 h" v={k.exitosas_24h} c="ok" />
        <Kpi t="Episodios Zabbix" v={k.episodios_abiertos} c={k.episodios_abiertos ? "warn" : ""} ir={() => nav("/alertas")} />
        <Kpi t="Riesgos < 7 días" v={k.riesgos_7d} c={k.riesgos_7d ? "crit" : ""} ir={() => nav("/prevencion")} />
        {Object.keys(sem).length > 0 && (
          <Kpi t="Bases en rojo" v={sem.rojo ?? 0} c={sem.rojo ? "crit" : "ok"}
               sub={`${sem.verde ?? 0} verde · ${sem.amarillo ?? 0} amarillo`} ir={() => nav("/salud")} />
        )}
      </div>
      <div className="g21 mt">
        <div className="card">
          <h3>Requiere atención <span className="sub">{q.data.atencion.length} elementos</span></h3>
          {q.data.atencion.length === 0 ? <Vacio>Nada pendiente.</Vacio> : (
            <div className="list">
              {q.data.atencion.map((a: any, i: number) => (
                <div key={i} className="li">
                  <span className={"dot d-" + (a.nivel === "crit" ? "crit" : "warn")} />
                  <div className="grow"><b>{a.texto}</b><div className="muted small">{a.detalle}</div></div>
                  <button className="btn sm" onClick={() => nav("/" + a.ir)}>Ver</button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card">
          <h3>Últimas ejecuciones <span className="sub">{todas ? "de todos los equipos" : "de tu equipo"}</span></h3>
          {ultimas.length === 0 ? <Vacio>Todavía no hay ejecuciones{todas ? "" : " de tu equipo"}.</Vacio> : (
            <div className="list">
              {ultimas.map((e) => (
                <button key={e.id} className="li li-btn" onClick={() => nav("/ejecuciones?id=" + e.id)} title="Ver el detalle y el error">
                  <div className="grow"><b className="mono-sm">{e.accion}</b>{(e.veces ?? 1) > 1 && <span className="tag t-warn" style={{ marginLeft: 6 }}>×{e.veces}</span>}
                    <div className="muted small">{e.destino} · ambiente {e.ambiente} · {e.solicitante} · {fecha(e.creada)}</div></div>
                  <EstadoEj e={e.estado} />
                </button>
              ))}
            </div>
          )}
          {(q.data.otras_equipos > 0 || todas) && (
            <label className="chk small mt"><input type="checkbox" checked={todas} onChange={(ev) => setTodas(ev.target.checked)} />
              {" "}Ver también las de otros equipos{!todas && ` (${q.data.otras_equipos})`}</label>
          )}
        </div>
      </div>
    </>
  );
}

function Kpi({ t, v, c = "", sub, ir }: { t: string; v: number; c?: string; sub?: string; ir?: () => void }) {
  const cuerpo = <><small>{t}</small><b>{v}</b>{sub && <i>{sub}</i>}</>;
  return ir ? <button className={"k " + c} onClick={ir}>{cuerpo}</button> : <div className={"k " + c}>{cuerpo}</div>;
}

// ======================================================================== INVENTARIO
export { InventarioP } from "./Inventario";

// ======================================================================== ALERTAS (Zabbix)
export function Alertas() {
  const [estado, setEstado] = useState("abierto");
  const q = useQuery({ queryKey: ["episodios", estado], queryFn: () => api.get<any[]>(`/api/alertas/episodios?estado=${estado}`) });
  return (
    <>
      <Cabecera titulo="Alertas de Zabbix" sub="Eventos agrupados en episodios: un problema raíz con sus síntomas relacionados por inventario.">
        <div className="tabs" style={{ marginBottom: 0, border: 0 }}>
          {["abierto", "cerrado"].map((e) => <button key={e} className={estado === e ? "on" : ""} onClick={() => setEstado(e)}>{e}s</button>)}
        </div>
      </Cabecera>
      <EstadoZabbix />
      <Cargando q={q} />
      {q.data && q.data.length === 0 && <div className="card"><Vacio>No hay episodios {estado}s.</Vacio></div>}
      {q.data?.map((ep) => (
        <div key={ep.id} className="card">
          <h3>
            <span className="fila"><span className={"dot d-" + (ep.severidad >= 4 ? "crit" : "warn")} style={{ marginTop: 0 }} />{ep.titulo}</span>
            <span className="sub">{ep.activo_raiz} · último evento {fecha(ep.ultimo_evento)}</span>
          </h3>
          {ep.causa_probable && <div className="info">Causa probable: {ep.causa_probable}</div>}
          <div className="mt"><TablaFilas filas={ep.eventos} /></div>
        </div>
      ))}
    </>
  );
}

/** Franja con la última lectura de la API de Zabbix (respaldo del webhook y trends de capacidad). */
function EstadoZabbix() {
  const q = useQuery({ queryKey: ["zabbix-estado"], queryFn: () => api.get<any>("/api/zabbix/estado") });
  if (!q.data) return null;
  const p = q.data.problemas, c = q.data.capacidad;
  const version = p?.datos?.version ?? c?.datos?.version;
  const faltan: string[] = [...(p?.datos?.hosts_sin_inventario ?? [])];
  const error = p?.error || c?.error;
  return (
    <div className="card" style={{ padding: "10px 14px" }}>
      <div className="fila" style={{ flexWrap: "wrap", gap: 8, fontSize: 12.5 }}>
        <b>Zabbix{version ? " " + version : ""}</b>
        {p ? <>
          <span>Problemas abiertos: <b>{p.datos.abiertos}</b></span>
          <span className="sub">respaldo del webhook cada 5 min</span>
          <Antiguedad s={p.antiguedad_s} origen="problem.get" />
        </> : <span className="sub">Todavía no se leyó la API de problemas.</span>}
        {c && <>
          <span>· Series de capacidad: <b>{c.datos.series?.length ?? 0}</b> de {c.datos.en_zabbix}/{c.datos.servidores} servidores</span>
          <Antiguedad s={c.antiguedad_s} origen="trend.get" />
        </>}
      </div>
      {error && <div className="warnbox mt">Última lectura con error: {error}. Se muestran los últimos datos buenos.</div>}
      {faltan.length > 0 && <ListaPlegable titulo="Hosts con problemas que no están en el inventario" items={faltan} />}
      {c?.datos?.faltan_en_zabbix?.length > 0 && (
        <ListaPlegable titulo="Servidores del inventario que no existen en Zabbix" items={c.datos.faltan_en_zabbix} />
      )}
    </div>
  );
}

/** Aviso con una lista larga de nombres: cerrado por defecto, se abre/cierra con un clic. */
function ListaPlegable({ titulo, items }: { titulo: string; items: string[] }) {
  return (
    <details className="info mt plegable">
      <summary>{titulo} <span className="tag">{items.length}</span></summary>
      <div className="plegable-cuerpo">{items.join(", ")}.</div>
    </details>
  );
}

// ======================================================================== PREVENCIÓN (ML)
export function Prevencion() {
  const { yo, avisar } = useSesion();
  const cat = useCatalogo();
  // El filtro de la API usa "bd" | "cm"; el equipo del perfil DBA se llama "dba".
  const equipo = yo.perfiles.includes("lid") ? "" : yo.equipo === "dba" ? "bd" : yo.equipo === "cm" ? "cm" : "";
  const q = useQuery({ queryKey: ["predicciones", equipo], queryFn: () => api.get<any>(`/api/prevencion/predicciones${equipo ? "?equipo=" + equipo : ""}`) });
  const [sel, setSel] = useState<any>(null);
  const puedeOpinar = cat.data?.find((a) => a.id === "ml.feedback")?.permiso === "ejecuta";
  const filas: any[] = q.data?.predicciones ?? [];
  const actual = sel ?? filas[0];

  async function opinar(pid: number, valor: string) {
    try {
      await api.post(`/api/prevencion/predicciones/${pid}/feedback`, { valor });
      avisar("Gracias: tu opinión entrena la siguiente versión del modelo.");
    } catch (e) {
      avisar(mensajeError(e));
    }
  }

  return (
    <>
      <Cabecera titulo="Prevención" sub="Predicción de llenado (tablespaces, ASM, FS, heap) y anomalías. Ordenado por lo que se llena primero." />
      {q.data?.en_sombra && <div className="warnbox" style={{ marginBottom: 14 }}>{q.data.nota}.</div>}
      <Cargando q={q} />
      {q.data && filas.length === 0 && <div className="card"><Vacio>Sin predicciones vigentes.</Vacio></div>}
      <Anomalias equipo={equipo} />
      {filas.length > 0 && (
        <div className="g21">
          <div className="card">
            <div className="tbl-wrap">
              <table className="tbl-std">
                <thead><tr><th>Serie</th><th>Actual</th><th>%/día</th><th>Llega al 100 %</th><th>Confianza</th></tr></thead>
                <tbody>
                  {filas.map((p) => {
                    const u = p.datos?.umbrales?.["100"] ?? {};
                    const dias = u.dias;
                    return (
                      <tr key={p.id} className={"click" + (actual?.id === p.id ? " sel" : "")} onClick={() => setSel(p)}>
                        <td className="mono-sm">{p.serie}</td>
                        <td>{p.datos?.actual ?? "—"} %</td>
                        <td>{p.datos?.pendiente_dia ?? "—"}</td>
                        <td>{dias == null ? <span className="muted">no se llena</span> :
                          <span className={"tag " + (dias < 3 ? "t-crit" : dias < 7 ? "t-warn" : "t-ok")}>{dias.toFixed(1)} días</span>}</td>
                        <td>{p.datos?.confianza ?? p.modelo}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {actual && (
            <div className="card">
              <h3>{actual.serie}<span className="sub">{actual.modelo} · {actual.version}</span></h3>
              <Grafico serie={actual.serie} pred={actual.datos} />
              <p className="small mt">{actual.datos?.explicacion}</p>
              <div className="kv mt">
                {["85", "95", "100"].map((u) => {
                  const x = actual.datos?.umbrales?.[u];
                  return x ? [<span key={u + "a"}>Al {u} %</span>,
                    <b key={u + "b"}>{x.ya_superado ? "ya superado" : x.fecha ? `${fecha(x.fecha)} (entre ${fecha(x.fecha_p10)} y ${fecha(x.fecha_p90)})` : "—"}</b>] : null;
                })}
              </div>
              {puedeOpinar && (
                <div className="btns mt">
                  <button className="btn sm green" onClick={() => opinar(actual.id, "util")}>Útil</button>
                  <button className="btn sm" onClick={() => opinar(actual.id, "falsa_alarma")}>Falsa alarma</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** M3: lo que hoy se sale de lo normal para ese día y hora de la semana (mismo horario de semanas anteriores). */
function Anomalias({ equipo }: { equipo: string }) {
  const q = useQuery({ queryKey: ["anomalias", equipo], queryFn: () => api.get<any>(`/api/prevencion/anomalias${equipo ? "?equipo=" + equipo : ""}`), refetchInterval: 300_000 });
  const filas: any[] = q.data?.anomalias ?? [];
  if (!q.data) return null;
  return (
    <div className="card">
      <h3>Anomalías <span className="sub">valor actual frente a lo normal para el mismo día y hora · {filas.length} serie(s){q.data.calculada ? ` · calculado ${fecha(q.data.calculada)}` : ""}</span></h3>
      {filas.length === 0 ? <Vacio>Nada fuera de lo normal{q.data.calculada ? "" : " (todavía no hay historia suficiente: hacen falta 3 semanas)"}.</Vacio> : (
        <div className="tbl-wrap"><table className="tbl-std">
          <thead><tr><th>Serie</th><th className="num">Actual</th><th className="num">Normal</th><th>Qué pasa</th><th className="num">Puntaje</th></tr></thead>
          <tbody>{filas.map((a) => (
            <tr key={a.serie}>
              <td className="mono-sm">{a.serie}</td><td className="num">{Number(a.valor).toFixed(1)}</td><td className="num">{Number(a.normal).toFixed(1)}</td>
              <td><span className={"tag " + (Math.abs(a.z) >= 6 ? "t-crit" : "t-warn")}>{a.z > 0 ? "alto" : "bajo"}</span> {a.explicacion}</td>
              <td className="num">{a.z}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Grafico({ serie, pred }: { serie: string; pred: any }) {
  const caja = useRef<HTMLDivElement>(null);
  const q = useQuery({ queryKey: ["serie", serie], queryFn: () => api.get<{ ts: string; valor: number }[]>(`/api/prevencion/serie?serie=${encodeURIComponent(serie)}&dias=20`) });
  useEffect(() => {
    if (!caja.current || !q.data) return;
    let graf: any;
    let vivo = true;
    import("echarts").then((ec) => {
      if (!vivo || !caja.current) return;
      const css = getComputedStyle(document.documentElement);
      const color = (v: string, d: string) => css.getPropertyValue(v).trim() || d;
      graf = ec.init(caja.current);
      const datos = q.data.map((p) => [p.ts, p.valor]);
      const u100 = pred?.umbrales?.["100"];
      // La proyección parte del último valor que usó el modelo (no del último punto crudo).
      const proy = u100?.fecha && pred?.ultimo_dato ? [[pred.ultimo_dato, pred.actual], [u100.fecha, 100]] : [];
      graf.setOption({
        grid: { left: 44, right: 40, top: 16, bottom: 30 },
        tooltip: { trigger: "axis" },
        xAxis: { type: "time", axisLabel: { color: color("--muted", "#64748b") } },
        yAxis: { type: "value", max: 100, min: (v: any) => Math.max(0, Math.floor(v.min - 5)), axisLabel: { formatter: "{value} %", color: color("--muted", "#64748b") } },
        series: [
          { type: "line", name: "Uso", data: datos, showSymbol: false, lineStyle: { width: 2, color: color("--acc", "#2563eb") } },
          { type: "line", name: "Proyección", data: proy, showSymbol: false, lineStyle: { type: "dashed", color: color("--crit", "#c5222c") } },
          { type: "line", name: "Umbrales", data: [], markLine: { symbol: "none", silent: true, label: { formatter: "{c} %" },
            data: [{ yAxis: 85 }, { yAxis: 95 }], lineStyle: { color: color("--warn", "#b45309"), type: "dotted" } } },
        ],
      });
    });
    const r = () => graf?.resize();
    window.addEventListener("resize", r);
    return () => { vivo = false; window.removeEventListener("resize", r); graf?.dispose(); };
  }, [q.data, pred]);
  if (q.data && q.data.length === 0) return <Vacio>Sin puntos de la serie en los últimos 20 días.</Vacio>;
  return <div ref={caja} className="chart-box" role="img" aria-label={`Uso de ${serie} y proyección`} />;
}

// ======================================================================== CAPA MEDIA (capamedia.tsx)
export { Frmweb, Orquestacion, UsuariosWL } from "./capamedia";

// ======================================================================== SNAPSHOT DE INVÁLIDOS
export function Snapshot() {
  const qc = useQueryClient();
  const { soloLectura } = useSesion();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const [base, setBase] = useState("");
  const [cambio, setCambio] = useState("");
  // La referencia es opcional: sin ella, el ANTES abre un grupo automático y lo demás usa el último ANTES.
  const refValida = !cambio || /^[A-Za-z0-9][A-Za-z0-9_\-/.]{2,39}$/.test(cambio);
  const listo = Boolean(base) && refValida;
  const comp = useQuery({
    queryKey: ["snapshot", base, cambio], enabled: listo, retry: false,
    queryFn: () => api.get<EstadoObj & { grupo?: string }>(cambio ? `/api/estado/bd/${base}/snapshot/${cambio}` : `/api/estado/bd/${base}/snapshot`),
  });
  const refrescar = () => qc.invalidateQueries({ queryKey: ["snapshot", base, cambio] });
  const c = comp.data?.datos;
  return (
    <>
      <Cabecera titulo="Snapshot de inválidos" sub="Antes y después de un despliegue: qué objetos quedaron inválidos por el cambio, sin confundirlos con la línea base." />
      <div className="card">
        <div className="fgrid">
          {inv.data && <CampoAmbiente id="snap-amb" items={inv.data.bases} alCambiar={() => setBase("")} />}
          <label className="lb">Base
            <SelectBuscable className="f" value={base} onChange={(e) => setBase(e.target.value)}>
              <option value="">— elegir —</option>
              {inv.data && <OpcionesActivo items={inv.data.bases} />}
            </SelectBuscable>
          </label>
          <label className="lb">Referencia (opcional: caso, despliegue, tarea…)
            <input className="f" value={cambio} maxLength={40} placeholder="Vacío = se agrupa con el último ANTES" onChange={(e) => setCambio(e.target.value.toUpperCase().trim())} />
            {!refValida && <span className="err-campo">3 a 40 caracteres: letras, números, - _ . /</span>}
          </label>
        </div>
        {base && !cambio && (
          <p className="muted small" style={{ marginBottom: 0 }}>Sin referencia: “Tomar ANTES” abre un grupo nuevo (SNAP-fecha-hora) y DESPUÉS, Comparar y
            Recompilar usan el último ANTES de {base}.</p>
        )}
      </div>
      {!listo ? <div className="card"><Vacio>Elige la base. La referencia es opcional.</Vacio></div> : (
        <div className="g2">
          <div className="steps">
            <Paso n={1} t="Snapshot ANTES" d="Antes de desplegar.">
              <Accion key={"a" + base + cambio} accion="bd.snap.tomar" compacto etiqueta="Tomar ANTES" iniciales={{ base, cambio, momento: "ANTES" }} />
            </Paso>
            <Paso n={2} t="Snapshot DESPUÉS" d="Cuando el despliegue termina.">
              <Accion key={"d" + base + cambio} accion="bd.snap.tomar" compacto etiqueta="Tomar DESPUÉS" iniciales={{ base, cambio, momento: "DESPUES" }} />
            </Paso>
            <Paso n={3} t="Comparar" d="Nuevos, corregidos y persistentes frente a la línea base.">
              <Accion key={"c" + base + cambio} accion="bd.snap.comparar" compacto etiqueta="Comparar" iniciales={{ base, cambio }} alTerminar={refrescar} />
            </Paso>
            <Paso n={4} t="Recompilar los nuevos" d="Solo los inválidos que dejó este cambio; toma el snapshot FINAL al terminar.">
              <Accion key={"r" + base + cambio} accion="bd.inv.recompilar" compacto etiqueta="Recompilar" iniciales={{ base, cambio }} alTerminar={refrescar} />
            </Paso>
          </div>
          <div className="card">
            <h3>Comparación {comp.data?.grupo && <span className="tag mono">{comp.data.grupo}</span>} {comp.data && <Antiguedad s={comp.data.antiguedad_s} />}</h3>
            {comp.error ? <Vacio>{(comp.error as Error).message}</Vacio> : !c ? <Cargando q={comp} /> : (
              <>
                <div className={c.veredicto === "sin_nuevos" ? "info" : "critbox"}>
                  {c.veredicto === "sin_nuevos" ? "El cambio no dejó objetos inválidos nuevos." : `El cambio dejó ${c.resumen.nuevos} objetos inválidos nuevos.`}
                </div>
                {c.aviso_linea_base && <div className="warnbox mt small">{c.aviso_linea_base}</div>}
                <div className="kpis mt">
                  <div className={"k " + (c.resumen.nuevos ? "crit" : "ok")}><small>Nuevos</small><b>{c.resumen.nuevos}</b></div>
                  <div className="k ok"><small>Corregidos</small><b>{c.resumen.corregidos}</b></div>
                  <div className="k"><small>Persistentes</small><b>{c.resumen.persistentes}</b></div>
                  <div className="k"><small>Línea base</small><b>{c.resumen.en_linea_base}</b></div>
                </div>
                <p className="small muted mt">ANTES: {fecha(c.antes.tomado)} ({c.antes.total}) · {c.despues.momento}: {fecha(c.despues.tomado)} ({c.despues.total})</p>
                {c.nuevos.length > 0 && <div className="mt"><h3 className="small">Nuevos por el cambio</h3><TablaFilas filas={c.nuevos} /></div>}
                {c.corregidos.length > 0 && <div className="mt"><h3 className="small">Corregidos</h3><TablaFilas filas={c.corregidos} /></div>}
              </>
            )}
            {(comp.data?.grupo || cambio) && c && <EvidenciaSnapshot base={base} grupo={comp.data?.grupo || cambio} soloLectura={soloLectura("snapshot")} />}
          </div>
        </div>
      )}
    </>
  );
}

function Paso({ n, t, d, children }: { n: number; t: string; d: string; children: React.ReactNode }) {
  return (
    <div className="step">
      <span className="n">{n}</span>
      <div className="grow"><b>{t}</b><div className="meta">{d}</div><div className="mt">{children}</div></div>
    </div>
  );
}

// ======================================================================== BLOQUEOS
function useRefresco() {
  const { avisar } = useSesion();
  const qc = useQueryClient();
  return async (base: string) => {
    try {
      const r = await api.post<any>("/api/refresco", { objeto: `bd:${base}` });
      avisar(r.estado === "reciente" ? `Ya hay un refresco reciente de ${base}: se une a ese (próximo en ${r.reintentar_en_s} s).`
        : "Refresco pedido. Los datos llegan en segundos.");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["bd"] }), 2500);
    } catch (e) {
      avisar(mensajeError(e));
    }
  };
}

export function Bloqueos() {
  const flotaQ = useQuery({ queryKey: ["bd", "bloqueos"], queryFn: () => api.get<any[]>("/api/estado/bd/bloqueos"), refetchInterval: 30_000 });
  const inv = useInventario();
  const enAmb = useEnAmbiente();
  const ambDe = (b: string) => inv.data?.bases.find((x) => x.id === b)?.ambiente;
  const delAmb = flotaQ.data?.filter((b) => enAmb(ambDe(b.base)));
  // Solo cuentan los bloqueos con lectura VIGENTE (reciente). Las lecturas viejas no se presentan como
  // bloqueo actual: van aparte, marcadas "sin confirmar", hasta que la revisión automática las confirme o limpie.
  const [verTodas, setVerTodas] = useState(false);
  const conBloqueo = (delAmb ?? []).filter((b) => b.bloqueadas > 0 && b.vigente).sort((a, b) => b.bloqueadas - a.bloqueadas);
  const viejas = (delAmb ?? []).filter((b) => b.bloqueadas > 0 && !b.vigente);
  const sinBloqueo = (delAmb ?? []).filter((b) => !(b.bloqueadas > 0) && !b.error).length;
  const conError = (delAmb ?? []).filter((b) => !(b.bloqueadas > 0) && b.error).length;
  const visibles = delAmb && (verTodas ? delAmb : [...conBloqueo, ...viejas]);
  const [base, setBase] = useState("");
  const actual = (base && visibles?.some((b) => b.base === base) ? base : "") || conBloqueo[0]?.base || "";
  const det = useQuery({ queryKey: ["bd", "bloqueos", actual], enabled: Boolean(actual), queryFn: () => api.get<EstadoObj & { vigente?: boolean; grupo?: string[] }>(`/api/estado/bd/${actual}/bloqueos`) });
  const refrescar = useRefresco();
  const cat = useCatalogo();
  const puedeRefrescar = cat.data?.find((a) => a.id === "com.refresco")?.permiso === "ejecuta";
  const [terminar, setTerminar] = useState<any>(null);
  const min = (s: number) => Math.round((s ?? 0) / 60);
  const tarjeta = (b: any) => (
    <button key={b.base} className={"dbc" + (actual === b.base ? " on" : "") + (b.bloqueadas > 0 && !b.vigente ? " viejo" : "")} onClick={() => setBase(b.base)}
            aria-label={`${b.base}: ${b.bloqueadas} sesiones bloqueadas`} aria-pressed={actual === b.base}>
      <div className="fila entre"><b>{b.base} <span className="muted small">{ambDe(b.base)}</span></b>
        {b.bloqueadas > 0 ? (b.vigente ? <span className="tag t-crit">{b.bloqueadas} bloqueadas</span> : <span className="tag">sin confirmar</span>)
          : <span className="tag t-ok">sin bloqueos</span>}</div>
      <small>{b.error ? "error: " + b.error : `${b.arboles} árboles · leído hace ${min(b.antiguedad_s)} min`}</small>
      {b.grupo?.length > 1 && <small className="muted d-block">RAC: también {b.grupo.filter((x: string) => x !== b.base).join(", ")}</small>}
    </button>
  );
  return (
    <>
      <Cabecera titulo="Bloqueos" sub="Sesiones de usuario esperando un bloqueo de aplicación: quién bloquea a quién, desde cuándo y sobre qué objeto.">
      </Cabecera>
      <div className="card"><div className="fgrid"><CampoAmbiente id="blq-amb" items={inv.data?.bases ?? []} alCambiar={() => setBase("")} /></div></div>
      <ActualizandoVista vista="bloqueos" />
      <Cargando q={flotaQ} />
      {delAmb && delAmb.length > 0 && (
        <div className="fila entre small muted" style={{ margin: "4px 2px 8px" }}>
          <span>
            {conBloqueo.length ? `${conBloqueo.length} base(s) con sesiones bloqueadas ahora` : "Ninguna base tiene sesiones bloqueadas ahora"}
            {` · ${sinBloqueo} sin bloqueos`}{viejas.length ? ` · ${viejas.length} con lectura vieja sin confirmar` : ""}
            {conError ? ` · ${conError} sin dato (error al recolectar)` : ""}
          </span>
          <label className="fila" style={{ gap: 6, cursor: "pointer" }}>
            <input type="checkbox" checked={verTodas} onChange={(e) => { setVerTodas(e.target.checked); setBase(""); }} />
            Mostrar también las bases sin bloqueos
          </label>
        </div>
      )}
      <div className="dbsel">{(verTodas ? delAmb : conBloqueo)?.map(tarjeta)}</div>
      {!verTodas && viejas.length > 0 && (
        <details className="card mt">
          <summary className="small"><b>{viejas.length} lectura(s) vieja(s) sin confirmar</b> <span className="muted">· se releen solas cada 5 min; pide un refresco para confirmarlas ya</span></summary>
          <div className="dbsel mt">{viejas.map(tarjeta)}</div>
        </details>
      )}
      {delAmb?.length === 0 && <div className="card"><Vacio>Sin datos de bloqueos en este ambiente todavía. Pide un refresco desde Salud.</Vacio></div>}
      {!!delAmb?.length && !conBloqueo.length && !viejas.length && !verTodas && (
        <div className="card"><Vacio>No hay sesiones bloqueadas en las {delAmb.length} bases revisadas.</Vacio></div>
      )}
      {det.data && (
        <div className="card">
          <h3>{actual} <span className="fila">{det.data.filtrado && <span className="tag t-cm">{det.data.filtrado}</span>}<Antiguedad s={det.data.antiguedad_s} origen={det.data.origen} />
            {puedeRefrescar && <button className="btn sm" onClick={() => refrescar(actual)} title={`Vuelve a leer solo ${actual}`}>↻ Solo esta base</button>}</span></h3>
          {det.data.grupo && det.data.grupo.length > 1 && <div className="info small">Base RAC: {det.data.grupo.join(", ")} son la misma base por distintos nodos; se muestra una sola vez.</div>}
          {det.data.vigente === false && <div className="warnbox small mt">Lectura de hace {min(det.data.antiguedad_s)} min: puede que el bloqueo ya no exista. Pide un refresco antes de actuar; mientras tanto no se permite terminar sesiones.</div>}
          {det.data.datos.arboles.length === 0 ? <Vacio>No hay sesiones bloqueadas.</Vacio> :
            det.data.datos.arboles.map((a: any, i: number) => (
              <div key={i} className="tree mt">
                <div className="small muted" style={{ marginBottom: 6 }}>{a.afectadas} sesiones afectadas · espera máx. {a.espera_max_s} s · {a.objetos.join(", ") || "objeto no identificado"}</div>
                <Nodo n={a.arbol} raiz alTerminar={setTerminar} vigente={det.data.vigente !== false} />
              </div>
            ))}
        </div>
      )}
      {det.error && <div className="critbox mt">{(det.error as Error).message}</div>}
      {terminar && (
        <Modal titulo={`Terminar sesión raíz ${terminar.sid},${terminar.serial}`} cerrar={() => setTerminar(null)}>
          <div className="warnbox">Se termina con <span className="mono-sm">ALTER SYSTEM KILL SESSION … IMMEDIATE</span> a través del paquete
            controlado de Ops Central. La transacción de la sesión se revierte.</div>
          <div className="kv mt">
            <span>Usuario</span><b>{terminar.username ?? "—"}</b><span>Máquina</span><b>{terminar.machine ?? "—"}</b>
            <span>Módulo</span><b>{terminar.module ?? "—"}</b><span>Programa</span><b>{terminar.program ?? "—"}</b>
          </div>
          <div className="mt">
            <Accion accion="bd.ses.terminar" compacto etiqueta="Terminar sesión"
                    iniciales={{ base: actual, sid: terminar.sid, serial: terminar.serial, inst_id: terminar.inst_id ?? 1 }}
                    alTerminar={() => det.refetch()} />
          </div>
        </Modal>
      )}
    </>
  );
}

function Nodo({ n, raiz, alTerminar, vigente }: { n: any; raiz?: boolean; alTerminar: (n: any) => void; vigente: boolean }) {
  const cat = useCatalogo();
  const puede = cat.data?.find((a) => a.id === "bd.ses.terminar")?.permiso === "ejecuta";
  // Nunca se ofrece terminar un proceso de background (LGWR, SMON…: tumba la instancia) ni sobre una lectura vieja.
  const motivoNo = n.background ? "Proceso de background de Oracle: no se termina" : !vigente ? "Lectura vieja: refresca primero" : "";
  return (
    <div>
      <div className={"node" + (raiz ? " root" : "")}>
        <div className="grow" style={{ flex: 1 }}>
          <b>{raiz ? "Raíz · " : ""}SID {n.sid}{n.serial ? "," + n.serial : ""}</b> <span className="muted small">inst {n.inst_id}</span>
          {n.background && <span className="tag" style={{ marginLeft: 6 }}>background</span>}
          <div className="small">{n.username ?? "?"} · {n.machine ?? "?"} · {n.module ?? n.program ?? ""}</div>
          <div className="small muted">{n.event ?? ""}{n.seconds_in_wait ? ` · ${n.seconds_in_wait} s` : ""}{n.sql_id ? ` · SQL_ID ${n.sql_id}` : ""}{n.objeto ? ` · ${n.objeto}` : ""}</div>
        </div>
        {raiz && puede && !n.incompleto && (motivoNo
          ? <span className="small muted">{motivoNo}</span>
          : <button className="btn sm red" onClick={() => alTerminar(n)}>Terminar</button>)}
      </div>
      {n.hijos?.length > 0 && <div className="kids">{n.hijos.map((h: any) => <Nodo key={`${h.inst_id}-${h.sid}`} n={h} alTerminar={alTerminar} vigente={vigente} />)}</div>}
    </div>
  );
}

// ======================================================================== SALUD
// Tipos de error de la recolección y qué hacer con cada uno (informe salud-bases-no-actualiza).
const TIPOS_ERROR: Record<string, string> = {
  "ORA-00942": "Falta ADMINBD.TBL_BASE_OBJETOS_INVALIDOS o el GRANT SELECT a OPS_CONSULTA: aplicar db/oracle/01 y 02 en la base (DBA).",
  "DPY-3015": "El verificador de clave de la cuenta no es compatible con el modo thin: configurar [bd] oracle_client_dir (modo thick) y reiniciar.",
  "DPY-6005": "No se pudo conectar (red, firewall, listener o VPN).",
  "DPY-4024": "Una consulta superó call_timeout_s: subirlo o revisar la plantilla con el DBA.",
  "DPY-4011": "La base o la red cerró la conexión.",
  "ORA-01017": "Usuario o clave de OPS_CONSULTA incorrectos para esa base.",
  "ORA-28001": "La clave de OPS_CONSULTA expiró: renovarla en la base y en el gestor de secretos.",
  "ORA-28000": "La cuenta OPS_CONSULTA está bloqueada.",
  "ORA-01034": "Instancia Oracle no disponible (caída o DSN erróneo).",
  "ORA-01033": "La instancia está arrancando o deteniéndose.",
  "ORA-12170": "Timeout de conexión TNS (red o firewall).",
  "ORA-12514": "El listener no conoce el servicio del DSN.",
  "ORA-20900": "El trigger TRG_OPS_CENTRAL_ORIGEN rechazó la IP del worker: agregarla a &IPS_WORKERS.",
  "CIRCUITO": "Demasiados fallos seguidos: la base queda en pausa unos minutos antes de reintentar.",
  "SIN DRIVER": "Este equipo no tiene instalado python-oracledb: uv sync --extra prod (o pip install -e \".[prod]\") y reiniciar opscentral dev.",
};
function tipoError(e?: string | null): string {
  if (!e) return "";
  const m = /(ORA-\d{5}|DPY-\d{4})/.exec(e);
  if (m) return m[1];
  if (/circuito|fallos seguidos/i.test(e)) return "CIRCUITO";
  if (/No module named 'oracledb'/.test(e)) return "SIN DRIVER";
  return "OTRO";
}

function Hace({ s, nunca, titulo }: { s?: number | null; nunca?: boolean; titulo?: string }) {
  if (nunca) return <span className="tag t-crit" title={titulo}>nunca</span>;
  return <Antiguedad s={s ?? undefined} origen={titulo} />;
}

/** "FRA 62 % · resp. 20 h · DG ok" para la celda de la flota. */
function resumenProteccion(p: any): string {
  if (!p) return "—";
  const partes: string[] = [];
  const fra = (p.fra ?? []).find((f: any) => f.name && f.pct_usado != null);
  if (fra) partes.push(`FRA ${Number(fra.pct_usado).toFixed(0)} %`);
  const bd = (p.respaldo ?? []).filter((r: any) => String(r.input_type ?? "").startsWith("DB") && r.horas != null).map((r: any) => Number(r.horas));
  if (p.respaldo) partes.push(bd.length ? `resp. ${Math.min(...bd).toFixed(0)} h` : "sin resp.");
  const dg = p.dataguard;
  if (dg && (dg.destinos?.length || Object.keys(dg.lag ?? {}).length)) partes.push("DG");
  return partes.join(" · ") || "—";
}

function Proteccion({ p }: { p: any }) {
  const fra = (p.fra ?? []).filter((f: any) => f.name);
  const dg = p.dataguard ?? {};
  return (
    <>
      <h3 className="small mt">Protección</h3>
      <div className="kpis">
        {fra.map((f: any) => (
          <div key={f.name} className={"k " + (f.pct_usado >= 95 ? "crit" : f.pct_usado >= 85 ? "warn" : "ok")}>
            <small>FRA {f.name} (sin lo recuperable)</small><b>{f.pct_usado ?? "—"} %</b>
            <span className="muted small">{f.usado_mb} de {f.limite_mb} MB · recuperable {f.recuperable_mb} MB</span>
          </div>))}
        {(p.respaldo ?? []).map((r: any) => (
          <div key={r.input_type} className="k"><small>Último {r.input_type} correcto</small><b>hace {r.horas} h</b>
            <span className="muted small">{fecha(r.ultimo_ok)}</span></div>))}
        {p.respaldo && p.respaldo.length === 0 && <div className="k warn"><small>Respaldos RMAN</small><b>ninguno en 30 días</b></div>}
      </div>
      {(dg.destinos?.length > 0 || Object.keys(dg.lag ?? {}).length > 0) && (
        <div className="tbl-wrap mt"><table className="tbl-std">
          <thead><tr><th>Data Guard ({dg.rol ?? "—"})</th><th>Estado</th><th>Gap / desfase</th><th>Error</th></tr></thead>
          <tbody>
            {(dg.destinos ?? []).map((d: any) => (
              <tr key={d.dest_id}><td>{d.dest_name}</td><td>{d.status}</td><td>{d.gap_status ?? "—"}</td><td>{d.error ?? "—"}</td></tr>))}
            {Object.entries(dg.lag ?? {}).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td>—</td><td>{String(v ?? "—")}</td><td>—</td></tr>))}
          </tbody>
        </table></div>
      )}
    </>
  );
}

export function Salud() {
  const ambiente = useAmbiente();
  const inv = useInventario();
  const q = useQuery({ queryKey: ["bd", "salud", ambiente], queryFn: () => api.get<EstadoObj[]>(`/api/estado/bd/salud${ambiente ? "?ambiente=" + ambiente : ""}`), refetchInterval: 30_000 });
  const [sel, setSel] = useState<EstadoObj | null>(null);
  const [filtro, setFiltro] = useState("");
  const refrescar = useRefresco();
  const cat = useCatalogo();
  const puedeRefrescar = cat.data?.find((a) => a.id === "com.refresco")?.permiso === "ejecuta";
  const cls = (p: number | null | undefined) => p == null ? "c-na" : p >= 95 ? "c-crit" : p >= 85 ? "c-warn" : "c-ok";
  const semCls: Record<string, string> = { verde: "c-ok", amarillo: "c-warn", rojo: "c-crit", sin_dato: "c-na" };

  const filas = q.data ?? [];
  const esParcial = (f: EstadoObj) => !f.error && Object.keys(f.datos?.parciales ?? {}).length > 0;
  const conError = filas.filter((f) => f.error);
  const parciales = filas.filter(esParcial);
  const ok = filas.length - conError.length - parciales.length;
  const grupos = Object.entries(conError.reduce<Record<string, string[]>>((acc, f) => {
    const t = tipoError(f.error);
    (acc[t] ??= []).push(f.objeto.split(":")[1]);
    return acc;
  }, {})).sort((a, b) => b[1].length - a[1].length);
  const ultimoIntento = filas.length ? Math.min(...filas.map((f) => f.intento_s ?? f.antiguedad_s)) : null;
  const visibles = filtro === "" ? filas
    : filtro === "PARCIAL" ? parciales
    : filtro === "OK" ? filas.filter((f) => !f.error && !esParcial(f))
    : conError.filter((f) => tipoError(f.error) === filtro);

  return (
    <>
      <Cabecera titulo="Salud de bases de datos"
        sub="Semáforo por base: instancia, espacio real (UNDO activo, TEMP en uso, ASM sin el espejo), protección (FRA, respaldos RMAN, Data Guard), bloqueos e inválidos fuera de la línea base. Al abrir se leen datos actuales; inválidos, a diario y por alerta." />
      <div className="card"><div className="fgrid"><CampoAmbiente id="salud-amb" items={inv.data?.bases ?? []} alCambiar={() => setFiltro("")} /></div></div>
      <ActualizandoVista vista="salud" />
      <Cargando q={q} />
      {q.data && q.data.length === 0 && <div className="card"><Vacio>Sin datos de salud todavía{ambiente ? ` en ${ambiente}` : ""}.</Vacio></div>}
      {filas.length > 0 && (
        <div className="card">
          <div className="btns" style={{ flexWrap: "wrap", gap: 6 }}>
            <button className={"btn sm" + (filtro === "" ? " pri" : "")} onClick={() => setFiltro("")}>Todas ({filas.length})</button>
            <button className={"btn sm" + (filtro === "OK" ? " pri" : "")} onClick={() => setFiltro("OK")}>Con datos ({ok})</button>
            <button className={"btn sm" + (filtro === "PARCIAL" ? " pri" : "")} onClick={() => setFiltro("PARCIAL")}>Parciales ({parciales.length})</button>
            {grupos.map(([t, bs]) => (
              <button key={t} className={"btn sm" + (filtro === t ? " pri" : "")} onClick={() => setFiltro(t)} title={TIPOS_ERROR[t] ?? "Ver el detalle en cada base"}>
                {t === "CIRCUITO" ? "Circuito abierto" : t === "OTRO" ? "Otros errores" : t} ({bs.length})
              </button>
            ))}
          </div>
          <p className="muted small" style={{ marginBottom: 0 }}>
            Último intento de recolección: {ultimoIntento == null ? "—" : <Antiguedad s={ultimoIntento} />}
            {conError.length > 0 && <> · {conError.length} de {filas.length} bases fallan al recolectar: el problema está en la conexión o en la base, no en esta pantalla.</>}
          </p>
          {filtro && TIPOS_ERROR[filtro] && <div className="critbox mt small"><b>{filtro}</b>: {TIPOS_ERROR[filtro]}</div>}
        </div>
      )}
      {visibles.length > 0 && (
        <div className="card tbl-wrap">
          <table className="hm">
            <thead><tr><th>Base</th><th>Ambiente</th><th>Semáforo</th><th>Tablespace máx.</th><th>ASM máx.</th><th title="FRA, último respaldo RMAN y Data Guard">Protección</th><th>Bloqueadas</th><th>Inválidos nuevos</th><th>Último dato</th>{puedeRefrescar && <th />}</tr></thead>
            <tbody>
              {visibles.map((f) => {
                const d = f.datos ?? {};
                const tsMax = d.tablespaces?.length ? Math.max(...d.tablespaces.map((t: any) => Number(t.pct_usado))) : null;
                const dgMax = d.diskgroups?.length ? Math.max(...d.diskgroups.map((t: any) => Number(t.pct_usado))) : null;
                const base = f.objeto.split(":")[1];
                const parcial = Object.keys(d.parciales ?? {});
                const te = tipoError(f.error);
                return (
                  <tr key={f.objeto}>
                    <td className="nm"><b>{base}</b></td>
                    <td className="nm">{f.ambiente || d.ambiente || "—"}</td>
                    <td className={"cell " + (f.error && !f.con_dato ? "c-na" : semCls[d.semaforo] ?? "c-na")} onClick={() => setSel(f)}
                        title={f.error ?? (parcial.length ? `Sin: ${parcial.join(", ")}` : undefined)}>
                      {f.error && !f.con_dato ? `error ${te === "OTRO" ? "" : te}` : d.semaforo}
                      {f.error && f.con_dato && <span className="tag t-crit" style={{ marginLeft: 4 }}>{te}</span>}
                      {!f.error && parcial.length > 0 && <span className="tag t-warn" style={{ marginLeft: 4 }}>parcial</span>}
                    </td>
                    <td className={"cell " + cls(tsMax)} onClick={() => setSel(f)}>{tsMax == null ? "—" : tsMax.toFixed(0) + " %"}</td>
                    <td className={"cell " + cls(dgMax)} onClick={() => setSel(f)}>{dgMax == null ? "—" : dgMax.toFixed(0) + " %"}</td>
                    <td className={"cell " + (semCls[d.proteccion_semaforo] ?? "c-na")} onClick={() => setSel(f)}
                        title={d.proteccion ? undefined : "Se lee al abrir esta vista y en la pasada diaria"}>{resumenProteccion(d.proteccion)}</td>
                    <td className={"cell " + (d.bloqueadas == null ? "c-na" : d.bloqueadas > 5 ? "c-crit" : d.bloqueadas > 0 ? "c-warn" : "c-ok")} onClick={() => setSel(f)}>{d.bloqueadas ?? "—"}</td>
                    <td className={"cell " + (d.invalidos_fuera_lb == null ? "c-na" : d.invalidos_fuera_lb > 0 ? "c-warn" : "c-ok")} onClick={() => setSel(f)}>{d.invalidos_fuera_lb ?? "—"}</td>
                    <td className="nm" title={`Último intento: ${f.intentado ?? f.actualizado ?? "—"}`}><Hace s={f.antiguedad_s} nunca={!f.con_dato} titulo={f.actualizado} /></td>
                    {puedeRefrescar && <td className="nm"><button className="btn sm" title={`Refrescar ${base}`} aria-label={`Refrescar ${base}`} onClick={() => refrescar(base)}>↻<span className="btn-txt"> Refrescar</span></button></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {sel && (
        <Modal titulo={`Salud de ${sel.objeto.split(":")[1]}`} cerrar={() => setSel(null)}>
          {sel.error && (
            <div className="critbox">
              <b>Último intento falló</b>{sel.con_dato ? " (se muestra el último dato bueno)" : " (nunca hubo un dato bueno)"}: {sel.error}
              {TIPOS_ERROR[tipoError(sel.error)] && <div className="mt small">Qué hacer: {TIPOS_ERROR[tipoError(sel.error)]}</div>}
            </div>
          )}
          {Object.keys(sel.datos?.parciales ?? {}).length > 0 && (
            <div className="list mt">
              {Object.entries(sel.datos.parciales as Record<string, string>).map(([k, v]) => (
                <div key={k} className="li"><span className="dot d-warn" /><div className="grow"><b>{k}</b> no disponible: <span className="muted small">{v}</span>
                  {TIPOS_ERROR[tipoError(v)] && <div className="muted small">{TIPOS_ERROR[tipoError(v)]}</div>}</div></div>
              ))}
            </div>
          )}
          {sel.datos?.motivos?.length > 0 && <div className="list mt">{sel.datos.motivos.map((m: string) => <div key={m} className="li"><span className="dot d-warn" />{m}</div>)}</div>}
          {sel.datos?.invalidos != null && (
            <div className="kpis mt">
              <div className={"k " + (sel.datos.invalidos_fuera_lb ? "warn" : "ok")}><small>Inválidos fuera de línea base</small><b>{sel.datos.invalidos_fuera_lb ?? "—"}</b></div>
              <div className="k"><small>En línea base</small><b>{sel.datos.invalidos_en_lb ?? "—"}</b></div>
              <div className="k"><small>Inválidos propios (sin papelera ni esquemas de Oracle)</small><b>{sel.datos.invalidos}</b></div>
            </div>
          )}
          {sel.datos?.rac && <div className="info small mt">Base RAC: {sel.datos.rac.grupo.join(", ")} son la misma base.
            {sel.datos.rac.representante !== sel.datos.base ? ` Espacio, inválidos y bloqueos se leen en ${sel.datos.rac.representante}; aquí solo la instancia.` : " Espacio, inválidos y bloqueos se leen aquí una sola vez para todo el grupo."}</div>}
          {sel.datos?.invalidos_esquemas?.length > 0 && (
            <details className="mt">
              <summary className="small"><b>De dónde sale el número de inválidos</b> <span className="muted">· por esquema (si alguno no debe contar, se agrega a [bd] invalidos_excluir)</span></summary>
              <div className="tbl-wrap mt"><table className="tbl-std">
                <thead><tr><th>Esquema</th><th className="num">Inválidos</th><th className="num">Fuera de línea base</th></tr></thead>
                <tbody>{sel.datos.invalidos_esquemas.map((e: any) => (
                  <tr key={e.owner}><td>{e.owner}</td><td className="num">{e.total}</td><td className="num">{e.fuera_lb ?? "—"}</td></tr>))}</tbody>
              </table></div>
            </details>
          )}
          {sel.datos?.leidas && (
            <p className="muted small mt">Última lectura por sección: {Object.entries(sel.datos.leidas as Record<string, string>)
              .filter(([k]) => k !== "linea_base").map(([k, v]) => `${k} ${fecha(v)}`).join(" · ")}
              {sel.datos.ultima_pasada && <> · última pasada: {sel.datos.ultima_pasada.origen === "evento" ? "alerta de Zabbix" : sel.datos.ultima_pasada.origen}
                ({(sel.datos.ultima_pasada.secciones as string[]).join(", ") || "solo instancia"})</>}</p>
          )}
          {sel.datos?.proteccion && <Proteccion p={sel.datos.proteccion} />}
          {sel.datos?.instancia && <><h3 className="small mt">Instancia</h3><TablaFilas filas={[sel.datos.instancia]} /></>}
          {sel.datos?.tablespaces?.length > 0 && <><h3 className="small mt">Tablespaces</h3><TablaFilas filas={sel.datos.tablespaces} /></>}
          {sel.datos?.diskgroups?.length > 0 && <><h3 className="small mt">Diskgroups</h3><TablaFilas filas={sel.datos.diskgroups} /></>}
          <p className="muted small mt">Último dato: {sel.con_dato ? sel.actualizado : "nunca"} · último intento: {sel.intentado ?? sel.actualizado} · origen {sel.origen}</p>
        </Modal>
      )}
    </>
  );
}

// ======================================================================== TAREAS DBA
const OTRAS_PANTALLAS = new Set(["bd.snap.tomar", "bd.snap.comparar", "bd.inv.recompilar", "bd.ses.bloqueos", "bd.ses.flota",
  "bd.ses.terminar", "bd.salud.flota", "bd.rep.hoja_vida", "bd.rep.analisis", "bd.rep.diskgroups", "bd.rep.sql_costosas",
  "bd.rep.programar"]);
const GRUPOS_BD: [string, string][] = [["bd.inv", "Inválidos"], ["bd.lb", "Línea base"], ["bd.ses", "Sesiones"], ["bd.salud", "Salud"],
  ["bd.usr", "Cuentas"], ["bd.ts", "Espacio"], ["bd.asm", "Espacio"], ["bd.stats", "Mantenimiento"], ["bd.job", "Mantenimiento"]];

export function Tareas() {
  const cat = useCatalogo();
  const [abierta, setAbierta] = useState<string | null>(null);
  const [verTodas, setVerTodas] = useState(false);
  const tareas = (cat.data ?? []).filter((a) => a.motor === "bd" && !OTRAS_PANTALLAS.has(a.id) && (verTodas || a.estado === "implementada"));
  const grupos = new Map<string, typeof tareas>();
  for (const t of tareas) {
    const g = GRUPOS_BD.find(([p]) => t.id.startsWith(p + "."))?.[1] ?? "Otras";
    grupos.set(g, [...(grupos.get(g) ?? []), t]);
  }
  const acc = tareas.find((t) => t.id === abierta);
  return (
    <>
      <Cabecera titulo="Tareas DBA" sub="Procedimientos del catálogo. Cada uno corre por el paquete controlado ADMINBD.PKG_OPS_CENTRAL, con auditoría.">
        <label className="chk" style={{ marginTop: 0 }}><input type="checkbox" checked={verTodas} onChange={(e) => setVerTodas(e.target.checked)} /> Mostrar también las catalogadas</label>
      </Cabecera>
      <Cargando q={cat} />
      {[...grupos.entries()].map(([g, ts]) => (
        <div key={g} className="tcat">
          <h4>{g}</h4>
          <div className="tgrid">
            {ts.map((t) => (
              <button key={t.id} className="tcard" onClick={() => setAbierta(t.id)} style={t.estado !== "implementada" ? { opacity: 0.6 } : undefined}>
                <div className="fila entre"><Clase c={t.clase} />{t.permiso === "ve" && <span className="tag">solo ver</span>}</div>
                <b>{t.nombre}</b>
                <span className="mono-sm">{t.id}{t.estado !== "implementada" ? " · catalogada" : ""}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      {acc && (
        <Modal titulo={acc.nombre} cerrar={() => setAbierta(null)}>
          <Accion accion={acc.id} compacto />
        </Modal>
      )}
    </>
  );
}

// ======================================================================== APROBACIONES
export function Aprobaciones() {
  const { avisar } = useSesion();
  const qc = useQueryClient();
  const pendQ = useQuery({ queryKey: ["pendientes"], queryFn: () => api.get<Ejecucion[]>("/api/aprobaciones/pendientes"), refetchInterval: 20_000 });
  const enAmb = useEnAmbiente();
  const q = { ...pendQ, data: pendQ.data?.filter((e) => enAmb(e.ambiente)) };
  const conSegundoFactor = useSegundoFactor();
  const [coment, setComent] = useState<Record<string, string>>({});
  const [ocupado, setOcupado] = useState("");

  async function resolver(e: Ejecucion, accion: "aprobar" | "rechazar") {
    setOcupado(e.id);
    try {
      const r = await conSegundoFactor(() => api.post(`/api/aprobaciones/${e.id}/${accion}`, { comentario: coment[e.id] ?? "" }));
      if (!r) return;
      avisar(accion === "aprobar" ? "Aprobada: pasa a la cola de ejecución." : "Rechazada.");
      qc.invalidateQueries();
    } catch (err) {
      avisar(mensajeError(err));
    } finally {
      setOcupado("");
    }
  }

  return (
    <>
      <Cabecera titulo="Aprobaciones" sub="Solicitudes de otras personas que tu perfil puede aprobar. Nadie aprueba lo que pidió." />
      <div className="card"><div className="fgrid"><CampoAmbiente id="apr-amb" /></div></div>
      <Cargando q={pendQ} />
      {q.data?.length === 0 && <div className="card"><Vacio>No tienes aprobaciones pendientes en este ambiente.</Vacio></div>}
      {q.data?.map((e) => (
        <div key={e.id} className="card">
          <h3><span className="mono-sm">{e.accion}</span><span className="fila"><Clase c={e.clase} /><span className="tag">{e.ambiente}</span></span></h3>
          <div className="kv">
            <span>Destino</span><b>{e.destino}</b>
            <span>Pedido por</span><b>{e.solicitante} · {fecha(e.creada)}</b>
            <span>Caso</span><b>{e.cambio ?? "—"}</b>
            <span>Motivo</span><b>{e.motivo ?? "—"}</b>
            <span>Huella</span><b className="mono-sm" title="Lo que apruebas: si algo cambia, la ejecución no corre">{e.huella.slice(0, 16)}…</b>
          </div>
          <pre className="console json mt">{JSON.stringify(e.params, null, 2)}</pre>
          <div className="fila mt">
            <input className="f" style={{ flex: 1, minWidth: 200 }} placeholder="Comentario (opcional)" value={coment[e.id] ?? ""}
                   onChange={(x) => setComent((p) => ({ ...p, [e.id]: x.target.value }))} />
            <button className="btn green" disabled={ocupado === e.id} onClick={() => resolver(e, "aprobar")}>Aprobar</button>
            <button className="btn" disabled={ocupado === e.id} onClick={() => resolver(e, "rechazar")}>Rechazar</button>
          </div>
        </div>
      ))}
    </>
  );
}

// ======================================================================== EJECUCIONES
const ESTADOS = ["", "pendiente_aprobacion", "en_cola", "ejecutando", "exitosa", "fallida", "rechazada", "cancelada", "vencida"];

export function Ejecuciones() {
  const [estado, setEstado] = useState("");
  const [cambio, setCambio] = useState("");
  // ?id=<ejecución> (desde el Inicio) abre directamente su detalle
  const [sel, setSel] = useState<string | null>(() => new URLSearchParams(window.location.search).get("id"));
  const ambiente = useAmbiente();
  const qs = new URLSearchParams({ ...(estado ? { estado } : {}), ...(cambio ? { cambio } : {}), ...(ambiente ? { ambiente } : {}) }).toString();
  const q = useQuery({ queryKey: ["ejecuciones", estado, cambio, ambiente], queryFn: () => api.get<Ejecucion[]>(`/api/ejecuciones${qs ? "?" + qs : ""}`), refetchInterval: 15_000 });
  return (
    <>
      <Cabecera titulo="Log de ejecuciones" sub="Todo lo que se ejecutó: quién, dónde, con qué caso (si lo hubo) y qué pasó. Cada ejecución cerrada deja evidencia con su SHA-256." />
      <div className="card">
        <div className="fgrid" style={{ marginBottom: 12 }}>
          <CampoAmbiente id="ej-amb" />
          <label className="lb">Estado
            <SelectBuscable className="f" value={estado} onChange={(e) => setEstado(e.target.value)}>
              {ESTADOS.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ") : "Todos"}</option>)}
            </SelectBuscable>
          </label>
          <label className="lb">Caso / referencia
            <input className="f" value={cambio} placeholder="OPS-123…" onChange={(e) => setCambio(e.target.value.toUpperCase().trim())} />
          </label>
        </div>
        <Cargando q={q} />
        {q.data?.length === 0 && <Vacio>Sin ejecuciones con ese filtro.</Vacio>}
        {q.data && q.data.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl-std">
              <thead><tr><th>Creada</th><th>Acción</th><th>Destino · ambiente</th><th>Clase</th><th>Ejecutó</th><th>Caso</th><th>Motivo</th><th>Estado</th></tr></thead>
              <tbody>
                {q.data.map((e) => (
                  <tr key={e.id} className={"click" + (sel === e.id ? " sel" : "")} onClick={() => setSel(e.id)}>
                    <td>{fecha(e.creada)}</td><td className="mono-sm nw">{e.accion}</td><td>{e.destino} <span className="muted small">{e.ambiente}</span></td>
                    <td><Clase c={e.clase} /></td><td>{e.solicitante}</td><td>{e.cambio ?? "—"}</td><td className="small">{(e.motivo ?? "—").slice(0, 60)}</td><td><EstadoEj e={e.estado} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {sel && <DetalleEjecucion id={sel} cerrar={() => setSel(null)} />}
    </>
  );
}

function DetalleEjecucion({ id, cerrar }: { id: string; cerrar: () => void }) {
  const { yo, avisar } = useSesion();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ejecucion", id], queryFn: () => api.get<Ejecucion>(`/api/ejecuciones/${id}`), refetchInterval: 5_000 });
  const [evid, setEvid] = useState<any>(null);
  const e = q.data;
  const activa = e && ["en_cola", "ejecutando"].includes(e.estado);

  async function verEvidencia() {
    try { setEvid(await api.get(`/api/ejecuciones/${id}/evidencia`)); } catch (x) { avisar(mensajeError(x)); }
  }
  async function cancelar() {
    if (!window.confirm("¿Cancelar esta ejecución?")) return;
    try { await api.post(`/api/ejecuciones/${id}/cancelar`); qc.invalidateQueries(); } catch (x) { avisar(mensajeError(x)); }
  }

  return (
    <Modal titulo={e ? e.accion : "Ejecución"} cerrar={cerrar}
           pie={<>
             {e && e.solicitante === yo.login && ["pendiente_aprobacion", "en_cola"].includes(e.estado) && <button className="btn" onClick={cancelar}>Cancelar ejecución</button>}
             {e?.evidencia && <button className="btn" onClick={verEvidencia}>Ver evidencia</button>}
             <button className="btn pri" onClick={cerrar}>Cerrar</button>
           </>}>
      <Cargando q={q} />
      {e && (
        <>
          <div className="kv">
            <span>Id</span><b className="mono-sm">{e.id}</b>
            <span>Estado</span><b><EstadoEj e={e.estado} /></b>
            <span>Destino</span><b>{e.destino} ({e.ambiente})</b>
            <span>Clase</span><b><Clase c={e.clase} /></b>
            <span>Ejecutó</span><b>{e.solicitante} · {fecha(e.creada)}</b>
            <span>Autorización</span><b>{e.aprobador && !e.aprobador.startsWith("politica:") ? `aprobada por ${e.aprobador}` : "ejecución directa (permiso de ejecución)"}{e.comentario ? ` · “${e.comentario}”` : ""}</b>
            <span>Caso / motivo</span><b>{e.cambio ?? "sin caso"} · {e.motivo ?? "—"}</b>
            <span>Inicio / fin</span><b>{fecha(e.iniciada)} → {fecha(e.terminada)}</b>
            <span>Origen</span><b>{e.origen}</b>
          </div>
          <p className="small muted mt">Parámetros</p>
          <pre className="console json">{JSON.stringify(e.params, null, 2)}</pre>
          {e.estado !== "pendiente_aprobacion" && <><p className="small muted mt">Salida</p><Consola key={e.id + String(activa)} id={e.id} /></>}
          {e.error && <div className="critbox mt">{e.error}</div>}
          {e.resultado && <><p className="small muted mt">Resultado</p><Resultado r={e.resultado} /></>}
          {["exitosa", "fallida"].includes(e.estado) && <ComentarEnJira ejecucionId={e.id} claveInicial={e.cambio} />}
          {evid && (
            <div className="mt">
              <div className={evid.integra ? "info" : "critbox"}>
                Evidencia {evid.integra ? "íntegra" : "ALTERADA"} · SHA-256 <span className="mono-sm">{evid.sha256}</span>
              </div>
              <pre className="console json mt">{JSON.stringify(evid.contenido, null, 2)}</pre>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

// ======================================================================== ADMINISTRACIÓN
export function Admin() {
  const { yo } = useSesion();
  const cat = useCatalogo();
  const puedeVerificar = yo.perfiles.some((p) => p === "lid" || p === "adm");
  const aud = useQuery({ queryKey: ["auditoria"], enabled: puedeVerificar, queryFn: () => api.get<any>("/api/auditoria/verificar") });
  const resumen = new Map<string, { total: number; impl: number }>();
  for (const a of cat.data ?? []) {
    const r = resumen.get(a.motor) ?? { total: 0, impl: 0 };
    r.total++;
    if (a.estado === "implementada") r.impl++;
    resumen.set(a.motor, r);
  }
  return (
    <>
      <Cabecera titulo="Administración" sub="Integridad de la auditoría y estado del catálogo de acciones." />
      <div className="g2">
        <div className="card">
          <h3>Cadena de auditoría</h3>
          {!puedeVerificar ? <Vacio>Solo Líder o Administrador verifican la cadena.</Vacio> : <Cargando q={aud} />}
          {aud.data && (
            <div className={aud.data.integra ? "info" : "critbox"}>
              {aud.data.integra ? "Íntegra" : "ROTA"}: {aud.data.registros} registros. {aud.data.mensaje}
            </div>
          )}
          {aud.data && <button className="btn mt" onClick={() => aud.refetch()}>Verificar de nuevo</button>}
        </div>
        <div className="card">
          <h3>Catálogo visible para tu perfil</h3>
          <TablaFilas filas={[...resumen.entries()].map(([motor, r]) => ({ motor, acciones: r.total, implementadas: r.impl }))} />
        </div>
      </div>
    </>
  );
}

// ======================================================================== VENTANA DE CAMBIO E INCIDENTES
export { Ventana } from "./Ventana";
export { Incidentes } from "./Incidentes";
// ======================================================================== REPORTES (HTML generados por script)
// Diseño: demo-ops-central.html (V.reportes / App.report). Los textos, el alcance y las etapas
// vienen de /api/reportes/tipos; las etapas se marcan con las líneas "Etapa i/n: …" del script.
type ReporteGen = {
  ejecucion: string; accion: string; tipo: string; destino: string; ambiente: string; solicitante: string;
  creada: string; nombre: string; bytes: number; sha256: string; estado?: string; muestra: boolean;
};
type TipoReporte = {
  accion: string; nombre: string; skill: string; descripcion: string; alcance: "base" | "ambiente";
  script: string; etapas: string[];
};
const ESTADO_CLASE: Record<string, string> = { CRITICAL: "t-crit", WARNING: "t-warn", OK: "t-ok" };

export function Reportes() {
  const cat = useCatalogo();
  const tipos = useQuery({ queryKey: ["reportes-tipos"], queryFn: () => api.get<TipoReporte[]>("/api/reportes/tipos"), staleTime: 300_000 });
  const ambiente = useAmbiente();
  const inv = useInventario();
  const q = useQuery({ queryKey: ["reportes", ambiente], queryFn: () => api.get<ReporteGen[]>(`/api/reportes${ambiente ? "?ambiente=" + ambiente : ""}`), refetchInterval: 30_000 });
  const [abierto, setAbierto] = useState<string | null>(null);
  const programar = cat.data?.find((a) => a.id === "bd.rep.programar");
  const programable = programar?.estado === "implementada" && programar.permiso === "ejecuta";
  const lista = (tipos.data ?? []).filter((t) => cat.data?.some((a) => a.id === t.accion));
  const sel = lista.find((t) => t.accion === abierto);
  return (
    <>
      <Cabecera titulo="Reportes"
        sub="Las skills de reportería del kit Oracle, ejecutables con un botón. Solo lectura; se construyen por etapas." />
      <Cargando q={tipos} />
      <div className="g2">
        {lista.map((t) => {
          const a = cat.data?.find((x) => x.id === t.accion);
          const puede = a?.permiso === "ejecuta" && a.estado === "implementada";
          return (
            <div key={t.accion} className="card">
              <h3>{t.nombre} <span className="tag t-bd mono">{t.skill}</span></h3>
              <p className="muted small">{t.descripcion}</p>
              <div className="btns mt">
                {puede ? (
                  <>
                    <button className="btn pri sm" onClick={() => setAbierto(t.accion)}>Generar</button>
                    <button className="btn sm" disabled={!programable}
                            title={programable ? undefined : "Catalogada: la programación se implementa en una etapa posterior"}>
                      Programar
                    </button>
                  </>
                ) : <span className="muted small">Su perfil puede descargar reportes generados.</span>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="card mt">
        <h3>Historial</h3>
        <div className="fgrid" style={{ marginBottom: 12 }}><CampoAmbiente id="hist-amb" items={inv.data?.bases ?? []} /></div>
        <Cargando q={q} />
        {q.data && q.data.length === 0 && <Vacio>Todavía no hay reportes generados{ambiente ? ` en ${ambiente}` : ""}.</Vacio>}
        {q.data && q.data.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl-std">
              <thead><tr><th>Reporte</th><th>Alcance</th><th>Generado por</th><th></th></tr></thead>
              <tbody>
                {q.data.map((r) => (
                  <tr key={r.ejecucion}>
                    <td>
                      <a href={`/api/ejecuciones/${r.ejecucion}/reporte`} target="_blank" rel="noopener" title={`${r.nombre} · SHA-256 ${r.sha256}`}>
                        {tipos.data?.find((t) => t.accion === r.accion)?.nombre ?? r.tipo}
                      </a>
                      {r.estado && <span className={"tag " + (ESTADO_CLASE[r.estado] ?? "")} style={{ marginLeft: 6 }}>{r.estado}</span>}
                      {r.muestra && <span className="tag" style={{ marginLeft: 6 }}>muestra</span>}
                    </td>
                    <td>{r.destino.startsWith("ambiente:") ? `Ambiente ${r.ambiente}` : `${r.destino} · ambiente ${r.ambiente}`} · {fecha(r.creada)}</td>
                    <td>{r.solicitante}</td>
                    <td><a className="btn sm" href={`/api/ejecuciones/${r.ejecucion}/reporte?descargar=true`} download={r.nombre}>⬇ HTML</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {sel && <GenerarReporte t={sel} cerrar={() => setAbierto(null)} />}
    </>
  );
}

const RE_ETAPA = /^Etapa (\d+)\/(\d+): /;
const TODAS = "__todas__";
type EstadoLote = { ejecucion: string; base: string; estado: string; terminal: boolean; error?: string | null; reporte: boolean };
type FilaLote = { base: string; ejecucion: string | null; estado: string; error: string | null };
const NOMBRE_EXTRA: Record<string, string> = { criterio: "Criterio", umbral: "Umbral de llenado (%)" };

function GenerarReporte({ t, cerrar }: { t: TipoReporte; cerrar: () => void }) {
  const acc = useAccion(t.accion);
  const { avisar } = useSesion();
  const qc = useQueryClient();
  const conSegundoFactor = useSegundoFactor();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const [alcance, setAlcance] = useState("");
  const [extra, setExtra] = useState<Record<string, string>>({});
  const [ejec, setEjec] = useState<string | null>(null);
  const [etapa, setEtapa] = useState(0); // última etapa anunciada por el script (1..n); 0 = ninguna
  const [fin, setFin] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [ocupado, setOcupado] = useState(false);
  // Lote "todas las bases del ambiente": una ejecución por base y un ZIP al final.
  const [lote, setLote] = useState<FilaLote[] | null>(null);
  const [estLote, setEstLote] = useState<EstadoLote[]>([]);
  const [zipBajado, setZipBajado] = useState(false);

  const bases = inv.data?.bases ?? [];
  const enAmb = useEnAmbiente();
  const ambienteVista = useAmbiente();
  const basesAmb = ambienteVista ? bases.filter((b) => b.ambiente === ambienteVista) : [];
  // Todos los ambientes del catálogo que pasan el filtro de la vista; los que no tienen bases en el
  // inventario se muestran deshabilitados (fallarían al generar).
  const ambientes = (acc?.parametros.ambiente?.valores ?? []).filter((a) => enAmb(a));
  // Parámetros propios del reporte además del alcance (criterio de sentencias, umbral de diskgroups).
  const otros = Object.entries(acc?.parametros ?? {}).filter(([k]) => k !== "base" && k !== "ambiente");

  useEffect(() => {
    if (!ejec) return;
    const es = new EventSource(`/api/ejecuciones/${ejec}/eventos`);
    es.addEventListener("linea", (ev) => {
      const m = RE_ETAPA.exec(JSON.parse((ev as MessageEvent).data).texto ?? "");
      if (m) setEtapa((p) => Math.max(p, Number(m[1])));
    });
    es.addEventListener("fin", async (ev) => {
      const estado = JSON.parse((ev as MessageEvent).data).estado as string;
      es.close();
      setFin(estado);
      if (estado !== "exitosa") {
        const e = await api.get<Ejecucion>(`/api/ejecuciones/${ejec}`).catch(() => null);
        setError(e?.error || `La ejecución terminó en estado ${estado}.`);
      } else avisar("Reporte generado: queda en el historial.");
      qc.invalidateQueries({ queryKey: ["reportes"] });
      qc.invalidateQueries({ queryKey: ["ejecuciones"] });
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [ejec]); // eslint-disable-line react-hooks/exhaustive-deps

  const idsLote = (lote ?? []).map((x) => x.ejecucion).filter((x): x is string => !!x);
  const urlZip = `/api/reportes/zip?ids=${idsLote.join(",")}`;
  const terminadas = estLote.filter((x) => x.terminal).length;
  const exitosas = estLote.filter((x) => x.estado === "exitosa" && x.reporte).length;
  const loteFin = !!lote && (idsLote.length === 0 || (estLote.length >= idsLote.length && terminadas >= idsLote.length));
  const loteEnCurso = !!lote && !loteFin;

  useEffect(() => {
    if (!lote || !idsLote.length || loteFin) return;
    let vivo = true;
    const leer = async () => {
      const r = await api.get<EstadoLote[]>(`/api/reportes/lote/estado?ids=${idsLote.join(",")}`).catch(() => null);
      if (vivo && r) setEstLote(r);
    };
    leer();
    const tm = setInterval(leer, 3000);
    return () => { vivo = false; clearInterval(tm); };
  }, [lote, loteFin]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!loteFin || zipBajado) return;
    setZipBajado(true);
    qc.invalidateQueries({ queryKey: ["reportes"] });
    qc.invalidateQueries({ queryKey: ["ejecuciones"] });
    if (exitosas > 0) {
      const a = document.createElement("a");
      a.href = urlZip;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
      avisar(`Lote terminado: ${exitosas} de ${(lote ?? []).length} reportes en el ZIP.`);
    } else setError("Ninguna base generó el reporte: revisa el detalle de cada una abajo.");
  }, [loteFin]); // eslint-disable-line react-hooks/exhaustive-deps

  async function generarLote() {
    const params: Record<string, unknown> = {};
    for (const [k, esp] of otros) {
      const x = extra[k] ?? (esp.defecto === undefined || esp.defecto === null ? "" : String(esp.defecto));
      if (x !== "") params[k] = esp.tipo === "entero" ? Number(x) : x;
    }
    setOcupado(true);
    try {
      const r = await conSegundoFactor(() => api.post<{ ejecuciones: FilaLote[] }>("/api/reportes/lote",
        { accion: t.accion, ambiente: ambienteVista, params }));
      if (!r) return;
      setEstLote([]);
      setZipBajado(false);
      setLote(r.ejecuciones);
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setOcupado(false);
    }
  }

  async function generar() {
    setError("");
    if (!alcance) {
      setError(t.alcance === "ambiente" ? "Elige el ambiente." : "Elige la base.");
      return;
    }
    if (alcance === TODAS) return generarLote();
    const params: Record<string, unknown> = { [t.alcance === "ambiente" ? "ambiente" : "base"]: alcance };
    for (const [k, esp] of otros) {
      const x = extra[k] ?? (esp.defecto === undefined || esp.defecto === null ? "" : String(esp.defecto));
      if (x !== "") params[k] = esp.tipo === "entero" ? Number(x) : x;
    }
    setOcupado(true);
    try {
      const r = await conSegundoFactor(() => api.post<Ejecucion>(`/api/acciones/${t.accion}/ejecuciones`, { params, cambio: null, motivo: null }));
      if (!r) return;
      setEtapa(0);
      setFin(null);
      setEjec(r.id);
    } catch (e) {
      setError(mensajeError(e));
    } finally {
      setOcupado(false);
    }
  }

  const enCurso = (!!ejec && !fin) || loteEnCurso;
  const punto = (i: number) => {
    if (fin === "exitosa") return "d-ok";
    if (!ejec) return "d-off";
    if (i + 1 < etapa) return "d-ok";
    if (i + 1 === etapa) return fin ? "d-crit" : "d-warn";
    return "d-off";
  };
  return (
    <Modal titulo={`Generar · ${t.nombre}`} cerrar={enCurso ? () => undefined : cerrar}
      pie={<>
        <button className="btn" onClick={cerrar} disabled={enCurso}>{fin ? "Cerrar" : "Cancelar"}</button>
        {lote ? (
          loteFin && exitosas > 0
            ? <a className="btn pri" href={urlZip} download>⬇ Descargar ZIP ({exitosas})</a>
            : <button className="btn pri" disabled={loteEnCurso || ocupado} onClick={() => { setLote(null); setEstLote([]); setError(""); }}>
                {loteEnCurso ? `Generando… ${terminadas}/${idsLote.length}` : "Volver"}
              </button>
        ) : fin === "exitosa" && ejec ? (
          <a className="btn pri" href={`/api/ejecuciones/${ejec}/reporte`} target="_blank" rel="noopener">Abrir reporte</a>
        ) : (
          <button className="btn pri" onClick={generar} disabled={ocupado || enCurso}>
            {enCurso ? "Generando…" : fin ? "Reintentar" : alcance === TODAS ? `Generar ${basesAmb.length} reportes (ZIP)` : "Generar"}
          </button>
        )}
      </>}>
      <p className="muted">Skill <code>{t.skill}</code> · construcción por etapas · solo lectura</p>
      <div className="fgrid mt">
        {t.alcance === "base" && !enCurso && <CampoAmbiente id="rep-amb" items={bases} alCambiar={() => setAlcance("")} />}
        <label className="lb">Alcance
          <SelectBuscable className="f" value={alcance} disabled={enCurso} onChange={(e) => setAlcance(e.target.value)}
            placeholder={t.alcance === "base" ? "Escribe base o servidor (ej. tron)…" : undefined}>
            <option value="">— elegir —</option>
            {t.alcance === "ambiente"
              ? ambientes.map((a) => {
                  const nb = bases.filter((b) => b.ambiente === a).length;
                  return <option key={a} value={a} disabled={!nb}>Ambiente {a} ({nb ? `${nb} bases` : "sin bases en el inventario"})</option>;
                })
              : <>
                  {ambienteVista
                    ? <option value={TODAS} disabled={!basesAmb.length}>★ Todas las bases de {ambienteVista} ({basesAmb.length}) · ZIP</option>
                    : <option value={TODAS} disabled>★ Todas las bases (elige primero un ambiente)</option>}
                  <OpcionesActivo items={bases} />
                </>}
          </SelectBuscable>
        </label>
        <label className="lb">Formato
          <select className="f" disabled><option>HTML interactivo</option></select>
        </label>
        {otros.map(([k, esp]) => {
          const val = extra[k] ?? (esp.defecto === undefined || esp.defecto === null ? "" : String(esp.defecto));
          const poner = (x: string) => setExtra((p) => ({ ...p, [k]: x }));
          return (
            <label key={k} className="lb">{NOMBRE_EXTRA[k] ?? k}
              {esp.tipo === "enum" ? (
                <SelectBuscable className="f" disabled={enCurso} value={val} onChange={(e) => poner(e.target.value)}>
                  {(esp.valores ?? []).map((o) => <option key={o}>{o}</option>)}
                </SelectBuscable>
              ) : (
                <input className="f" type={esp.tipo === "entero" ? "number" : "text"} min={esp.min} max={esp.max}
                       disabled={enCurso} value={val} onChange={(e) => poner(e.target.value)} />
              )}
            </label>
          );
        })}
      </div>
      {t.alcance === "base" && (
        <p className="muted small" style={{ marginBottom: 0 }}>
          Este reporte se genera por base. Con «Todas las bases» se pide uno por cada base del ambiente
          y al terminar se descarga un ZIP con los HTML y un <code>indice.csv</code> con el resultado de cada base.
        </p>
      )}
      {lote ? (
        <div className="mt">
          <p className="small">
            {loteFin ? "Lote terminado" : "Generando"} · {terminadas} de {idsLote.length} terminadas · {exitosas} con reporte
            {lote.length > idsLote.length && ` · ${lote.length - idsLote.length} no se pudieron pedir`}
          </p>
          <div style={{ height: 6, background: "var(--line, #ddd)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${idsLote.length ? Math.round((terminadas / idsLote.length) * 100) : 100}%`,
                          background: "var(--acc, #2b6cb0)", transition: "width .3s" }} />
          </div>
          <div className="list mt" style={{ maxHeight: 220, overflow: "auto" }}>
            {lote.map((f) => {
              const e = estLote.find((x) => x.ejecucion === f.ejecucion);
              const estado = e?.estado ?? f.estado;
              const clase = estado === "exitosa" ? "d-ok" : ["fallida", "rechazada", "cancelada", "vencida"].includes(estado) ? "d-crit" : "d-warn";
              const err = e?.error ?? f.error;
              return (
                <div key={f.base} className="li">
                  <span className={"dot " + clase} />
                  <div className="grow"><b>{f.base}</b> <span className="muted small">{estado}</span>
                    {err && <div className="muted small" title={err}>{err.length > 140 ? err.slice(0, 140) + "…" : err}</div>}
                  </div>
                  {estado === "exitosa" && f.ejecucion &&
                    <a className="btn sm" href={`/api/ejecuciones/${f.ejecucion}/reporte`} target="_blank" rel="noopener">Ver</a>}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
      <div className="list mt">
        {t.etapas.map((s, i) => (
          <div key={s} className="li"><span className={"dot " + punto(i)} /><div className="grow">{s}</div></div>
        ))}
      </div>
      )}
      {enCurso && !lote && (
        <p className="muted small">Etapa {Math.max(etapa, 1)} de {t.etapas.length} · ejecución <span className="mono-sm">{ejec}</span></p>
      )}
      {error && <div className="critbox mt">{error}</div>}
    </Modal>
  );
}
