# TODO - Sistema de Actualización Eficiente de Jugadores

## Objetivo
Optimizar las llamadas a la API de Riot evitando actualizaciones innecesarias para jugadores inactivos.

## Tareas a completar:

### 1. Modificar data_updater.py
- [ ] Cambiar intervalo de actualización completa de 10 min a 48 horas
- [ ] Asegurar que el worker de verificación de estado "en partida" llame a actualización incremental cuando un jugador termina partida
- [ ] Implementar lógica para solo actualizar jugadores que han jugado recientemente

### 2. Añadir endpoint API para actualización manual
- [ ] Crear endpoint `/api/actualizar-jugador/<puuid>` en blueprints/api.py
- [ ] Implementar función de actualización manual que permita forzar actualización

### 3. Añadir botón en perfil de jugador
- [ ] Añadir botón de actualización manual en templates/jugador.html
- [ ] Conectar con el endpoint API

### 4. Testing y verificación
- [ ] Verificar que el sistema funciona correctamente
- [ ] Monitorizar llamadas API

## Notas:
- El sistema existente ya tiene:
  - `player_update_tracker.py` con seguimiento de estado
  - `actualizar_jugador_especifico()` para actualizaciones incrementales
  - `_check_all_players_live_games()` que verifica cada 2 minutos
- Solo necesitamos ajustar la lógica y añadir el botón manual
