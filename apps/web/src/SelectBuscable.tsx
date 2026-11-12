// Lista desplegable con campo de búsqueda (feedback de la demo: con 200+ bases un <select> no sirve).
// Se usa igual que un <select>: mismos hijos (<option>, <optgroup>, componentes que los devuelven) y el
// mismo onChange(e) con e.target.value. Por dentro, un <select> oculto renderiza los hijos y de ahí se
// leen las opciones; lo visible es un campo de texto que filtra mientras se escribe.
//
// Búsqueda por similitud: las bases se conocen por otros nombres (alias prod_tron, servicio TRON, DSN,
// servidor). Cada <option> puede traer `data-buscar` con esos textos extra. Se busca sin acentos ni
// mayúsculas, ignorando "_", "-" y espacios, tolerando un error de tipeo, y si nada coincide se muestran
// las opciones más parecidas en vez de dejar la lista vacía.
import { useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

type Op = { value: string; label: string; grupo: string; disabled: boolean; buscar: string };

const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const compacto = (s: string) => norm(s).replace(/[^a-z0-9]/g, "");

/** Menor distancia de edición entre `t` y cualquier tramo de `h` (búsqueda aproximada de subcadena).
 *  Dos letras intercambiadas ("torn" por "tron") cuentan como un solo error. */
function distanciaTramo(t: string, h: string): number {
  let ant: number[] = [];
  let prev = new Array(h.length + 1).fill(0);
  for (let i = 1; i <= t.length; i++) {
    const cur = [i];
    for (let j = 1; j <= h.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (t[i - 1] === h[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && t[i - 1] === h[j - 2] && t[i - 2] === h[j - 1]) cur[j] = Math.min(cur[j], ant[j - 2] + 1);
    }
    ant = prev;
    prev = cur;
  }
  return Math.min(...prev);
}

/** Puntaje de coincidencia de `q` en `texto`: 0 = no coincide. Todas las palabras de q deben aparecer
 *  (exacta vale más que aproximada; al inicio vale más que en medio). */
export function puntaje(q: string, texto: string): number {
  const palabras = norm(q).split(/[^a-z0-9]+/).filter(Boolean);
  if (!palabras.length) return 1;
  const h = compacto(texto);
  let total = 0;
  for (const p of palabras) {
    const i = h.indexOf(p);
    if (i >= 0) { total += i === 0 ? 4 : 3; continue; }
    const tolera = p.length >= 7 ? 2 : p.length >= 4 ? 1 : 0;
    if (tolera && distanciaTramo(p, h) <= tolera) { total += 1; continue; }
    return 0;
  }
  return total;
}
export const coincide = (q: string, texto: string) => puntaje(q, texto) > 0;

/** Filtra una lista por similitud: si hay coincidencias exactas se muestran solo esas (ordenadas);
 *  si no, las aproximadas (un error de tipeo). Para tablas y listas que no usan SelectBuscable. */
export function filtrarSimilares<T>(q: string, items: T[], texto: (x: T) => string): T[] {
  if (!q.trim()) return items;
  const exactas = 3 * norm(q).split(/[^a-z0-9]+/).filter(Boolean).length;
  const con = items.map((x, i) => ({ x, i, p: puntaje(q, texto(x)) })).filter((r) => r.p > 0)
    .sort((a, b) => b.p - a.p || a.i - b.i);
  const solo = con.some((r) => r.p >= exactas) ? con.filter((r) => r.p >= exactas) : con;
  return solo.map((r) => r.x);
}

/** Parecido aproximado (bigramas) para sugerir opciones cuando nada coincide. */
function parecido(a: string, b: string): number {
  const bi = (s: string) => { const out = new Set<string>(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); return out; };
  const x = bi(compacto(a)), y = bi(compacto(b));
  if (!x.size || !y.size) return 0;
  let comun = 0;
  x.forEach((g) => { if (y.has(g)) comun++; });
  return (2 * comun) / (x.size + y.size);
}

/** Marca en negrita el tramo de `texto` que coincide con alguna palabra de `q` (sin acentos/mayúsculas). */
function resaltar(texto: string, q: string): ReactNode {
  const palabras = norm(q).split(/[^a-z0-9]+/).filter((p) => p.length >= 2);
  if (!palabras.length) return texto;
  const t = norm(texto);
  let mejor: [number, number] | null = null;
  for (const p of palabras) {
    const i = t.indexOf(p);
    if (i >= 0 && (!mejor || p.length > mejor[1])) mejor = [i, p.length];
  }
  if (!mejor) return texto;
  const [i, n] = mejor;
  return <>{texto.slice(0, i)}<mark className="sb-marca">{texto.slice(i, i + n)}</mark>{texto.slice(i + n)}</>;
}

export function SelectBuscable({ value, onChange, children, disabled, id, className = "f", placeholder }: {
  value: string | number | undefined; onChange: (e: { target: { value: string } }) => void; children: ReactNode;
  disabled?: boolean; id?: string; className?: string; placeholder?: string;
}) {
  const oculto = useRef<HTMLSelectElement>(null);
  const lista = useRef<HTMLUListElement>(null);
  const [ops, setOps] = useState<Op[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [q, setQ] = useState("");
  const [activo, setActivo] = useState(0);
  const idLista = useId();

  useLayoutEffect(() => {
    const s = oculto.current;
    if (!s) return;
    const nuevas: Op[] = Array.from(s.options).map((o) => ({
      value: o.value, label: o.text, disabled: o.disabled, buscar: o.dataset.buscar ?? "",
      grupo: o.parentElement?.tagName === "OPTGROUP" ? (o.parentElement as HTMLOptGroupElement).label : "",
    }));
    setOps((prev) => (JSON.stringify(prev) === JSON.stringify(nuevas) ? prev : nuevas));
  });

  const v = String(value ?? "");
  const actual = ops.find((o) => o.value === v);
  // Coincidencias ordenadas por puntaje (estable: a igual puntaje se respeta el orden original y su grupo).
  // Sin coincidencias: las más parecidas, marcadas como sugerencia.
  const { filtradas, sugeridas } = useMemo(() => {
    if (!q.trim()) return { filtradas: ops, sugeridas: false };
    const conPuntaje = ops.map((o, i) => ({ o, i, p: o.value === "" ? 0 : puntaje(q, `${o.label} ${o.buscar} ${o.grupo}`) }))
      .filter((x) => x.p > 0).sort((a, b) => b.p - a.p || a.i - b.i);
    // si hay coincidencias exactas, las aproximadas (un error de tipeo) solo meterían ruido
    const exactas = 3 * norm(q).split(/[^a-z0-9]+/).filter(Boolean).length;
    const solo = conPuntaje.some((x) => x.p >= exactas) ? conPuntaje.filter((x) => x.p >= exactas) : conPuntaje;
    if (solo.length) return { filtradas: solo.map((x) => x.o), sugeridas: false };
    const cerca = ops.filter((o) => o.value !== "" && !o.disabled)
      .map((o) => ({ o, s: Math.max(parecido(q, o.label), ...o.buscar.split(/\s+/).filter(Boolean).map((b) => parecido(q, b))) }))
      .filter((x) => x.s >= 0.25).sort((a, b) => b.s - a.s).slice(0, 8).map((x) => x.o);
    return { filtradas: cerca, sugeridas: true };
  }, [q, ops]);

  function elegir(o: Op | undefined) {
    if (!o || o.disabled) return;
    setAbierto(false);
    setQ("");
    if (o.value !== v) onChange({ target: { value: o.value } });
  }

  function tecla(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!abierto) { setAbierto(true); return; }
      if (!filtradas.length) return;
      const paso = e.key === "ArrowDown" ? 1 : -1;
      let i = activo;
      for (let n = 0; n < filtradas.length; n++) {
        i = (i + paso + filtradas.length) % filtradas.length;
        if (!filtradas[i].disabled) break;
      }
      setActivo(i);
      lista.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      if (abierto) { e.preventDefault(); elegir(filtradas[activo]); }
    } else if (e.key === "Escape") {
      if (abierto) { e.stopPropagation(); setAbierto(false); setQ(""); }
    }
  }

  // Al buscar se muestran planas (ordenadas por coincidencia) con su grupo como etiqueta; sin búsqueda, por grupo.
  const conGrupos = !q.trim();
  // "— elegir —" no dice que se puede escribir: sin selección real se muestra una invitación a buscar.
  const sinElegir = !actual || (actual.value === "" && /^\s*[—–-]/.test(actual.label));
  const invitacion = placeholder || (ops.length > 12 ? "Escribe para buscar (nombre, alias o servidor)…" : "Buscar…");
  const elegibles = ops.filter((o) => o.value !== "" && !o.disabled).length;
  let grupoPrevio = "";
  return (
    <div className="sb">
      <svg className="sb-lupa" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M10.4 10.4 14 14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      <input id={id} className={className + " sb-in"} role="combobox" aria-expanded={abierto} aria-controls={idLista}
        aria-autocomplete="list" autoComplete="off" disabled={disabled}
        placeholder={sinElegir ? invitacion : actual!.label}
        value={abierto ? q : (actual && actual.value !== "" ? actual.label : "")}
        onFocus={() => { setAbierto(true); setQ(""); setActivo(Math.max(0, ops.findIndex((o) => o.value === v))); }}
        onBlur={() => setTimeout(() => { setAbierto(false); setQ(""); }, 120)}
        onChange={(e) => { setQ(e.target.value); setAbierto(true); setActivo(0); }}
        onKeyDown={tecla} />
      <span className="sb-flecha" aria-hidden>▾</span>
      {abierto && !disabled && (
        <ul ref={lista} id={idLista} className="sb-lista" role="listbox">
          {!q.trim() && elegibles > 12 && <li className="sb-ayuda">Escribe para filtrar entre {elegibles} opciones · no hace falta el nombre exacto</li>}
          {q.trim() && !sugeridas && filtradas.length > 0 && <li className="sb-ayuda">{filtradas.length} coincidencia{filtradas.length === 1 ? "" : "s"} con “{q}”</li>}
          {filtradas.length === 0 && <li className="sb-vacio">Nada parecido a “{q}”</li>}
          {sugeridas && filtradas.length > 0 && <li className="sb-grupo">Sin coincidencia exacta · parecidos a “{q}”</li>}
          {filtradas.map((o, i) => {
            const cab = conGrupos && o.grupo && o.grupo !== grupoPrevio ? <li className="sb-grupo" key={"g" + o.grupo + i}>{o.grupo}</li> : null;
            grupoPrevio = o.grupo;
            return [cab,
              <li key={o.value + i} data-i={i} role="option" aria-selected={o.value === v} aria-disabled={o.disabled}
                className={"sb-op" + (i === activo ? " act" : "") + (o.value === v ? " sel" : "") + (o.disabled ? " dis" : "")}
                onMouseDown={(e) => { e.preventDefault(); elegir(o); }} onMouseEnter={() => setActivo(i)}>
                <span>{resaltar(o.label, q)}</span>
                {!conGrupos && (o.grupo || o.buscar) && <span className="sb-extra">{[o.grupo.replace(/^Ambiente /, ""), o.buscar].filter(Boolean).join(" · ")}</span>}
              </li>];
          })}
        </ul>
      )}
      <select ref={oculto} value={v} onChange={() => undefined} hidden aria-hidden tabIndex={-1}>{children}</select>
    </div>
  );
}
