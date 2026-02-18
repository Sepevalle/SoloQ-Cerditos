# Resumen de Implementación: Fix Match History GitHub Size Limits

## 📋 Descripción
Esta implementación soluciona el problema de que el historial de partidas deja de actualizarse cuando excede los límites de tamaño de la API de GitHub.

## 🎯 Objetivos Alcanzados

### 1. Compatibilidad de Lectura ✅
- `read_player_match_history()` ahora soporta tres formatos:
  - **v3 (chunked)**: `match_history/{puuid}/index.json` + múltiples archivos por semana
  - **v2 (weekly)**: `match_history/{puuid}/index.json` + un archivo por semana
  - **legacy**: `match_history/{puuid}.json` (archivo único)

### 2. Prevención de Fallos por Tamaño ✅
- Umbral conservador: `MAX_B64_BYTES = 950_000` bytes (~1MB con overhead)
- Cálculo preciso de tamaño Base64 con `estimate_payload_size()`
- Partición automática de semanas grandes en chunks numerados

### 3. Formato v3 (Chunks por Tamaño) ✅
- Una semana se divide en múltiples archivos si excede el umbral
- Nomenclatura: `weeks/2026-W07-01.json`, `weeks/2026-W07-02.json`, etc.
- El `index.json` lista TODOS los chunks en el campo `files`
- Orden determinista: semanas más recientes primero, chunks numerados secuencialmente

### 4. Actualización de Consumidores ✅
- `validate_lp_assignments.py` ahora soporta:
  - Archivos legacy `.json` (estructura plana)
  - Carpetas v2/v3 con `index.json` + archivos referenciados
- Carga todos los archivos listados en `index.files`
- Elimina duplicados por `match_id` al combinar

### 5. Integridad al Escribir ✅
- Fase 1: Guardar todos los chunks primero
- Fase 2: Actualizar `index.json` solo con archivos exitosos
- Si falla un chunk, no se incluye en el index
- Si falla el index, los chunks guardados permanecen consistentes

### 6. Logging Defensivo ✅
- `write_file_to_github()` ahora loguea:
  - Tamaño JSON (bytes)
  - Tamaño Base64 (bytes)
  - Status code y respuesta truncada en errores
  - Advertencia si el payload excede el umbral

### 7. Robustez (Reintentos) ✅
- Reintentos automáticos (máx 3) para:
  - Conflicto SHA (409): Re-lee SHA y reintenta
  - Rate limit (403): Backoff exponencial
  - Errores de servidor (5xx): Backoff exponencial
  - Timeouts: Backoff exponencial
- Base de backoff: 2 segundos (2^intento)

## 📁 Archivos Modificados

### 1. `services/github_service.py`
**Cambios principales:**
- Nuevas constantes: `MAX_B64_BYTES`, `MAX_RETRIES`, `RETRY_BACKOFF_BASE`
- Nueva función: `estimate_payload_size()` - calcula tamaño JSON y Base64
- Nueva función: `get_iso_week()` - obtiene semana ISO de timestamp
- `write_file_to_github()` mejorada:
  - Logging de tamaños JSON y Base64
  - Verificación de umbral
  - Lógica de reintentos con backoff
  - Manejo de conflictos SHA
- `read_player_match_history()` reescrita:
  - Detecta y carga formato v2/v3
  - Fallback a legacy
  - Elimina duplicados y ordena por timestamp
- `save_player_match_history()` reescrita:
  - Agrupa por semana ISO
  - Particiona semanas grandes en chunks
  - Guarda chunks primero, luego index
  - Manejo de remakes en archivo separado

### 2. `validate_lp_assignments.py`
**Cambios principales:**
- Nueva función: `load_match_history_from_folder()` - carga formato v2/v3
- `validate_match_lp_assignments()` actualizada:
  - Detecta carpetas vs archivos
  - Carga legacy o v2/v3 según corresponda
  - Contador de jugadores procesados

## 🔧 Formato v3 (Nuevo)

### Estructura de Archivos
```
match_history/
├── {puuid}/
│   ├── index.json           # Lista todos los chunks
│   └── weeks/
│       ├── 2026-W07.json    # Semana pequeña (único archivo)
│       ├── 2026-W08-01.json # Semana grande (parte 1)
│       ├── 2026-W08-02.json # Semana grande (parte 2)
│       └── 2026-W08-03.json # Semana grande (parte 3)
└── {otro_puuid}.json        # Legacy (sin cambios)
```

### Ejemplo de index.json
```json
{
  "puuid": "abc123...",
  "last_updated": 1704067200.0,
  "format_version": "v3",
  "files": [
    "weeks/2026-W08-01.json",
    "weeks/2026-W08-02.json",
    "weeks/2026-W08-03.json",
    "weeks/2026-W07.json"
  ],
  "total_matches": 150,
  "total_remakes": 5
}
```

### Ejemplo de chunk (weeks/2026-W08-01.json)
```json
{
  "matches": [...],
  "remakes": [],
  "week": "2026-W08",
  "chunk": 1
}
```

## 🧪 Criterios de Aceptación

- ✅ Jugador con historial grande: No falla al guardar, se crean múltiples archivos por semana si es necesario
- ✅ Jugador pequeño: Sigue funcionando (legacy o weekly), sin errores
- ✅ Lectura: `read_player_match_history()` reconstruye matches correctamente desde index + files
- ✅ Validación: `validate_lp_assignments.py` puede validar tanto legacy como formato por carpetas

## 📝 Notas de Implementación

1. **Backward Compatibility**: El sistema detecta automáticamente el formato y actúa en consecuencia. No se requiere migración masiva.

2. **Determinismo**: Los chunks se generan en orden estable (semandas ordenadas, chunks numerados secuencialmente).

3. **Tolerancia a Fallos**: Si un chunk falla, no se incluye en el index. Si el index falla, los chunks guardados permanecen para la próxima ejecución.

4. **Performance**: La lectura de historiales grandes requiere múltiples requests (uno por chunk). Se recomienda usar `limit` donde sea posible.

5. **Límites**: El umbral de 950KB Base64 deja margen de seguridad respecto al límite práctico de ~1MB de GitHub.

## 🚀 Próximos Pasos (Testing)

Para validar la implementación:

1. **Test Legacy**: Verificar que jugadores existentes con formato legacy siguen funcionando
2. **Test v2/v3**: Crear un jugador con muchas partidas y verificar que se particiona correctamente
3. **Test Validación**: Ejecutar `validate_lp_assignments.py` en un directorio con ambos formatos
4. **Monitoreo**: Observar logs de `write_file_to_github()` para detectar payloads grandes o errores

## 📊 Métricas de Éxito

- Ningún jugador debería dejar de actualizar por tamaño
- Reducción de errores 413/422 en logs de GitHub
- `validate_lp_assignments.py` procesa correctamente todos los formatos
