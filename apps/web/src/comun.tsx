import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { SelectBuscable } from "./SelectBuscable";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErrorApi, mensajeError } from "./api";
import type { AccionCat, Inventario, Yo } from "./tipos";

// ---------------------------------------------------------------- contexto de sesión y avisos
type Ctx = { yo: Yo; avisar: (t: string) => void; soloLectura: (pantalla: string) => boolean };
export const Sesion = createContext<Ctx>(null as unknown as Ctx);
export const useSesion = () => useContext(Sesion);

// ---------------------------------------------------------------- ambiente (filtro de cada vista)
export const ORDEN_AMBIENTES = ["PROD", "DRP", "QA", "TEST", "DESA"];
export const ordenarAmbientes = (xs: Iterable<string>) =>
  Array.from(new Set(xs)).sort((a, b) => (ORDEN_AMBIENTES.indexOf(a) + 1 || 99) - (ORDEN_AMBIENTES.indexOf(b) + 1 || 99) || a.localeCompare(b));

export function useInventario() {
  return useQuery({ queryKey: ["inventario"], queryFn: () => api.get<Inventario>("/api/inventario"), staleTime: 300_000 });
}

type CtxAmbiente = { ambiente: string; setAmbiente: (a: string) => void };
const Ambiente = createContext<CtxAmbiente>({ ambiente: "", setAmbiente: () => undefined });

/** Cada vista tiene su propio filtro de ambiente ("" = todos): lo comparten el campo "Ambiente" de la
 *  vista y lo que se abre dentro de ella (formularios, modales). Al cambiar de vista empieza en "Todos". */
export function ConAmbiente({ children }: { children: ReactNode }) {
  const [ambiente, setAmbiente] = useState("");
  return <Ambiente.Provider value={{ ambiente, setAmbiente }}>{children}</Ambiente.Provider>;
}
export const useAmbiente = () => useContext(Ambiente).ambiente;

/** ¿El ambiente `a` pasa el filtro de la vista? (sin filtro, todo pasa) */
export function useEnAmbiente() {
  const ambiente = useAmbiente();
  return (a?: string | null) => !ambiente || (a ?? "").toUpperCase() === ambiente;
}

/** Campo "Ambiente" de la vista. Ofrece siempre los cinco ambientes (PROD, DRP, QA, TEST, DESA) más
 *  cualquier otro que traiga el inventario. Con `items`, los ambientes sin registros en esa lista se
 *  muestran igual, marcados "sin registros" (p. ej. DRP en una vista de bases si el inventario no tiene
 *  bases DRP). `alCambiar` limpia lo que dependía del ambiente (la base o el servidor elegidos). */
export function CampoAmbiente({ items, alCambiar, id = "f-ambiente" }: {
  items?: { ambiente: string }[]; alCambiar?: () => void; id?: string;
}) {
  const { ambiente, setAmbiente } = useContext(Ambiente);
  const inv = useInventario();
  const todo = [...(inv.data?.bases ?? []), ...(inv.data?.servidores ?? []), ...(inv.data?.dominios ?? [])];
  const fuente = items ?? todo;
  const ambs = ordenarAmbientes([...ORDEN_AMBIENTES, ...todo.map((i) => i.ambiente), ...fuente.map((i) => i.ambiente)].filter(Boolean));
  const cuenta = (a: string) => fuente.filter((i) => i.ambiente === a).length;
  return (
    <div>
      <label className="lb" htmlFor={id}>Ambiente</label>
      <SelectBuscable id={id} className="f" value={ambiente} onChange={(e) => { setAmbiente(e.target.value); alCambiar?.(); }}>
        <option value="">Todos</option>
        {ambs.map((a) => <option key={a} value={a}>{a}{cuenta(a) ? "" : " · sin registros"}</option>)}
      </SelectBuscable>
    </div>
  );
}

/** Opciones de un <select> de bases o servidores. Con filtro de ambiente: solo ese ambiente y solo el
 *  nombre ("ABBYY"). Sin filtro: agrupadas por ambiente ("Ambiente TEST" → ABBYY), sin repetirlo en cada fila. */
export function OpcionesActivo({ items }: { items: { id: string; ambiente: string; etiqueta?: string; [k: string]: any }[] }) {
  const ambiente = useAmbiente();
  // data-buscar: otros nombres con que se conoce el activo (alias prod_tron, servicio, DSN, servidor, IP, host
  // de Zabbix). La lista con búsqueda los usa para encontrarlo aunque se escriba distinto al id.
  const extra = (i: Record<string, any>) => Array.from(new Set(["alias", "servicio", "dsn", "servidor", "ip", "zabbix_host"]
    .map((k) => i[k]).filter((x) => x && String(x).toUpperCase() !== String(i.id).toUpperCase()).map(String))).join(" ");
  const op = (i: { id: string; etiqueta?: string }) => <option key={i.id} value={i.id} data-buscar={extra(i)}>{i.etiqueta ?? i.id}</option>;
  if (ambiente) {
    const deAmb = items.filter((i) => i.ambiente === ambiente);
    if (!deAmb.length) return <option value="" disabled>Sin registros en {ambiente} (el inventario no los tiene)</option>;
    return <>{deAmb.map(op)}</>;
  }
  return (
    <>
      {ordenarAmbientes(items.map((i) => i.ambiente)).map((a) => (
        <optgroup key={a} label={`Ambiente ${a}`}>
          {items.filter((i) => i.ambiente === a).map(op)}
        </optgroup>
      ))}
    </>
  );
}

/** Ejecuta `fn`; si la API responde 428 pide el segundo factor y reintenta una vez.
 *  En OIDC redirige al proveedor de identidad (vuelve a Ops Central y la persona repite la acción). */
export function useSegundoFactor() {
  const qc = useQueryClient();
  const { avisar } = useSesion();
  return async function conSegundoFactor<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      return await fn();
    } catch (e) {
      if (!(e instanceof ErrorApi && e.estado === 428)) throw e;
      const s = await api.get<{ proveedor_ingreso: string }>("/api/salud");
      if (s.proveedor_ingreso === "oidc") {
        window.location.href = "/api/auth/oidc/ingresar?stepup=1";
        return undefined;
      }
      // Solo desarrollo: el segundo factor se simula.
      await api.post("/api/auth/dev/segundo-factor");
      await qc.invalidateQueries({ queryKey: ["yo"] });
      avisar("Segundo factor simulado (modo desarrollo).");
      return await fn();
    }
  };
}

export function useCatalogo() {
  return useQuery({ queryKey: ["catalogo"], queryFn: () => api.get<AccionCat[]>("/api/catalogo"), staleTime: 300_000 });
}

export function useAccion(id: string): AccionCat | undefined {
  return useCatalogo().data?.find((a) => a.id === id);
}

// ---------------------------------------------------------------- piezas visuales
export function Cabecera({ titulo, sub, children }: { titulo: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="ph">
      <div>
        <h1>{titulo}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="btns">{children}</div>}
    </div>
  );
}

export function Cargando({ q }: { q: { isLoading: boolean; error: unknown } }) {
  if (q.isLoading) return <div className="carga">Cargando…</div>;
  if (q.error) return <div className="critbox">{(q.error as Error).message}</div>;
  return null;
}

export function Vacio({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Antiguedad({ s, origen }: { s?: number; origen?: string }) {
  if (s === undefined || s === null) return null;
  const txt = s < 90 ? `hace ${Math.round(s)} s` : s < 5400 ? `hace ${Math.round(s / 60)} min` : `hace ${Math.round(s / 3600)} h`;
  return <span className={"tag " + (s > 900 ? "t-warn" : "")} title={origen}>{txt}</span>;
}

const ESTADO_TAG: Record<string, string> = {
  exitosa: "t-ok", fallida: "t-crit", rechazada: "t-crit", cancelada: "", vencida: "",
  pendiente_aprobacion: "t-warn", en_cola: "t-acc", ejecutando: "t-acc",
};
export function EstadoEj({ e }: { e: string }) {
  return <span className={"tag " + (ESTADO_TAG[e] ?? "")}>{e.replace("_", " ")}</span>;
}

export function Clase({ c }: { c: string }) {
  const t = c === "C" || c === "X" ? "t-crit" : c === "O" ? "t-warn" : "t-ok";
  return <span className={"tag " + t} title="Clase de riesgo">Clase {c}</span>;
}

export function fecha(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("es", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function Modal({ titulo, children, pie, cerrar }: { titulo: string; children: ReactNode; pie?: ReactNode; cerrar: () => void }) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => e.key === "Escape" && cerrar();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [cerrar]);
  return (
    <div className="ov" onMouseDown={(e) => e.target === e.currentTarget && cerrar()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={titulo}>
        <h2>{titulo}</h2>
        {children}
        {pie && <div className="foot">{pie}</div>}
      </div>
    </div>
  );
}

/** Tabla genérica para resultados cuyo formato varía por acción. */
export function TablaFilas({ filas, max = 200 }: { filas: Record<string, unknown>[]; max?: number }) {
  if (!filas.length) return <Vacio>Sin filas.</Vacio>;
  const cols = Array.from(new Set(filas.flatMap((f) => Object.keys(f)))).filter((c) => c !== "hijos").slice(0, 12);
  return (
    <div className="tbl-wrap">
      <table className="tbl-std">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {filas.slice(0, max).map((f, i) => (
            <tr key={i}>{cols.map((c) => <td key={c} className="mono-sm">{celda(f[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function celda(v: unknown) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Muestra el resultado de una ejecución: tablas si trae listas de filas, si no JSON. */
export function Resultado({ r }: { r: any }) {
  if (r === null || r === undefined) return null;
  if (Array.isArray(r)) return <TablaFilas filas={r} />;
  if (typeof r === "object") {
    const listas = Object.entries(r).filter(([, v]) => Array.isArray(v) && (v as unknown[]).length && typeof (v as unknown[])[0] === "object");
    const resto = Object.fromEntries(Object.entries(r).filter(([, v]) => !(Array.isArray(v) && (v as unknown[]).length && typeof (v as unknown[])[0] === "object")));
    return (
      <>
        {Object.keys(resto).length > 0 && <pre className="console json">{JSON.stringify(resto, null, 2)}</pre>}
        {listas.map(([k, v]) => (
          <div key={k} className="mt">
            <h3 className="small muted" style={{ marginBottom: 6 }}>{k}</h3>
            <TablaFilas filas={v as Record<string, unknown>[]} />
          </div>
        ))}
      </>
    );
  }
  return <pre className="console">{String(r)}</pre>;
}

// ---------------------------------------------------------------- consola en vivo (SSE)
export function Consola({ id, alTerminar }: { id: string; alTerminar?: (estado: string) => void }) {
  const [lineas, setLineas] = useState<{ n: number; texto: string }[]>([]);
  const [fin, setFin] = useState<string | null>(null);
  const caja = useRef<HTMLDivElement>(null);
  const cb = useRef(alTerminar);
  cb.current = alTerminar;

  useEffect(() => {
    setLineas([]);
    setFin(null);
    const es = new EventSource(`/api/ejecuciones/${id}/eventos`);
    es.addEventListener("linea", (ev) => {
      const l = JSON.parse((ev as MessageEvent).data);
      setLineas((p) => (p.some((x) => x.n === l.n) ? p : [...p, l]));
    });
    es.addEventListener("fin", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setFin(d.estado);
      es.close();
      cb.current?.(d.estado);
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [id]);

  useEffect(() => {
    caja.current?.scrollTo(0, caja.current.scrollHeight);
  }, [lineas]);

  return (
    <div className="console" ref={caja} aria-live="polite">
      {lineas.length === 0 && !fin && <span className="ts">Esperando a que un worker tome la ejecución…</span>}
      {lineas.map((l) => (
        <div key={l.n} className={/error|fall/i.test(l.texto) ? "er" : /OK|exitos|VALID/.test(l.texto) ? "ok" : ""}>
          <span className="ts">{String(l.n).padStart(3, "0")} </span>{l.texto}
        </div>
      ))}
      {fin && <div className={fin === "exitosa" ? "ok" : "er"}>── fin: {fin}</div>}
    </div>
  );
}

/** Al abrir una vista de BD (Bloqueos, Salud) pide una lectura ACTUAL del ambiente visible: sin esto la
 *  pantalla solo muestra lo último guardado (la pasada periódica, una alerta o un refresco anterior).
 *  Mientras se lee, avisa y recarga los datos cada pocos segundos; el servidor limita la frecuencia. */
export function ActualizandoVista({ vista }: { vista: "bloqueos" | "salud" }) {
  const ambiente = useAmbiente();
  const qc = useQueryClient();
  const { avisar } = useSesion();
  const cat = useCatalogo();
  const puedeRefrescar = cat.data?.find((a) => a.id === "com.refresco")?.permiso === "ejecuta";
  const [r, setR] = useState<{ estado?: string; en_curso?: boolean; ultimo?: string | null; reintentar_en_s?: number;
    resultado?: { total: number; con_error: number } | null } | null>(null);
  const [termino, setTermino] = useState(false);
  async function pedir(forzar: boolean) {
    const x = await api.post<any>("/api/refresco/vista", { vista, ambiente: ambiente || null, forzar });
    setR(x); setTermino(false);
    return x;
  }
  useEffect(() => {
    let vivo = true;
    setR(null); setTermino(false);
    api.post<any>("/api/refresco/vista", { vista, ambiente: ambiente || null })
      .then((x) => vivo && setR(x)).catch(() => vivo && setR({ estado: "error" }));
    return () => { vivo = false; };
  }, [vista, ambiente]);
  useEffect(() => {
    if (!r?.en_curso) return;
    const t = setInterval(async () => {
      qc.invalidateQueries({ queryKey: ["bd"] });
      try {
        const e = await api.get<any>(`/api/refresco/vista?vista=${vista}${ambiente ? "&ambiente=" + ambiente : ""}`);
        if (!e.en_curso) { setR((p) => ({ ...p, ...e })); setTermino(true); qc.invalidateQueries({ queryKey: ["bd"] }); }
      } catch { /* se reintenta en el próximo ciclo */ }
    }, 4000);
    return () => clearInterval(t);
  }, [r?.en_curso, vista, ambiente, qc]);

  async function refrescarTodo() {
    try {
      const x = await pedir(true);
      if (x.estado === "reciente" && !x.en_curso) avisar(`Se acaba de leer todo; se puede volver a pedir en ${x.reintentar_en_s} s.`);
    } catch (e) { avisar(mensajeError(e)); }
  }

  const hace = r?.ultimo ? Math.max(0, Math.round((Date.now() - new Date(r.ultimo).getTime()) / 60000)) : null;
  const texto = !r || r.estado === "error" ? "" :
    r.en_curso ? `Leyendo datos actuales de Oracle${ambiente ? ` (${ambiente})` : ""}… Mientras tanto se muestran los últimos guardados; se actualizan solos.` :
    termino && r.resultado ? (r.resultado.con_error
      ? `Lectura terminada: ${r.resultado.total - r.resultado.con_error} de ${r.resultado.total} bases respondieron; ${r.resultado.con_error} sin conexión (se muestra su último dato guardado).`
      : `Lectura terminada: ${r.resultado.total} bases con datos actuales.`) :
    termino ? "Lectura terminada." :
    hace != null ? `Última lectura completa hace ${hace} min.` : "";
  return (
    <div className={"fila entre lectura-vista" + (r?.en_curso ? " info" : "")} role="status">
      <span className="small fila" style={{ gap: 8 }}>
        {r?.en_curso && <span className="giro" aria-hidden>↻</span>}
        <span className={r?.en_curso ? "" : "muted"}>{texto}</span>
      </span>
      {puedeRefrescar && (
        <button className="btn" disabled={Boolean(r?.en_curso)} onClick={refrescarTodo}
                title={`Vuelve a leer en Oracle todas las bases ${ambiente ? "de " + ambiente : "de todos los ambientes"}`}>
          {r?.en_curso ? "Leyendo…" : `↻ Refrescar todo${ambiente ? ` (${ambiente})` : ""}`}
        </button>
      )}
    </div>
  );
}
