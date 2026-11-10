import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mensajeError } from "./api";
import Accion from "./Accion";
import {
  Antiguedad, Cabecera, Cargando, Clase, Consola, EstadoEj, Modal, Resultado, TablaFilas, Vacio, fecha,
  useCatalogo, useSegundoFactor, useSesion,
} from "./comun";
import type { Ejecucion, EstadoObj, Inventario } from "./tipos";

// ======================================================================== INICIO
export function Inicio() {
  const nav = useNavigate();
  const { yo } = useSesion();
  const q = useQuery({ queryKey: ["inicio"], queryFn: () => api.get<any>("/api/inicio"), refetchInterval: 30_000 });
  if (!q.data) return <Cargando q={q} />;
  const k = q.data.kpis;
  const sem = k.bases ?? {};
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
          <h3>Últimas ejecuciones</h3>
          {q.data.ultimas.length === 0 ? <Vacio>Todavía no hay ejecuciones.</Vacio> : (
            <div className="list">
              {q.data.ultimas.map((e: Ejecucion) => (
                <div key={e.id} className="li">
                  <div className="grow"><b className="mono-sm">{e.accion}</b>
                    <div className="muted small">{e.destino} · {e.solicitante} · {fecha(e.creada)}</div></div>
                  <EstadoEj e={e.estado} />
                </div>
              ))}
            </div>
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
export function InventarioP() {
  const q = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario") });
  const [tab, setTab] = useState<"servidores" | "dominios" | "bases">("servidores");
  return (
    <>
      <Cabecera titulo="Inventario" sub="Servidores, dominios WebLogic y bases, con sus relaciones. Fuente: inventario de Ops Central." />
      <div className="card">
        <div className="tabs">
          {(["servidores", "dominios", "bases"] as const).map((t) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)} {q.data && <span className="muted">({q.data[t].length})</span>}
            </button>
          ))}
        </div>
        <Cargando q={q} />
        {q.data && <TablaFilas filas={q.data[tab].map((x: any) => ({ ...x, relacionados: x.relacionados.join(", ") }))} />}
      </div>
    </>
  );
}

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
      {faltan.length > 0 && <div className="info mt">Hosts con problemas que no están en el inventario: {faltan.join(", ")}.</div>}
      {c?.datos?.faltan_en_zabbix?.length > 0 && <div className="info mt">Servidores del inventario que no existen en Zabbix: {c.datos.faltan_en_zabbix.join(", ")}.</div>}
    </div>
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
      {filas.length > 0 && (
        <div className="g21">
          <div className="card">
            <div className="tbl-wrap">
              <table>
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

// ======================================================================== CAPA MEDIA
function PantallaAcciones({ titulo, sub, acciones, conHost = true }: { titulo: string; sub: string; acciones: string[]; conHost?: boolean }) {
  const cat = useCatalogo();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const disponibles = acciones.filter((a) => cat.data?.some((c) => c.id === a));
  const [sel, setSel] = useState<string>("");
  const [host, setHost] = useState("");
  const [dominios, setDominios] = useState("");
  const actual = sel || disponibles[0];
  const servidores = (inv.data?.servidores ?? []).filter((s) => !s.equipo || s.equipo === "cm");
  const doms = (inv.data?.dominios ?? []).filter((d) => !host || d.servidor === host);
  return (
    <>
      <Cabecera titulo={titulo} sub={sub} />
      <Cargando q={cat} />
      {conHost && (
        <div className="card">
          <div className="fgrid">
            <label className="lb">Servidor
              <select className="f" value={host} onChange={(e) => { setHost(e.target.value); setDominios(""); }}>
                <option value="">— elegir —</option>
                {servidores.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.ambiente}</option>)}
              </select>
            </label>
            <label className="lb">Dominios (vacío = todos)
              <select className="f" value={dominios} onChange={(e) => setDominios(e.target.value)} disabled={!host}>
                <option value="">Todos los del servidor</option>
                {doms.map((d) => <option key={d.id} value={d.id}>{d.id}</option>)}
              </select>
            </label>
          </div>
        </div>
      )}
      <div className="card">
        <div className="tabs">
          {disponibles.map((a) => {
            const c = cat.data!.find((x) => x.id === a)!;
            return <button key={a} className={actual === a ? "on" : ""} onClick={() => setSel(a)}>{c.nombre.split("(")[0]}</button>;
          })}
        </div>
        {actual && (!conHost || host) ? (
          <Accion key={actual + host + dominios} accion={actual} compacto
                  iniciales={conHost ? { host, ...(dominios ? { dominios } : {}) } : {}} ocultos={["hosts"]} />
        ) : <Vacio>Elige el servidor para continuar.</Vacio>}
        {actual && <div className="fila mt small muted"><Clase c={cat.data!.find((x) => x.id === actual)!.clase} /> <span className="mono-sm">{actual}</span></div>}
      </div>
    </>
  );
}

export const Orquestacion = () => (
  <PantallaAcciones titulo="Orquestación WebLogic" sub="Estado, bajada y subida ordenada de dominios, con snapshot previo de Managed Servers."
    acciones={["orq.status", "orq.verificar", "orq.inventory", "orq.stop", "orq.start", "orq.credenciales", "rec.recolectar", "sys.conectividad"]} />
);
export const UsuariosWL = () => (
  <PantallaAcciones titulo="Usuarios WebLogic" sub="Listar, preparar el dominio y aplicar altas, bajas y grupos en el realm."
    acciones={["usr.listar", "usr.preparar", "usr.aplicar"]} />
);
export const Frmweb = () => (
  <PantallaAcciones titulo="Procesos frmweb" sub="Detectar procesos huérfanos (sin sesión) y mitigarlos por lotes."
    acciones={["frm.validar", "frm.mitigar"]} />
);

// ======================================================================== SNAPSHOT DE INVÁLIDOS
export function Snapshot() {
  const qc = useQueryClient();
  const inv = useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
  const [base, setBase] = useState("");
  const [cambio, setCambio] = useState("");
  const listo = base && /^[A-Za-z0-9][A-Za-z0-9_\-/.]{2,39}$/.test(cambio);
  const comp = useQuery({
    queryKey: ["snapshot", base, cambio], enabled: Boolean(listo), retry: false,
    queryFn: () => api.get<EstadoObj>(`/api/estado/bd/${base}/snapshot/${cambio}`),
  });
  const refrescar = () => qc.invalidateQueries({ queryKey: ["snapshot", base, cambio] });
  const c = comp.data?.datos;
  return (
    <>
      <Cabecera titulo="Snapshot de inválidos" sub="Antes y después de un despliegue: qué objetos quedaron inválidos por el cambio, sin confundirlos con la línea base." />
      <div className="card">
        <div className="fgrid">
          <label className="lb">Base
            <select className="f" value={base} onChange={(e) => setBase(e.target.value)}>
              <option value="">— elegir —</option>
              {inv.data?.bases.map((b) => <option key={b.id} value={b.id}>{b.id} · {b.ambiente}</option>)}
            </select>
          </label>
          <label className="lb">Referencia (caso, despliegue, tarea…)
            <input className="f" value={cambio} maxLength={40} placeholder="OPS-123 o DESPLIEGUE-NOMINA-1110" onChange={(e) => setCambio(e.target.value.toUpperCase().trim())} />
          </label>
        </div>
      </div>
      {!listo ? <div className="card"><Vacio>Elige la base y escribe una referencia que agrupe el ANTES y el DESPUÉS (clave de Jira, nombre del despliegue…).</Vacio></div> : (
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
            <h3>Comparación {comp.data && <Antiguedad s={comp.data.antiguedad_s} />}</h3>
            {comp.error ? <Vacio>{(comp.error as Error).message}</Vacio> : !c ? <Cargando q={comp} /> : (
              <>
                <div className={c.veredicto === "sin_nuevos" ? "info" : "critbox"}>
                  {c.veredicto === "sin_nuevos" ? "El cambio no dejó objetos inválidos nuevos." : `El cambio dejó ${c.resumen.nuevos} objetos inválidos nuevos.`}
                </div>
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
  const flota = useQuery({ queryKey: ["bd", "bloqueos"], queryFn: () => api.get<any[]>("/api/estado/bd/bloqueos"), refetchInterval: 30_000 });
  const [base, setBase] = useState("");
  const actual = base || flota.data?.slice().sort((a, b) => b.bloqueadas - a.bloqueadas)[0]?.base || "";
  const det = useQuery({ queryKey: ["bd", "bloqueos", actual], enabled: Boolean(actual), queryFn: () => api.get<EstadoObj>(`/api/estado/bd/${actual}/bloqueos`) });
  const refrescar = useRefresco();
  const cat = useCatalogo();
  const puedeRefrescar = cat.data?.find((a) => a.id === "com.refresco")?.permiso === "ejecuta";
  const [terminar, setTerminar] = useState<any>(null);
  return (
    <>
      <Cabecera titulo="Bloqueos" sub="Árbol de bloqueo por base: quién bloquea a quién, desde cuándo y sobre qué objeto.">
        {actual && puedeRefrescar && <button className="btn" onClick={() => refrescar(actual)}>Refrescar {actual}</button>}
      </Cabecera>
      <Cargando q={flota} />
      <div className="dbsel">
        {flota.data?.map((b) => (
          <button key={b.base} className={"dbc" + (actual === b.base ? " on" : "")} onClick={() => setBase(b.base)}
                  aria-label={`${b.base}: ${b.bloqueadas} sesiones bloqueadas`} aria-pressed={actual === b.base}>
            <div className="fila entre"><b>{b.base}</b>{b.bloqueadas > 0 ? <span className="tag t-crit">{b.bloqueadas} bloqueadas</span> : <span className="tag t-ok">sin bloqueos</span>}</div>
            <small>{b.error ? "error: " + b.error : `${b.arboles} árboles · actualizado hace ${Math.round(b.antiguedad_s / 60)} min`}</small>
          </button>
        ))}
      </div>
      {flota.data?.length === 0 && <div className="card"><Vacio>Sin datos de bloqueos todavía. Pide un refresco desde Salud.</Vacio></div>}
      {det.data && (
        <div className="card">
          <h3>{actual} <span className="fila">{det.data.filtrado && <span className="tag t-cm">{det.data.filtrado}</span>}<Antiguedad s={det.data.antiguedad_s} origen={det.data.origen} /></span></h3>
          {det.data.datos.arboles.length === 0 ? <Vacio>No hay sesiones bloqueadas.</Vacio> :
            det.data.datos.arboles.map((a: any, i: number) => (
              <div key={i} className="tree mt">
                <div className="small muted" style={{ marginBottom: 6 }}>{a.afectadas} sesiones afectadas · espera máx. {a.espera_max_s} s · {a.objetos.join(", ") || "objeto no identificado"}</div>
                <Nodo n={a.arbol} raiz alTerminar={setTerminar} />
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

function Nodo({ n, raiz, alTerminar }: { n: any; raiz?: boolean; alTerminar: (n: any) => void }) {
  const cat = useCatalogo();
  const puede = cat.data?.find((a) => a.id === "bd.ses.terminar")?.permiso === "ejecuta";
  return (
    <div>
      <div className={"node" + (raiz ? " root" : "")}>
        <div className="grow" style={{ flex: 1 }}>
          <b>{raiz ? "Raíz · " : ""}SID {n.sid}{n.serial ? "," + n.serial : ""}</b> <span className="muted small">inst {n.inst_id}</span>
          <div className="small">{n.username ?? "?"} · {n.machine ?? "?"} · {n.module ?? n.program ?? ""}</div>
          <div className="small muted">{n.event ?? ""}{n.seconds_in_wait ? ` · ${n.seconds_in_wait} s` : ""}{n.sql_id ? ` · SQL_ID ${n.sql_id}` : ""}{n.objeto ? ` · ${n.objeto}` : ""}</div>
        </div>
        {raiz && puede && !n.incompleto && <button className="btn sm red" onClick={() => alTerminar(n)}>Terminar</button>}
      </div>
      {n.hijos?.length > 0 && <div className="kids">{n.hijos.map((h: any) => <Nodo key={`${h.inst_id}-${h.sid}`} n={h} alTerminar={alTerminar} />)}</div>}
    </div>
  );
}

// ======================================================================== SALUD
export function Salud() {
  const [ambiente, setAmbiente] = useState("");
  const q = useQuery({ queryKey: ["bd", "salud", ambiente], queryFn: () => api.get<EstadoObj[]>(`/api/estado/bd/salud${ambiente ? "?ambiente=" + ambiente : ""}`), refetchInterval: 30_000 });
  const [sel, setSel] = useState<EstadoObj | null>(null);
  const refrescar = useRefresco();
  const cat = useCatalogo();
  const puedeRefrescar = cat.data?.find((a) => a.id === "com.refresco")?.permiso === "ejecuta";
  const cls = (p: number | null | undefined) => p == null ? "c-na" : p >= 95 ? "c-crit" : p >= 85 ? "c-warn" : "c-ok";
  const semCls: Record<string, string> = { verde: "c-ok", amarillo: "c-warn", rojo: "c-crit", sin_dato: "c-na" };
  return (
    <>
      <Cabecera titulo="Salud de bases" sub="Semáforo por base: instancia, tablespaces, ASM, bloqueos e inválidos fuera de la línea base.">
        <select className="f" style={{ width: 160 }} value={ambiente} onChange={(e) => setAmbiente(e.target.value)} aria-label="Ambiente">
          <option value="">Todos los ambientes</option>
          {["PROD", "DRP", "QA", "TEST", "DESA"].map((a) => <option key={a}>{a}</option>)}
        </select>
      </Cabecera>
      <Cargando q={q} />
      {q.data && q.data.length === 0 && <div className="card"><Vacio>Sin datos de salud todavía.</Vacio></div>}
      {q.data && q.data.length > 0 && (
        <div className="card tbl-wrap">
          <table className="hm">
            <thead><tr><th>Base</th><th>Ambiente</th><th>Semáforo</th><th>Tablespace máx.</th><th>ASM máx.</th><th>Bloqueadas</th><th>Inválidos nuevos</th><th>Actualizado</th>{puedeRefrescar && <th />}</tr></thead>
            <tbody>
              {q.data.map((f) => {
                const d = f.datos ?? {};
                const tsMax = d.tablespaces?.length ? Math.max(...d.tablespaces.map((t: any) => Number(t.pct_usado))) : null;
                const dgMax = d.diskgroups?.length ? Math.max(...d.diskgroups.map((t: any) => Number(t.pct_usado))) : null;
                const base = f.objeto.split(":")[1];
                return (
                  <tr key={f.objeto}>
                    <td className="nm"><b>{base}</b></td>
                    <td className="nm">{d.ambiente ?? "—"}</td>
                    <td className={"cell " + (f.error ? "c-na" : semCls[d.semaforo] ?? "c-na")} onClick={() => setSel(f)}>{f.error ? "error" : d.semaforo}</td>
                    <td className={"cell " + cls(tsMax)} onClick={() => setSel(f)}>{tsMax == null ? "—" : tsMax.toFixed(0) + " %"}</td>
                    <td className={"cell " + cls(dgMax)} onClick={() => setSel(f)}>{dgMax == null ? "—" : dgMax.toFixed(0) + " %"}</td>
                    <td className={"cell " + (d.bloqueadas > 5 ? "c-crit" : d.bloqueadas > 0 ? "c-warn" : "c-ok")} onClick={() => setSel(f)}>{d.bloqueadas ?? "—"}</td>
                    <td className={"cell " + (d.invalidos_fuera_lb > 0 ? "c-warn" : "c-ok")} onClick={() => setSel(f)}>{d.invalidos_fuera_lb ?? "—"}</td>
                    <td className="nm"><Antiguedad s={f.antiguedad_s} origen={f.origen} /></td>
                    {puedeRefrescar && <td className="nm"><button className="btn sm" onClick={() => refrescar(base)}>Refrescar</button></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {sel && (
        <Modal titulo={`Salud de ${sel.objeto.split(":")[1]}`} cerrar={() => setSel(null)}>
          {sel.error && <div className="critbox">{sel.error}</div>}
          {sel.datos?.motivos?.length > 0 && <div className="list">{sel.datos.motivos.map((m: string) => <div key={m} className="li"><span className="dot d-warn" />{m}</div>)}</div>}
          {sel.datos?.instancia && <><h3 className="small mt">Instancia</h3><TablaFilas filas={[sel.datos.instancia]} /></>}
          {sel.datos?.tablespaces?.length > 0 && <><h3 className="small mt">Tablespaces</h3><TablaFilas filas={sel.datos.tablespaces} /></>}
          {sel.datos?.diskgroups?.length > 0 && <><h3 className="small mt">Diskgroups</h3><TablaFilas filas={sel.datos.diskgroups} /></>}
        </Modal>
      )}
    </>
  );
}

// ======================================================================== TAREAS DBA
const OTRAS_PANTALLAS = new Set(["bd.snap.tomar", "bd.snap.comparar", "bd.inv.recompilar", "bd.ses.bloqueos", "bd.ses.flota",
  "bd.ses.terminar", "bd.salud.flota"]);
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
  const q = useQuery({ queryKey: ["pendientes"], queryFn: () => api.get<Ejecucion[]>("/api/aprobaciones/pendientes"), refetchInterval: 20_000 });
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
      <Cargando q={q} />
      {q.data?.length === 0 && <div className="card"><Vacio>No tienes aprobaciones pendientes.</Vacio></div>}
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
  const [sel, setSel] = useState<string | null>(null);
  const qs = new URLSearchParams({ ...(estado ? { estado } : {}), ...(cambio ? { cambio } : {}) }).toString();
  const q = useQuery({ queryKey: ["ejecuciones", estado, cambio], queryFn: () => api.get<Ejecucion[]>(`/api/ejecuciones${qs ? "?" + qs : ""}`), refetchInterval: 15_000 });
  return (
    <>
      <Cabecera titulo="Log de ejecuciones" sub="Todo lo que se ejecutó: quién, dónde, con qué caso (si lo hubo) y qué pasó. Cada ejecución cerrada deja evidencia con su SHA-256." />
      <div className="card">
        <div className="fgrid" style={{ marginBottom: 12 }}>
          <label className="lb">Estado
            <select className="f" value={estado} onChange={(e) => setEstado(e.target.value)}>
              {ESTADOS.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ") : "Todos"}</option>)}
            </select>
          </label>
          <label className="lb">Caso / referencia
            <input className="f" value={cambio} placeholder="OPS-123…" onChange={(e) => setCambio(e.target.value.toUpperCase().trim())} />
          </label>
        </div>
        <Cargando q={q} />
        {q.data?.length === 0 && <Vacio>Sin ejecuciones con ese filtro.</Vacio>}
        {q.data && q.data.length > 0 && (
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>Creada</th><th>Acción</th><th>Destino</th><th>Clase</th><th>Ejecutó</th><th>Caso</th><th>Motivo</th><th>Estado</th></tr></thead>
              <tbody>
                {q.data.map((e) => (
                  <tr key={e.id} className={"click" + (sel === e.id ? " sel" : "")} onClick={() => setSel(e.id)}>
                    <td>{fecha(e.creada)}</td><td className="mono-sm">{e.accion}</td><td>{e.destino} <span className="muted small">{e.ambiente}</span></td>
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

// ======================================================================== ETAPAS POSTERIORES
function Proxima({ titulo, etapa, texto }: { titulo: string; etapa: string; texto: string }) {
  return (
    <>
      <Cabecera titulo={titulo} />
      <div className="card"><Vacio><span className="tag t-acc">{etapa}</span><p className="mt">{texto}</p></Vacio></div>
    </>
  );
}
export const Ventana = () => <Proxima titulo="Ventana de cambios" etapa="Etapa E5" texto="Guion de la ventana con pasos de Capa Media y Base de Datos encadenados por número de cambio." />;
export const Incidentes = () => <Proxima titulo="Incidentes" etapa="Etapa E6" texto="Vista por incidente con la línea de tiempo de eventos Zabbix, ejecuciones y evidencias." />;
export const Reportes = () => <Proxima titulo="Reportes" etapa="Etapa E6" texto="Reportes periódicos de salud, inválidos, crecimiento y cumplimiento de controles." />;
