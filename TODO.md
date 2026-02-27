# TODO - Implementación de Sistema de Actualización Eficiente

## Pasos a completar:

### 1. Añadir endpoint API para actualización manual de jugador
- [ ] Crear endpoint en blueprints/api.py para actualizar un jugador específico
- [ ] El endpoint recibirá el puuid y llamará a la función existente

### 2. Añadir botón en perfil de jugador
- [ ] Modificar templates/jugador.html para incluir botón de actualización
- [ ] Añadir estilos y lógica JavaScript para el botón
- [ ] El botón llamará al endpoint API de actualización

### 3. Optimizar detección de jugadores inactivos
- [ ] Mejorar la lógica en data_updater_new.py para no hacer llamadas API a jugadores inactivos
- [ ] El ciclo de 48h ya existe, pero optimizaremos para solo actualizar jugadores activos

### 4. Mejorar sistema de recuperación de partidas al terminar partida
- [ ] Ya existe _check_all_players_live_games(), optimizar para recuperar solo partidas nuevas
- [ ] Ya existe actualizar_jugador_especifico() para actualización incremental
