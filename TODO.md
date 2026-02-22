# TODO - Roadmap Ajustado a Render Free

Contexto operativo asumido:
- Render Free: CPU/RAM limitadas, posibles cold starts, disco efímero.
- Presupuesto limitado de llamadas Riot/Gemini.
- Persistencia principal en GitHub (latencia + límites de API).

Objetivo: priorizar mejoras de **impacto alto y coste bajo**.

Leyenda:
- `[P0]` crítico inmediato
- `[P1]` alto impacto / bajo coste
- `[P2]` medio plazo
- `[P3]` opcional

## 1) Prioridad Realista (Render Free)

- [ ] `[P0]` Reducir trabajo por request en páginas pesadas (`/stats/estadisticas`, `/historial_global`).
- [ ] `[P0]` Evitar recálculos completos salvo bajo demanda manual.
- [ ] `[P0]` Limitar coste IA: siempre cachear y reutilizar análisis ya guardados.
- [ ] `[P1]` Unificar navbar y templates para reducir bugs de mantenimiento.
- [ ] `[P1]` Añadir más paginación server-side donde haya listas largas.
- [ ] `[P1]` Logging estructurado mínimo (menos ruido, más señales).

## 2) Quick Wins (1-2 días)

- [ ] `[P0]` Añadir `MAX_MATCHES_HISTORIAL_GLOBAL` configurable (ej. 500/1000) para no cargar todo en memoria.
- [ ] `[P0]` Añadir caché TTL corto en `/historial_global` (ej. 60-120s).
- [ ] `[P0]` Guardar en `stats_by_queue` todos los bloques que pinta UI (incluido `most_played_champions`) para evitar recomputes.
- [ ] `[P1]` Corregir textos con encoding roto (`Ã`, `â`) en templates y strings de backend.
- [ ] `[P1]` Revisar endpoints admin (`/configsv`, `/configops`) y bloquearlos con token simple por header/query.
- [ ] `[P1]` Añadir “última actualización” visible en vistas para entender datos stale.

## 3) Coste API Riot/Gemini

- [ ] `[P0]` Política “cache first” obligatoria para IA:
  - si existe análisis de partida/jugador en GitHub: devolverlo sin llamada a Gemini.
- [ ] `[P0]` Registrar por llamada IA:
  - modelo, duración, tamaño input, estado (ok/error), motivo de fallback.
- [ ] `[P1]` Reducir payload timeline antes de IA:
  - resumir eventos por ventanas (objetivos, kills clave, swings de oro).
- [ ] `[P1]` Cuotas duras por jugador/día (manuales con permisos `SI/NO` ya existentes).
- [ ] `[P1]` Backoff y circuit breaker para Riot/GitHub cuando haya errores en ráfaga.

## 4) Estabilidad y Resiliencia

- [ ] `[P0]` Manejar “cold start”:
  - endpoints deben responder rápido aunque datos estén en warming.
- [ ] `[P0]` Timeouts conservadores en llamadas externas + fallback limpio.
- [ ] `[P1]` `healthcheck` simple:
  - lectura GitHub, estado caché, timestamp de última actualización.
- [ ] `[P1]` Evitar operaciones O(N jugadores * N partidas) en requests web.
- [ ] `[P2]` Precalcular snapshots ligeros (`index_json`, `global_stats`) en workers y servir lectura.

## 5) Datos y GitHub (backend de facto)

- [ ] `[P0]` Esquema versionado para JSON críticos:
  - `global_stats.json`
  - `match_history/*`
  - `analisisIA/*`
  - `config/logros/achievements_config.json`
- [ ] `[P1]` Validación previa a escritura (schema) para evitar romper producción por config inválida.
- [ ] `[P1]` Reintentos con backoff + protección ante conflicto SHA (ya existe parcialmente, reforzar cobertura).
- [ ] `[P2]` Limpieza automática de archivos obsoletos/chunks huérfanos.

## 6) UI/UX (bajo coste, alta claridad)

- [ ] `[P1]` Estados vacíos útiles en todas las vistas:
  - explicar si faltan puuids, datos de historial o stats precalculadas.
- [ ] `[P1]` Indicadores visuales de filtro activo (cola/campeón) en estadísticas.
- [ ] `[P1]` Paginación uniforme y compacta en tablas.
- [ ] `[P2]` Evitar duplicación de layout: migrar `index.html`, `jugador.html`, `estadisticas.html` a base común.

## 7) Seguridad mínima viable

- [ ] `[P0]` Proteger `/configsv` y `/configops` con clave admin en env (`ADMIN_TOKEN`).
- [ ] `[P0]` Nunca mostrar rutas internas sensibles ni claves en errores frontend.
- [ ] `[P1]` Sanitizar inputs de formularios de configuración y limitar tamaño de payload.

## 8) Observabilidad pragmática

- [ ] `[P1]` Logs con prefijo por request (`request_id`) en rutas críticas.
- [ ] `[P1]` Métricas simples en JSON:
  - latencia media por endpoint
  - errores por endpoint
  - últimas 20 fallas IA/Riot/GitHub.
- [ ] `[P2]` Endpoint interno `/ops/status` para diagnóstico rápido.

## 9) Testing mínimo que sí compensa

- [ ] `[P0]` Tests de humo:
  - render de `/`, `/stats/estadisticas`, `/historial_global`, `/logros`.
- [ ] `[P1]` Tests unitarios de:
  - filtros de estadísticas
  - permisos IA
  - cálculo de logros por tiers.
- [ ] `[P2]` Test de regresión para encoding UTF-8 en templates críticos.

## 10) Funcionalidades nuevas viables en Free

- [ ] `[P1]` Historial Global con más filtros server-side:
  - jugador, cola, fecha desde/hasta (sin cargar todo el dataset en frontend).
- [ ] `[P1]` Export CSV de vistas filtradas (streaming, límite de filas).
- [ ] `[P2]` Comparador simple 1v1 de jugadores (solo métricas ya precalculadas).
- [ ] `[P3]` Recomendador/predicciones (solo si hay presupuesto de cómputo/API).

## 11) Backlog No Recomendado (por ahora)

No priorizar en Render Free hasta estabilizar:
- [ ] `[P3]` Features en tiempo real complejas.
- [ ] `[P3]` Cálculos masivos on-demand de largo historial en cada request.
- [ ] `[P3]` UI muy pesada con múltiples gráficos dinámicos simultáneos.

## 12) Plan por fases (realista)

### Fase A (P0, 1 semana)
- [ ] Limitar coste en `/historial_global` y `/estadisticas`.
- [ ] Proteger endpoints admin.
- [ ] Cerrar huecos de cache-first IA.
- [ ] Tests de humo básicos.

### Fase B (P1, 1-2 semanas)
- [ ] Unificación de templates/navbars.
- [ ] Logging útil + estado de salud.
- [ ] Mejoras UX de filtros y estados vacíos.

### Fase C (P2, cuando haya margen)
- [ ] Limpieza estructural por capas.
- [ ] Mejoras de observabilidad y mantenimiento de datos.

