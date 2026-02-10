# Fix: Jugador en partida no se está revisando correctamente

## Problema Identificado
- El estado `en_partida` se obtiene del caché de estadísticas, que tiene un TTL de 5 minutos
- Cuando hay datos en caché, la verificación de partida activa se omite completamente
- El `_last_live_check` no persiste correctamente entre peticiones

## Solución
Separar la verificación de partida activa del caché de estadísticas generales, para que:
1. Siempre se verifique si el jugador está en partida (con su propio TTL más corto)
2. Las demás estadísticas (top campeones, rachas, etc.) sigan usando el caché existente

## Archivos a Modificar
- [x] `blueprints/main.py` - Separar la lógica de verificación de partida activa


## Cambios Implementados
1. ✅ Mover la verificación `esta_en_partida` fuera del bloque `if cached_stats:` - Ahora se ejecuta SIEMPRE para cada jugador
2. ✅ Crear lógica de caché separada para el estado de partida con TTL de 60 segundos (`_live_game_cache`)
3. ✅ Asegurar que `en_partida` y `nombre_campeon` siempre sean valores frescos
4. ✅ Eliminar el `else` problemático que reseteaba valores cuando había caché
5. ✅ Agregar manejo de caso cuando no hay PUUID (valores por defecto)

## Resumen del Fix
- **Problema**: El estado `en_partida` solo se actualizaba cuando no había caché de estadísticas, causando que los jugadores nunca aparecieran "en partida" si sus stats estaban cacheadas.
- **Solución**: Separar completamente la verificación de partida activa del caché de estadísticas generales. Ahora se verifica siempre con un caché propio de 60 segundos para no saturar la API.
- **Resultado**: Los jugadores ahora se mostrarán correctamente como "en partida" cuando estén jugando, independientemente de si sus otras estadísticas están en caché.
