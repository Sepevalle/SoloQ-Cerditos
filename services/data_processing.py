from datetime import datetime, timedelta, timezone

def calculate_lp_change_robust(match, all_matches_for_player, player_queue_lp_history):
    """
    VERSIÓN MEJORADA: Calcula el cambio de LP para una partida de forma robusta y consistente.
    Usa múltiples estrategias de fallback para garantizar resultados precisos.

    Args:
        match (dict): La partida para la que calcular el cambio de LP.
        all_matches_for_player (list): Todas las partidas del jugador en la temporada actual.
        player_queue_lp_history (list): Snapshots de LP/ELO para la cola específica.

    Returns:
        tuple: (lp_change, elo_before, elo_after) donde cualquiera puede ser None si no se puede determinar.
    """
    game_end_ts = match.get('game_end_timestamp', 0)
    queue_id = match.get('queue_id')
    match_id = match.get('match_id')

    if not game_end_ts or not queue_id:
        return None, None, None

    # === ESTRATEGIA 1: Usar snapshots históricos con validación estricta ===
    snapshots = sorted(player_queue_lp_history, key=lambda x: x['timestamp'])
    
    if not snapshots:
        return None, None, None

    # Encontrar el snapshot justo antes y después de la partida
    snapshot_before = None
    snapshot_after = None
    
    for snapshot in reversed(snapshots):
        if snapshot['timestamp'] < game_end_ts:
            snapshot_before = snapshot
            break
    
    for snapshot in snapshots:
        if snapshot['timestamp'] > game_end_ts:
            snapshot_after = snapshot
            break

    # Si tenemos ambos snapshots, verificar que NO hay otras partidas de la misma cola entre ellos
    if snapshot_before and snapshot_after:
        # Filtrar solo partidas de la misma cola y que no sean la actual
        matches_in_queue = [
            m for m in all_matches_for_player 
            if m.get('queue_id') == queue_id and m.get('match_id') != match_id
        ]
        
        # Verificar si hay partidas entre los snapshots
        matches_between_snapshots = [
            m for m in matches_in_queue
            if snapshot_before['timestamp'] < m.get('game_end_timestamp', 0) < snapshot_after['timestamp']
        ]
        
        # Si hay solo una partida entre snapshots (la nuestra), podemos usar este método
        if len(matches_between_snapshots) == 0:
            elo_before = snapshot_before.get('elo', 0)
            elo_after = snapshot_after.get('elo', 0)
            
            if elo_before > 0 and elo_after > 0:
                lp_change = elo_after - elo_before
                return lp_change, elo_before, elo_after

    # === ESTRATEGIA 2: Usar la diferencia entre partidas consecutivas ===
    # Ordenar partidas de la misma cola por tiempo
    queue_matches = sorted(
        [m for m in all_matches_for_player if m.get('queue_id') == queue_id],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    
    # Encontrar el índice de la partida actual
    current_idx = next((i for i, m in enumerate(queue_matches) if m.get('match_id') == match_id), None)
    
    if current_idx is not None:
        # Si hay una partida anterior con ELO post-match, usarlo como ELO pre-match
        if current_idx > 0:
            prev_match = queue_matches[current_idx - 1]
            elo_before = prev_match.get('post_game_valor_clasificacion')
            
            # Si hay una partida siguiente con ELO pre-match, usarlo como ELO post-match
            if current_idx < len(queue_matches) - 1:
                next_match = queue_matches[current_idx + 1]
                elo_after = next_match.get('pre_game_valor_clasificacion')
                
                if elo_before is not None and elo_after is not None:
                    lp_change = elo_after - elo_before
                    return lp_change, elo_before, elo_after

    # === ESTRATEGIA 3: Usar snapshots más cercanos (con riesgo de ambigüedad) ===
    # Este método es menos preciso pero útil cuando hay múltiples partidas entre snapshots
    closest_snapshot_before = None
    closest_snapshot_after = None
    
    min_time_before = float('inf')
    min_time_after = float('inf')
    
    for snapshot in snapshots:
        if snapshot['timestamp'] < game_end_ts:
            time_diff = game_end_ts - snapshot['timestamp']
            if time_diff < min_time_before:
                min_time_before = time_diff
                closest_snapshot_before = snapshot
        else:
            time_diff = snapshot['timestamp'] - game_end_ts
            if time_diff < min_time_after:
                min_time_after = time_diff
                closest_snapshot_after = snapshot
    
    if closest_snapshot_before and closest_snapshot_after:
        elo_before = closest_snapshot_before.get('elo', 0)
        elo_after = closest_snapshot_after.get('elo', 0)
        
        if elo_before > 0 and elo_after > 0:
            # Validación: si hay múltiples partidas, usar estimación conservadora
            matches_between = [
                m for m in all_matches_for_player
                if m.get('queue_id') == queue_id 
                and m.get('match_id') != match_id
                and closest_snapshot_before['timestamp'] < m.get('game_end_timestamp', 0) < closest_snapshot_after['timestamp']
            ]
            
            lp_change = elo_after - elo_before
            
            # Marcar como ambiguo si hay múltiples partidas
            if len(matches_between) > 0:
                # Devolver con cautela - solo si es una estimación razonable
                return None, elo_before, elo_after
            
            return lp_change, elo_before, elo_after

    # Fallback: retornar None si no se puede determinar con confianza
    return None, None, None


def calculate_lp_change(match, all_matches_for_player, player_queue_lp_history):
    """
    Mantener compatibilidad hacia atrás con el nombre anterior.
    """
    return calculate_lp_change_robust(match, all_matches_for_player, player_queue_lp_history)

def process_player_match_history(matches, player_lp_history):
    """
    Procesa el historial de partidas de un jugador para calcular cambios de LP.
    MEJORADO: Usa la función calculate_lp_change_robust para mayor precisión.

    Args:
        matches (list): Lista de partidas del jugador.
        player_lp_history (dict): Diccionario de snapshots de LP del jugador, indexado por nombre de cola.

    Returns:
        list: Lista de partidas con información de cambio de LP añadida.
    """
    # Ordenar partidas por timestamp descendente (más recientes primero)
    matches_sorted = sorted(matches, key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    # Inicializar campos de LP para todas las partidas
    for match in matches_sorted:
        if 'lp_change_this_game' not in match:
            match['lp_change_this_game'] = None
        if 'pre_game_valor_clasificacion' not in match:
            match['pre_game_valor_clasificacion'] = None
        if 'post_game_valor_clasificacion' not in match:
            match['post_game_valor_clasificacion'] = None

    # Procesar cada partida usando la estrategia robusta mejorada
    for match in matches_sorted:
        # Solo calcular si no tiene valor válido
        if match.get('lp_change_this_game') is None:
            queue_id = match.get('queue_id')
            
            # Mapear queue_id a nombre de cola
            queue_name = None
            if queue_id == 420:
                queue_name = "RANKED_SOLO_5x5"
            elif queue_id == 440:
                queue_name = "RANKED_FLEX_SR"
            
            if queue_name and queue_name in player_lp_history:
                # Usar la estrategia robusta mejorada
                lp_change, elo_before, elo_after = calculate_lp_change_robust(
                    match, 
                    matches_sorted, 
                    player_lp_history[queue_name]
                )

                if lp_change is not None:
                    match['lp_change_this_game'] = lp_change
                    match['pre_game_valor_clasificacion'] = elo_before
                    match['post_game_valor_clasificacion'] = elo_after

    return matches_sorted
