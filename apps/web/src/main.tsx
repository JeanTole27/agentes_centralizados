import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ErrorApi } from "./api";
import App from "./App";
import "./estilos.css";
import "./extra.css";

// Las pantallas leen del almacén de Ops Central. Se refrescan cada 60 s como máximo:
// el sondeo es contra la API, nunca contra las bases ni los servidores.
const cliente = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 60_000,
      staleTime: 15_000,
      retry: (n, e) => !(e instanceof ErrorApi && [401, 403, 404].includes(e.estado)) && n < 2,
    },
  },
});

ReactDOM.createRoot(document.getElementById("raiz")!).render(
  <React.StrictMode>
    <QueryClientProvider client={cliente}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
