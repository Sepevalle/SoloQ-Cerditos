# TODO: Fix Sistema de Llamadas a IA

## Objetivo
Implementar sistema automático de 24 horas para llamadas a IA con posibilidad de forzar manualmente.

## Pasos a completar

### 1. services/github_service.py
- [ ] Actualizar `read_player_permission()` para verificar tiempo transcurrido
- [ ] Añadir campos: `ultima_llamada`, `proxima_llamada_disponible`, `modo_forzado`
- [ ] Implementar lógica de rehabilitación automática después de 24h
- [ ] Actualizar `save_player_permission()` para manejar nuevos campos

### 2. services/ai_service.py
- [ ] Actualizar `block_player_permission()` para registrar timestamp
- [ ] Añadir función `get_time_until_next_analysis()`
- [ ] Añadir función `is_analysis_available()`
- [ ] Actualizar `analyze_matches()` para incluir metadata de tiempo

### 3. blueprints/api.py
- [ ] Mejorar endpoint `/analisis-ia/<puuid>` con verificación de tiempo
- [ ] Añadir parámetro opcional `?force=true`
- [ ] Devolver información clara sobre tiempo restante
- [ ] Manejar caso de análisis forzado manualmente

### 4. templates/jugador.html
- [ ] Mostrar contador de tiempo restante
- [ ] Mostrar badge de "Automático" vs "Forzado"
- [ ] Mejorar mensajes de error con tiempo exacto
- [ ] Añadir opción de forzar análisis cuando corresponda

## Estado
En progreso...
