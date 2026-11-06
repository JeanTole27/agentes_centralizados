import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErrorApi } from "./api";
import type { AccionCat, Yo } from "./tipos";

// ---------------------------------------------------------------- contexto de sesión y avisos
type Ctx = { yo: Yo; avisar: (t: string) => void; soloLectura: (pantalla: string) => boolean };
export const Sesion = createContext<Ctx>(null as unknown as Ctx);
export const useSesion = () => useContext(Sesion);

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
      <table>
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
