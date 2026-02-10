# Fix: Jugador en partida no se está revisando correctamente

## Problema Identificado
- El estado `en_partida` se obtiene del caché de estadísticas, que tiene un TTL de 5 minutos
- Cuando hay datos en caché, la verificación de partida activa se omite completamente
- El `_last_live_check` no persiste correctamente entre peticiones

## Solución
Separar la verificación de partida activa del caché de estadísticas generales, para que:
1. Siempre se verifique si el jugador está en partida (con su propio TTL más corto)
2. Las demás estadísticas (top campeones, rachas, etc.) sigan usando el caché existente

## Archivos Modificados
- [x] `blueprints/main.py` - Separar la lógica de verificación de partida activa
- [x] `services/data_updater.py` - Filtrar partidas por cola (SoloQ y Flex) antes de guardar

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
3. ✅ Logs actualizados para mostrar cuando una partida es descartada por cola no permitida

## Resumen del Fix
- **Problema 1**: El estado `en_partida` solo se actualizaba cuando no había caché de estadísticas, causando que los jugadores nunca aparecieran "en partida" si sus stats estaban cacheadas.
- **Solución 1**: Separar completamente la verificación de partida activa del caché de estadísticas generales. Ahora se verifica siempre con un caché propio de 60 segundos para no saturar la API.

- **Problema 2**: Se estaban descargando y guardando partidas de todas las colas (ARAM, Normales, etc.) en el historial, cuando solo deberían ser SoloQ y Flex.
- **Solución 2**: Filtrar las partidas por `queue_id` antes de guardarlas, permitiendo solo 420 (SoloQ) y 440 (Flex).

## Resultado
- Los jugadores ahora se mostrarán correctamente como "en partida" cuando estén jugando, independientemente de si sus otras estadísticas están en caché
- El historial de partidas solo contendrá partidas de SoloQ y Flex, mejorando la precisión de las estadísticas
- Se eliminan partidas de ARAM, Normales, y otras colas del historial
