# Fix: Jugador en partida no se está revisando correctamente + Cálculo de LP

## Problemas Identificados

### 1. Verificación de partida activa
- El estado `en_partida` se obtiene del caché de estadísticas, que tiene un TTL de 5 minutos
- Cuando hay datos en caché, la verificación de partida activa se omite completamente
- El `_last_live_check` no persiste correctamente entre peticiones

### 2. Filtrado de partidas por cola
- Se estaban descargando y guardando partidas de todas las colas (ARAM, Normales, etc.)
- Solo deberían guardarse SoloQ (420) y Flex (440)

### 3. Cálculo de LP en partidas
- Las partidas se guardaban sin calcular los cambios de LP
- No se asignaban los campos `lp_change_this_game`, `pre_game_valor_clasificacion`, `post_game_valor_clasificacion`

## Archivos Modificados
- [x] `blueprints/main.py` - Separar la lógica de verificación de partida activa
- [x] `services/data_updater.py` - Filtrar partidas por cola + Calcular LP antes de guardar

## Cambios Implementados en `blueprints/main.py`
1. ✅ Agregado `import time` que faltaba
2. ✅ Mover la verificación `esta_en_partida` fuera del bloque `if cached_stats:` - Ahora se ejecuta SIEMPRE para cada jugador
3. ✅ Crear lógica de caché separada para el estado de partida con TTL de 60 segundos (`_live_game_cache`)
4. ✅ Asegurar que `en_partida` y `nombre_campeon` siempre sean valores frescos
5. ✅ Eliminar el `else` problemático que reseteaba valores cuando había caché
6. ✅ Agregar manejo de caso cuando no hay PUUID (valores por defecto)

## Cambios Implementados en `services/data_updater.py`
1. ✅ Agregado filtro de colas permitidas: `ALLOWED_QUEUE_IDS = {420, 440}`
   - 420 = RANKED_SOLO_5x5 (SoloQ)
   - 440 = RANKED_FLEX_SR (Flex)
2. ✅ Filtrar partidas por `queue_id` antes de guardar en el historial
3. ✅ Agregado cálculo de LP antes de guardar partidas:
   - Leer `lp_history.json` para obtener snapshots de ELO
   - Usar `process_player_match_history()` para calcular cambios de LP
   - La función `calculate_lp_change_robust()` asigna el LP a la **última partida** de cada ventana entre snapshots (evita duplicación)
4. ✅ Logs actualizados para mostrar progreso del cálculo de LP

## Lógica de Cálculo de LP (Anti-Duplicación)
La función `calculate_lp_change_robust()` en `services/data_processing.py` implementa:

```python
# Si hay varias partidas entre snapshots, asignar todo el delta
# únicamente al ÚLTIMO partido (mayor timestamp) dentro del intervalo.
last_match = max(matches_between_snapshots, key=lambda x: x.get('game_end_timestamp', 0))
if last_match.get('match_id') == match_id:
    lp_change = elo_after - elo_before
    return lp_change, elo_before, elo_after
# Si no somos el último partido, no asignamos aquí.
```

Esto garantiza que cuando múltiples partidas caen entre dos snapshots de ELO, solo la última partida recibe el cambio de LP completo, evitando la duplicación de importes.

## Resumen del Fix
- **Problema 1**: El estado `en_partida` solo se actualizaba cuando no había caché de estadísticas
- **Solución 1**: Separar completamente la verificación de partida activa del caché de estadísticas generales con TTL de 60 segundos

- **Problema 2**: Se estaban guardando partidas de ARAM, Normales, etc. en el historial
- **Solución 2**: Filtrar por `queue_id` antes de guardar, permitiendo solo 420 y 440

- **Problema 3**: Las partidas se guardaban sin cálculo de LP
- **Solución 3**: Calcular LP usando `lp_history.json` y `process_player_match_history()` antes de guardar, con lógica anti-duplicación

## Resultado
- Los jugadores ahora se mostrarán correctamente como "en partida" cuando estén jugando
- El historial solo contendrá partidas de SoloQ y Flex
- Las partidas nuevas tendrán correctamente calculados sus cambios de LP
- No hay duplicación de LP cuando múltiples partidas caen entre snapshots
