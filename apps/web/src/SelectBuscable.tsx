// Lista desplegable con campo de búsqueda (feedback de la demo: con 200+ bases un <select> no sirve).
// Se usa igual que un <select>: mismos hijos (<option>, <optgroup>, componentes que los devuelven) y el
// mismo onChange(e) con e.target.value. Por dentro, un <select> oculto renderiza los hijos y de ahí se
// leen las opciones; lo visible es un campo de texto que filtra mientras se escribe.
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

type Op = { value: string; label: string; grupo: string; disabled: boolean };

const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

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
      value: o.value, label: o.text, disabled: o.disabled,
      grupo: o.parentElement?.tagName === "OPTGROUP" ? (o.parentElement as HTMLOptGroupElement).label : "",
    }));
    setOps((prev) => (JSON.stringify(prev) === JSON.stringify(nuevas) ? prev : nuevas));
  });

  const v = String(value ?? "");
  const actual = ops.find((o) => o.value === v);
  const filtradas = q ? ops.filter((o) => norm(`${o.label} ${o.grupo}`).includes(norm(q))) : ops;

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
      const paso = e.key === "ArrowDown" ? 1 : -1;
      let i = activo;
      for (let n = 0; n < filtradas.length; n++) {
        i = (i + paso + filtradas.length) % filtradas.length;
        if (!filtradas[i].disabled) break;
      }
      setActivo(i);
      lista.current?.children[i]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      if (abierto) { e.preventDefault(); elegir(filtradas[activo]); }
    } else if (e.key === "Escape") {
      if (abierto) { e.stopPropagation(); setAbierto(false); setQ(""); }
    }
  }

  let grupoPrevio = "";
  return (
    <div className="sb">
      <input id={id} className={className + " sb-in"} role="combobox" aria-expanded={abierto} aria-controls={idLista}
        aria-autocomplete="list" autoComplete="off" disabled={disabled}
        placeholder={actual?.label || placeholder || "Buscar…"}
        value={abierto ? q : (actual && actual.value !== "" ? actual.label : "")}
        onFocus={() => { setAbierto(true); setQ(""); setActivo(Math.max(0, filtradas.findIndex((o) => o.value === v))); }}
        onBlur={() => setTimeout(() => { setAbierto(false); setQ(""); }, 120)}
        onChange={(e) => { setQ(e.target.value); setAbierto(true); setActivo(0); }}
        onKeyDown={tecla} />
      <span className="sb-flecha" aria-hidden>▾</span>
      {abierto && !disabled && (
        <ul ref={lista} id={idLista} className="sb-lista" role="listbox">
          {filtradas.length === 0 && <li className="sb-vacio">Sin coincidencias para “{q}”</li>}
          {filtradas.map((o, i) => {
            const cab = o.grupo && o.grupo !== grupoPrevio ? <li className="sb-grupo" key={"g" + o.grupo + i}>{o.grupo}</li> : null;
            grupoPrevio = o.grupo;
            return [cab,
              <li key={o.value + i} role="option" aria-selected={o.value === v} aria-disabled={o.disabled}
                className={"sb-op" + (i === activo ? " act" : "") + (o.value === v ? " sel" : "") + (o.disabled ? " dis" : "")}
                onMouseDown={(e) => { e.preventDefault(); elegir(o); }} onMouseEnter={() => setActivo(i)}>
                {o.label}
              </li>];
          })}
        </ul>
      )}
      <select ref={oculto} value={v} onChange={() => undefined} hidden aria-hidden tabIndex={-1}>{children}</select>
    </div>
  );
}
