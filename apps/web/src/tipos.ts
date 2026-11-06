export type Pantalla = { id: string; nombre: string; solo_lectura: boolean };
export type Yo = {
  login: string;
  nombre: string;
  perfiles: string[];
  equipo: string | null;
  mfa_reciente: boolean;
  menu: { grupo: string; pantallas: Pantalla[] }[];
  simulacion: boolean;
};

export type EspParam = {
  tipo: string;
  requerido?: boolean;
  valores?: string[];
  min?: number;
  max?: number;
  defecto?: unknown;
};

export type AccionCat = {
  id: string;
  nombre: string;
  motor: string;
  clase: string;
  clase_prod: string | null;
  permiso: "ejecuta" | "ve";
  estado: "implementada" | "catalogada";
  parametros: Record<string, EspParam>;
  bloqueo: string | null;
};

export type Evaluacion = {
  accion: string;
  destino: string;
  ambiente: string;
  clase: string;
  permitido: boolean;
  motivo: string;
  controles: string[];
  requiere_cambio: boolean;
  requiere_aprobacion: boolean;
  requiere_segundo_factor: boolean;
};

export type Ejecucion = {
  id: string;
  accion: string;
  motor: string;
  clase: string;
  destino: string;
  ambiente: string;
  params: Record<string, unknown>;
  cambio: string | null;
  motivo: string | null;
  solicitante: string;
  estado: string;
  huella: string;
  aprobador: string | null;
  comentario: string | null;
  creada: string;
  iniciada: string | null;
  terminada: string | null;
  resultado: any;
  error: string | null;
  evidencia: string | null;
  evidencia_sha256: string | null;
  origen: string;
};

export type EstadoObj<T = any> = {
  objeto: string;
  tipo: string;
  datos: T;
  origen: string;
  actualizado: string;
  error: string | null;
  antiguedad_s: number;
  filtrado?: string;
};

export type Inventario = {
  servidores: { id: string; ambiente: string; ip?: string; equipo?: string; relacionados: string[] }[];
  dominios: { id: string; ambiente: string; servidor: string; relacionados: string[] }[];
  bases: { id: string; ambiente: string; criticidad?: string; servidor?: string; relacionados: string[] }[];
};
