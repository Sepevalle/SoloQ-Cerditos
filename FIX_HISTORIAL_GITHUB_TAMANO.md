# Fix: historial de partidas no se actualiza por tamaño (GitHub)

## 🚀 AI IMPLEMENTATION PROMPT (entrada para otra IA)

Eres una IA de desarrollo y debes **implementar** mejoras para evitar que el historial de partidas deje de actualizarse por límites de tamaño al guardar en GitHub.

### Objetivo
Garantizar que **todos** los jugadores (incluyendo los que tienen muchísimas partidas) puedan persistir y leer su historial sin fallar por:
- Límite de tamaño de GitHub Contents API.
- Overhead de Base64 al subir el contenido.

### Contexto técnico del repo
- Lenguaje: Python (Flask).
- Persistencia: archivos en GitHub vía Contents API desde el módulo services/github_service.py.
- Existe formato legacy y formato “v2 weekly” ya implementado:
  - Legacy: match_history/{puuid}.json
  - v2: match_history/{puuid}/index.json + match_history/{puuid}/weeks/{YYYY}-W{WW}.json

### Requisitos (obligatorios)
1) Mantener compatibilidad de lectura:
   - Si existe match_history/{puuid}/index.json (v2/v3) se debe poder leer y reconstruir la lista de matches.
   - Si no existe index, usar legacy match_history/{puuid}.json.

2) Evitar fallos por tamaño:
   - Nunca intentar subir un payload que exceda el límite práctico.
   - Considerar que el upload via Contents API usa Base64 (overhead ~33%).

3) Implementar “v3 chunks por tamaño”:
   - Una semana ISO puede dividirse en múltiples archivos si su tamaño excede el umbral.
   - Ejemplo de nombres válidos:
     - weeks/2026-W07-01.json
     - weeks/2026-W07-02.json
   - El index debe listar TODOS los chunks reales en el campo files.

4) Actualizar consumidores que asumen match_history/*.json plano:
   - El script validate_lp_assignments.py hoy itera match_history/*.json y asume estructura {"matches": [...]}
   - Debe soportar también el formato por carpetas con index.json + files.

5) Integridad al escribir:
   - Escribir chunks primero y actualizar index al final.
   - El index NO debe referenciar chunks que no se hayan guardado correctamente.

### Alcance
- Cambios de backend/servicios y scripts.
- No cambiar UX, templates, endpoints, ni diseño.

### No-alcance (no hacer)
- No añadir páginas nuevas ni features de UI.
- No migrar masivamente ni borrar legacy automáticamente (solo si se indica en sección “Opcional”).

### Archivos a modificar (mínimo)
1) services/github_service.py
   - save_player_match_history(puuid, historial_data)
   - (opcional recomendado) write_file_to_github(...)
2) validate_lp_assignments.py

### Implementación esperada (pasos concretos)

#### Paso A — Umbral correcto considerando Base64
En services/github_service.py:
- Implementar una función utilitaria para estimar el tamaño del payload:
  - bytes_json = len(json_str.encode('utf-8'))
  - bytes_b64 = len(base64.b64encode(json_str.encode('utf-8')))
- Definir un umbral conservador, por ejemplo:
  - MAX_B64_BYTES = 950_000  (ajustable)
  - O equivalente en JSON bytes si prefieres.

#### Paso B — Split dentro de semana (v3)
En save_player_match_history():
- Agrupar matches por semana ISO como ya está.
- Para cada semana:
  - Serializar la lista a JSON compacto.
  - Si supera el umbral, partir week_matches en N chunks.
  - Guardar cada chunk como weeks/{wk}-{NN}.json
  - Añadir cada path relativo a index.files.

Reglas:
- Cada chunk debe mantener orden por game_end_timestamp DESC.
- Debe ser determinista: con los mismos matches, el orden de files debe ser estable.
- Si una semana no excede el umbral, puede seguir guardándose como weeks/{wk}.json (compat v2) o también como -01. Elige UNA política y documenta.

#### Paso C — Index consistente
- Solo incluir en files los chunks que realmente se guardaron OK.
- Si falla algún chunk, no romper el index previo: devolver False y dejar el estado consistente.

#### Paso D — Logging defensivo
En write_file_to_github():
- Loguear:
  - bytes_json
  - bytes_b64
  - status_code y response.text truncado (ya existe)

#### Paso F — Robustez (recomendado)
Sin cambiar la UX ni los endpoints, mejorar tolerancia a fallos:
- Si GitHub devuelve conflicto por SHA (p.ej. 409/422 dependiendo del caso):
  - Re-leer SHA y reintentar 1-2 veces con backoff corto.
- Si GitHub devuelve rate limit/forbidden (403) o errores temporales (5xx):
  - Reintentar con backoff exponencial corto (máx 2-3 intentos) y luego fallar de forma limpia.
- Asegurar que un fallo parcial no deje el index apuntando a chunks inexistentes.

#### Paso E — Actualizar validate_lp_assignments.py
- Cambiar validate_match_lp_assignments() para que:
  - Recorra match_history/.
  - Si encuentra archivos *.json con estructura legacy, procesarlos como hoy.
  - Si encuentra subcarpetas (cada una para un puuid):
    - Leer index.json
    - Leer cada archivo listado en index.files
    - Unir matches y validar igual.

### Criterios de aceptación (Definition of Done)
- Un jugador con historial grande:
  - No falla al guardar en GitHub.
  - Se crean múltiples archivos por semana si hace falta.
  - index.json lista los chunks correctos.
- Un jugador pequeño:
  - Sigue funcionando (legacy o weekly), sin errores.
- Lectura:
  - read_player_match_history() reconstruye matches correctamente desde index + files.
- validate_lp_assignments.py:
  - Puede validar tanto legacy como formato por carpetas.

### Validación recomendada
- Ejecutar un ciclo que intente guardar un historial artificial “grande” y verificar que se parte.
- Ejecutar validate_lp_assignments.py en un directorio match_history que contenga ambos formatos.

### Entregables
- Código modificado en los archivos listados.
- Si introduces un nuevo formato (v3), actualizar el índice para reflejarlo (sin romper v2).

---

## Contexto y diagnóstico (referencia)

## Contexto
En este proyecto el historial de partidas por jugador se persiste en el repo vía GitHub API (Contents API), usando [`services/github_service.py`](services/github_service.py).

- Lectura: `read_player_match_history(puuid)`
- Escritura: `save_player_match_history(puuid, historial_data)`

Ya existe un formato **v2** “por semanas” para evitar el archivo único grande:
- Legacy: `match_history/{puuid}.json`
- v2 weekly: `match_history/{puuid}/index.json` + `match_history/{puuid}/weeks/{YYYY}-W{WW}.json`

## Síntoma
Jugadores con muchas partidas dejan de actualizarse porque el archivo (o chunk semanal) supera el límite de tamaño aceptado por la API de GitHub.

## Causa raíz (probable)
1) **Límite de la GitHub Contents API**: el endpoint `PUT /repos/{owner}/{repo}/contents/{path}` tiene límites prácticos (≈ 1MB de contenido). Cuando el archivo excede ese límite, GitHub responde con error (p.ej. 413 / 422 según el caso).

2) **Overhead por Base64**: `write_file_to_github()` sube el contenido en Base64. El tamaño que “viaja” en la request crece ~33%.
   - En `save_player_match_history()` se usa `MAX_CONTENTS_BYTES = 900_000` (estimando bytes del JSON UTF‑8).
   - Pero 900KB de JSON → ~1.2MB base64, lo que puede fallar aunque el JSON “parezca” < 1MB.

3) **Semana demasiado grande**: incluso con v2 semanal, una semana con muchísimas partidas puede seguir superando el límite.

## Objetivo
Garantizar que **siempre** se pueda persistir historial de partidas, incluso para jugadores con muchísimas partidas, sin romper la lectura existente.

## Estrategia recomendada (v3: chunks por tamaño)
Mantener el “index + lista de archivos” (v2), pero permitir que una semana se divida en **sub‑chunks** por tamaño.

### Idea
En lugar de guardar solo:
- `weeks/2026-W07.json`

Permitir:
- `weeks/2026-W07-01.json`
- `weeks/2026-W07-02.json`
- `weeks/2026-W07-03.json`

Y en `index.json` mantener `files: [...]` con TODOS los paths relativos.

✅ Ventaja importante: **`read_player_match_history()` ya concatena `files`**, así que si `files` contiene 3 archivos para una semana, la lectura seguirá funcionando **sin cambios** (solo concatenará más partes).

## Impacto en otros procesos (qué hay que cambiar/revisar)

En esta codebase, casi todo el consumo del historial pasa por `get_player_match_history()` → `read_player_match_history()`. Eso **ya soporta** el formato “index + files” (v2) y por lo tanto también soportará “weeks con sufijo -NN” (v3) siempre que se mantenga la lista `files` en el index.

### Consumidores que NO deberían romperse (porque usan el servicio)
- `services/match_service.py` (lectura/escritura centralizada).
- `services/data_updater.py` (workers de actualización que leen/guardan vía `read_player_match_history` / `save_player_match_history`).
- Blueprints: `blueprints/main.py`, `blueprints/player.py`, `blueprints/stats.py`, `blueprints/api.py` (usan `get_player_match_history`).
- Generación del index: `services/index_json_generator.py` (usa `get_player_match_history(puuid, limit=20)`).

### Consumidores que SÍ requieren cambios si el historial se parte en carpetas/archivos

#### 1) Scripts locales que iteran `match_history/*.json`
Ejemplo: `validate_lp_assignments.py`.

Actualmente asume que dentro de `match_history/` solo hay archivos `*.json` con estructura `{"matches": [...]}`.
Con formato v2/v3, para jugadores grandes habrá carpetas:
- `match_history/{puuid}/index.json`
- `match_history/{puuid}/weeks/*.json`

Qué cambiar:
- Si encuentra un archivo `match_history/{puuid}.json` (legacy), procesarlo como hoy.
- Si encuentra una carpeta `match_history/{puuid}/`:
  - Cargar `index.json`.
  - Iterar `files` y cargar cada archivo listado.
  - Combinar en una única lista `matches` para ejecutar la validación igual que antes.

#### 2) Herramientas externas / usos fuera del código
Si existe cualquier job externo (otro repo, un script en CI, o una persona) que descargue `match_history/{puuid}.json` directamente desde GitHub, eso **ya no será confiable** para jugadores grandes (porque se guardarán en v2/v3).

Qué hacer:
- Documentar que la fuente “oficial” es `match_history/{puuid}/index.json` cuando exista.
- (Opcional) generar un “legacy recortado” (últimas N partidas) en `match_history/{puuid}.json` para compatibilidad humana/externa.

#### 3) Estadísticas globales
Las stats globales en runtime se calculan desde listas `all_matches` armadas a partir de `get_player_match_history()`, así que **no deberían romperse** por el split.

Lo que sí conviene revisar:
- Performance: al combinar muchos chunks, leer un historial completo (`limit=-1`) hará muchas requests a GitHub.
  - Mitigación: mantener el uso de `limit=20` donde sea posible (como ya hace el index).
  - Para cálculos globales: evitar recalcular full-scan demasiado seguido (ya existe `GLOBAL_STATS_UPDATE_INTERVAL`).

### Ajuste clave: umbral por Base64
Cambiar la lógica de “umbral” para que se base en el tamaño real aproximado de la carga:
- Opción A (simple): bajar el umbral del JSON UTF‑8, p.ej. `MAX_JSON_BYTES = 650_000`.
- Opción B (mejor): calcular el tamaño Base64 y comparar contra un máximo conservador.

Recomendación: usar Opción B si se toca `write_file_to_github()`; si no, usar Opción A en `save_player_match_history()`.

## Cambios puntuales a realizar

### 1) `services/github_service.py`

#### 1.1 Ajustar umbral
En `save_player_match_history()`:
- Cambiar `MAX_CONTENTS_BYTES = 900_000` por un valor más conservador (p.ej. 650_000) **o** calcular bytes base64.

Motivo: evitar fallos por el overhead de base64.

#### 1.2 Split por tamaño dentro de semana
En el loop que guarda cada `week_matches`:
- Si `week_bytes > MAX_*`, dividir `week_matches` en partes:
  - `weeks/{wk}-01.json`, `weeks/{wk}-02.json`, ...
- Agregar cada parte a `files`.

Puntos a cuidar:
- Mantener orden (más reciente primero) en cada chunk.
- Evitar duplicados: si ya hay archivos antiguos para esa semana, decidir política (ver “Migración”).

#### 1.3 (Opcional pero recomendable) Mejorar `write_file_to_github()`
En `write_file_to_github()` hoy se loguea tamaño de `content_json` (UTF‑8), pero no el de Base64.
- Agregar log de `len(content_b64)`
- (Opcional) si supera un máximo, devolver `False` antes de llamar a GitHub.

### 2) Migración / convivencia con legacy

Hay dos escenarios:

**Escenario A (sin migración masiva, recomendado para mínimo riesgo):**
- Dejar los legacy `match_history/{puuid}.json` como están.
- Para los jugadores grandes, a partir de ahora se guarda v2/v3 en carpeta.
- `read_player_match_history()` ya prioriza index v2 cuando existe.

**Escenario B (migración controlada):**
- Ejecutar un script que para cada `puuid`:
  - Lee legacy.
  - Llama a `save_player_match_history()` para escribir en v2/v3.
  - (Opcional) borra legacy después de verificar.

Recomendación: solo borrar legacy si estás seguro de que no hay consumidores externos.

## Validación (cómo saber que quedó bien)

### Validación funcional
- Para un jugador “chico”: se sigue guardando legacy (o v2), y `historial_global` / vista de jugador muestra partidas.
- Para un jugador “grande”: se guarda en múltiples archivos; `index.json` lista varios.

### Validación de tamaño
- Confirmar en logs de `write_file_to_github()`:
  - `bytes(JSON)` y `bytes(Base64)` quedan bajo el máximo.

### Validación de lectura
- `read_player_match_history()` debe:
  - Leer `index.json`.
  - Descargar todos los paths de `files`.
  - Combinar y ordenar por `game_end_timestamp`.

## Checklist de implementación

- [ ] 1. Reproducir el fallo con un jugador con muchas partidas (log de error HTTP de GitHub).
- [ ] 2. Confirmar tamaño del payload (JSON + base64) al momento de fallar.
- [ ] 3. Ajustar umbral en `save_player_match_history()` (bajar a ~650KB o medir base64).
- [ ] 4. Implementar split por tamaño dentro de la semana (`weeks/{wk}-NN.json`).
- [ ] 5. Verificar que `index.json` incluya todos los chunks (ordenados recientes→antiguos).
- [ ] 6. Probar guardado de:
  - [ ] jugador pequeño (1 archivo)
  - [ ] jugador mediano (semanal simple)
  - [ ] jugador grande (semanal dividido en N partes)
- [ ] 7. Probar lectura para los 3 casos y validar orden/duplicados.
- [ ] 8. (Opcional) Añadir logs defensivos en `write_file_to_github()` para tamaño base64.
- [ ] 9. Desplegar y monitorear: buscar respuestas no-200/201 en `write_file_to_github`.
- [ ] 10. Actualizar scripts locales que leen `match_history/*.json` (p.ej. `validate_lp_assignments.py`) para soportar carpetas con `index.json` + `files`.
- [ ] 11. (Opcional) Plan de migración controlada para pasar legacy→v2/v3.
- [ ] 12. (Opcional) Mantener compatibilidad externa: generar `match_history/{puuid}.json` recortado (últimas N) si hay consumidores fuera del código.

## Notas operativas
- Si `GITHUB_TOKEN` no está configurado, nada se guarda (ver `write_file_to_github`).
- El proceso de actualización en background está en `services/data_updater.py` y termina llamando a `save_player_match_history()`.

## Consideraciones adicionales (para no llevarse sorpresas)

### Límites y comportamiento de GitHub
- **Contents API y tamaño**: incluso si el JSON pesa < 1MB, la request puede fallar por el overhead de Base64 y por límites prácticos del endpoint.
- **Rate limits**:
  - Sin token o con token con permisos limitados, GitHub puede aplicar rate limit con facilidad.
  - El formato v2/v3 implica **más requests** (index + N chunks). Para lecturas completas (`limit=-1`) el número de requests crece linealmente con el número de chunks.
- **Latencia y timeouts**: `read_file_from_github()` usa timeouts relativamente cortos (raw 30s, API 30s). Con muchos chunks, aumentan las probabilidades de fallos intermitentes.

### Consistencia/atomicidad al escribir (chunks + index)
- El flujo recomendado es: **guardar chunks primero** y **al final** escribir `index.json`.
- Riesgo: si se guardan algunos chunks pero falla el index, esos chunks quedan “huérfanos” (no referenciados por `files`). No es grave funcionalmente, pero hace crecer el repo.
- Riesgo inverso (peor): si se actualiza el index apuntando a chunks que no se llegaron a escribir, la lectura quedaría incompleta.
  - Por eso el index debe incluir **solo** los archivos que se guardaron correctamente.
- Recomendación: en caso de fallo parcial, dejar el index anterior intacto y reintentar la escritura en el siguiente ciclo.

### Concurrencia (múltiples hilos/procesos)
- Si dos workers intentan persistir el mismo jugador a la vez (o dos instancias de la app), pueden pisarse:
  - Ambos leen el mismo SHA y hacen PUT → uno puede fallar con conflicto.
  - Ambos pueden generar index con listas `files` diferentes.
- Mitigaciones posibles:
  - Garantizar un único escritor por PUUID (lock en memoria o cola de trabajos).
  - Reintentos con backoff cuando GitHub devuelva conflicto.

### Duplicados y orden
- Al combinar chunks, el orden final se normaliza ordenando por `game_end_timestamp`.
- Si se re-procesa una partida y se vuelve a insertar por error, el split por chunks no lo evita.
  - Recomendación: mantener un set de `match_id` al construir `matches` para evitar duplicados antes de persistir.

### Rendimiento y costo de lectura
- `get_player_match_history(limit=20)` es barato y debería usarse donde sea posible (como ya hace el index).
- `limit=-1` + v2/v3 puede ser costoso (muchos downloads). Considerar:
  - Optimizar lectura: cargar primero los chunks más recientes y parar cuando se alcance el límite.
  - Cachear en memoria resultados parciales por jugador si el endpoint se consulta mucho.

### Crecimiento del repositorio
- Partir historial en muchos archivos hace que el repo crezca rápido (cada update agrega/actualiza blobs).
- Recomendaciones de operación:
  - Evitar re-escrituras completas innecesarias; solo tocar semanas afectadas.
  - Considerar rotación (p.ej. mantener temporada actual, archivar temporadas pasadas) si el repo empieza a pesar demasiado.

### Compatibilidad hacia atrás y hacia fuera
- Dentro de la app: mientras se use `get_player_match_history()`, el cambio debería ser transparente.
- Fuera de la app:
  - Si alguien consume `match_history/{puuid}.json` directamente, ese path puede dejar de estar actualizado para jugadores grandes.
  - Mitigación opcional: mantener un legacy “recortado” (últimas N partidas) para compatibilidad.

### Rollback
- Si se necesitara revertir, se puede volver a leer desde v2/v3 y re-generar un legacy con las últimas N partidas.
- No conviene depender de un rollback que regenere el JSON completo si el problema original era el tamaño.

### Observabilidad
- Asegurar logs útiles en `write_file_to_github()`:
  - status code + primeros ~500 chars de error (ya existe)
  - tamaño JSON y, si se añade, tamaño Base64
- Monitorizar específicamente:
  - `413/422` (tamaño) y `409` (conflicto SHA)
  - `403` (rate limit o permisos)

### Seguridad
- `GITHUB_TOKEN` debe tener permisos suficientes para escribir (scope típico `repo` si es privado).
- Evitar loguear tokens o URLs que los contengan.

### Alternativas si GitHub se queda corto
- Si el histórico sigue creciendo (o hay demasiadas requests), GitHub deja de ser ideal como “storage”. Alternativas típicas:
  - Objeto en S3/R2/GCS
  - DB simple (SQLite/Postgres)
  - Cache + job de snapshot con retención

## Resultado esperado
Después del cambio, ningún jugador debería dejar de actualizar el historial por tamaño: el sistema partirá automáticamente el historial en archivos suficientemente pequeños para GitHub.
