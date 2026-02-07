# 🔍 Explicación Detallada de `procesar_jugador()`

## 📌 Propósito General
`procesar_jugador()` es la **función principal de actualización** del servidor. Se ejecuta **CADA 5 MINUTOS** para TODOS los jugadores y es responsable de:
- Obtener datos frescos del jugador (Elo actual, si está en partida)
- Procesar partidas nuevas
- Guardar historial en GitHub
- Actualizar caché en memoria

---

## 🔄 Flujo Completo de procesar_jugador()

### PASO 1: Verificar si el jugador está en partida (LLAMADA LIGERA A API)
```python
game_data = esta_en_partida(api_key_spectator, puuid, riot_id=riot_id)
is_currently_in_game = game_data is not None
```
- **COSTO**: 1 request a API (usa clave secundaria para no saturar)
- **OBTIENE**: Si el jugador está jugando AHORA y datos de la partida actual
- **USA**: Para determinar si necesita actualización profunda

---

### PASO 2: Decidir si hacer actualización COMPLETA o LIGERA
```python
was_in_game_before = old_data_list and any(d.get('en_partida') for d in old_data_list)
needs_full_update = not old_data_list or is_currently_in_game or was_in_game_before
```

**Hace actualización COMPLETA (costosa) SI:**
- El jugador es NUEVO (no hay old_data_list)
- El jugador está EN PARTIDA AHORA
- El jugador ESTABA en partida hace 5min (acaba de terminar)

**Hace actualización LIGERA SI:**
- El jugador no está jugando y tampoco estaba antes (inactivo)

---

### PASO 3: Obtener Elo Actual (SIEMPRE - LLAMADA A API)
```python
elo_info = obtener_elo(api_key_main, puuid, riot_id=riot_id)
```

**SIEMPRE se llama aunque sea actualización ligera**
- **COSTO**: 1 request a API
- **OBTIENE**: 
  - Elo actual en SoloQ (tier, rank, LP)
  - Elo actual en Flex (tier, rank, LP)
  - W/L record
- **OBJETIVO**: Mantener ratings siempre frescos

---

### PASO 4: Leer Historial PARCIAL de GitHub (SOLO si actualización completa)
```python
player_match_history_data = get_player_match_history(puuid, limit=150)
existing_matches = player_match_history_data.get('matches', [])
```

**⚠️ PREGUNTA DEL USUARIO: "¿Por qué cargas 150 si ya tengo lo antiguo?"**

**RESPUESTA**: No carga TODO lo antiguo, solo últimas 150 porque:
1. Necesita saber qué match_ids YA TIENE para no procesarlos de nuevo
2. Necesita los ELO de partidas anteriores para calcular LP changes (ej: si la partida anterior fue +50 LP, esta debe restar 50 del actual)
3. No necesita TODAS las partidas antiguas, solo las recientes para continuidad

**Optimización**: Cuando hace `obtener_historial_partidas()` le pide 100 últimas a Riot API, así que 150 en caché es más que suficiente para cubrir.

---

### PASO 5: Si es actualización COMPLETA - Obtener partidas nuevas de API
```python
all_match_ids = obtener_historial_partidas(api_key_main, puuid, count=100)
```

**COSTO**: 1 request a API
**OBTIENE**: 100 últimos match_ids del jugador
**FILTRA**: Solo nuevas (no en existing_matches, no en remakes)
**LIMITA**: Máximo 30 nuevas por ciclo (para no saturar)

---

### PASO 6: Procesar cada nueva partida EN PARALELO
```python
with ThreadPoolExecutor(max_workers=5) as executor:
    resultados_partidas = executor.map(obtener_info_partida, tareas_partidas)
```

**COSTO**: 1 request por partida nueva (máximo 30)
**OBTIENE**:
- Nombre del campeón jugado
- Resultado (Victoria/Derrota)
- KDA (Kills, Deaths, Assists)
- CS (creep score)
- Duración de partida
- Elos pre-game y post-game

**OPTIMIZACIÓN**: Se hace EN PARALELO (5 workers) para no tardar 30 segundos

---

### PASO 7: Filtrar solo SoloQ/Flex y guardar
```python
ranked_only_matches = [m for m in all_matches_for_player if m.get('queue_id') in [420, 440]]
```

**Se guarda en GitHub**:
- Solo partidas SoloQ (420) y Flex (440)
- Se descartan ARAM, Normal, Co-op, etc.
- Se actualiza caché en memoria

---

## 🤔 ¿ENTONCES POR QUÉ NECESITA 150 PARTIDAS?

### Escenario: Un jugador tiene 5 partidas nuevas
```
Estado anterior: 
  - Partido 100: Post-game ELO = 2000
  - Partido 101: Post-game ELO = 2050
  - ... (otros 98)

nueva partida:
  - Partido 105 (NUEVA): Pre-game ELO = ? / Post-game ELO = 2100

¿CÓMO CALCULA LP CHANGE?
1. Busca la partida anterior (101) en el historial
2. Lee su post-game ELO (2050)
3. Lee el post-game ELO de esta partida (2100)
4. LP change = 2100 - 2050 = +50
```

**Si NO tuviera las 150 partidas anteriores**:
- NO sabría cuál fue el post-game ELO de la partida anterior
- NO podría calcular LP change correctamente

**Pero espera, ¿el usuario pregunta si puede ver el HISTORIAL COMPLETO?**

La respuesta es: **NO necesita el historial COMPLETO**, solo las últimas partidas para calcular cambios. Pero el usuario dice que ya tiene lo antiguo en GitHub... 

**INSIGHT**: El usuario sugiere que quizás deberíamos:
1. Guardar en GitHub actualizado
2. Cuando lee, traer TODAS de GitHub (porque ya están ahí)
3. Pero en caché de memoria solo mantener las últimas 150

Esto es lo que YA estamos haciendo, así que está bien.

---

## 📊 RESUMEN DE LO QUE OBTIENE

### Por ciclo (cada 5 minutos):
1. ✅ Elo actual (SoloQ + Flex)
2. ✅ Partidas nuevas (máximo 30)
3. ✅ Detalles de cada nueva partida (campeón, KDA, resultado)
4. ✅ Datos del cambio de LP in cada nueva
5. ✅ Si está jugando ahora (para mostrar en web)

### Lo que ACTUALIZA en GitHub:
- ✅ Solo partidas SoloQ y Flex
- ✅ Las últimas 150 (limitado para memoria)
- ✅ Caché local con timestamp

### Lo que NO obtiene:
- ❌ Partidas ARAM, Normal (se descartan)
- ❌ Más de 100 partidas antiguas (optimización)
- ❌ Análisis profundo de partidas (eso es aparte)

---

## 💡 MEJORA SUGERIDA POR EL USUARIO

**Actual**: Lee 150 cada vez que necesita actualizar

**Mejor**: 
1. Guardar TODA la información en GitHub (una sola vez)
2. En caché local mantener solo últimas 150
3. Solo obtener "deltas" (partidas nuevas)

**¿Esto está implementado?**
Técnicamente casi, pero podría optimizarse más. El usuario sugiere que simplemente compare:
- ELO anterior guardado en GitHub
- ELO actual de API
- Si subió, actualiza GitHub

Esto es más eficiente que re-procesar todo el historial.
