export type Pantalla = { id: string; nombre: string; solo_lectura: boolean };
export type Yo = {
  login: string;
  nombre: string;
  perfiles: string[];
  equipo: string | null;
  mfa_reciente: boolean;
  nombres_perfil?: string[];
  menu: { grupo: string; pantallas: Pantalla[] }[];
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
  parametros_libres?: boolean;
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
  ejecucion_directa?: boolean;
};

/** Caso de Jira asociado (opcional) y los valores que propone para la acción. */
export type Caso = {
  clave: string;
  resumen: string;
  estado: string;
  tipo: string;
  prioridad: string;
  asignado: string;
  informante?: string;
  proyecto: string;
  url: string;
  descripcion?: string;
  etiquetas?: string[];
  componentes?: string[];
  actualizado: string;
  sugeridos: Record<string, unknown>;
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
