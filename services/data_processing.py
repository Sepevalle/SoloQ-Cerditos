from datetime import datetime, timedelta, timezone

def calculate_lp_change(match, all_matches_for_player, player_queue_lp_history):
    """
    Calculates the LP gain/loss for a single match robustly.

    Args:
        match (dict): The match to calculate the LP change for.
        all_matches_for_player (list): A list of all matches for the player in the current season.
        player_queue_lp_history (list): A list of LP snapshots for the player in the specific queue.

    Returns:
        tuple: A tuple containing the LP change (int or None), ELO before the match (int or None),
               and ELO after the match (int or None).
    """
    game_end_ts = match.get('game_end_timestamp', 0)
    queue_id = match.get('queue_id')

    if not game_end_ts or not queue_id:
        return None, None, None

    snapshots = sorted(player_queue_lp_history, key=lambda x: x['timestamp'])
    
    snapshot_before, snapshot_after = None, None
    # Find the snapshot just before the match
    for snapshot in reversed(snapshots):
        if snapshot['timestamp'] < game_end_ts:
            snapshot_before = snapshot
            break
    
    # Find the snapshot just after the match
    for snapshot in snapshots:
        if snapshot['timestamp'] > game_end_ts:
            snapshot_after = snapshot
            break

    # Case 1: Both snapshots exist
    if snapshot_before and snapshot_after:
        # Ensure no other match is between the snapshots
        is_clean_change = True
        for other_match in all_matches_for_player:
            if other_match['match_id'] != match['match_id'] and other_match.get('queue_id') == queue_id:
                other_ts = other_match.get('game_end_timestamp', 0)
                if snapshot_before['timestamp'] < other_ts < snapshot_after['timestamp']:
                    is_clean_change = False
                    break
        
        if is_clean_change:
            elo_before = snapshot_before.get('elo', 0)
            elo_after = snapshot_after.get('elo', 0)
            
            if elo_before == 0 or elo_after == 0:
                return None, None, None

            lp_change = elo_after - elo_before
            return lp_change, elo_before, elo_after

    # Case 2: Only 'after' snapshot exists (likely first game)
    elif snapshot_after and not snapshot_before:
        is_clean_window = True
        for other_match in all_matches_for_player:
            if other_match['match_id'] != match['match_id'] and other_match.get('queue_id') == queue_id:
                other_ts = other_match.get('game_end_timestamp', 0)
                if game_end_ts < other_ts < snapshot_after['timestamp']:
                    is_clean_window = False
                    break
        
        if is_clean_window:
            elo_after = snapshot_after.get('elo', 0)
            if elo_after != 0:
                # We can't know lp_change or elo_before, but we know elo_after
                return None, None, elo_after

    # Default case: Not enough info
    return None, None, None

def process_player_match_history(matches, player_lp_history):
    """
    Processes a player's match history to calculate LP changes for each match.
    MEJORADO: Ahora intenta múltiples métodos de cálculo para minimizar valores null.

    Args:
        matches (list): A list of matches for the player.
        player_lp_history (dict): A dictionary of LP snapshots for the player, keyed by queue name.

    Returns:
        list: The list of matches with LP change information added.
    """
    # Ordenar matches por timestamp descendente (más recientes primero)
    matches_sorted = sorted(matches, key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    # Ensure all matches have the lp_change_this_game key initialized
    for match in matches_sorted:
        if 'lp_change_this_game' not in match:
            match['lp_change_this_game'] = None
        if 'pre_game_valor_clasificacion' not in match:
            match['pre_game_valor_clasificacion'] = None
        if 'post_game_valor_clasificacion' not in match:
            match['post_game_valor_clasificacion'] = None

    for i, match in enumerate(matches_sorted):
        # Solo calcular si no tiene valor válido
        if match.get('lp_change_this_game') is None:
            queue_id = match.get('queue_id')
            queue_name = "RANKED_SOLO_5x5" if queue_id == 420 else "RANKED_FLEX_SR" if queue_id == 440 else None

            if queue_name and queue_name in player_lp_history:
                # Método 1: Usar snapshots históricos de la API
                lp_change, elo_before, elo_after = calculate_lp_change(match, matches, player_lp_history[queue_name])

                # Método 2: Si Método 1 falló, buscar la partida anterior en la misma cola
                if lp_change is None and i + 1 < len(matches_sorted):
                    for next_match in matches_sorted[i+1:]:
                        if next_match.get('queue_id') == queue_id and next_match.get('post_game_valor_clasificacion') is not None:
                            # La partida anterior está disponible
                            elo_before = next_match.get('post_game_valor_clasificacion')
                            # Buscar la siguiente partida en la misma cola para obtener post_game
                            for prev_match in matches_sorted[:i]:
                                if prev_match.get('queue_id') == queue_id and prev_match.get('post_game_valor_clasificacion') is not None:
                                    elo_after = prev_match.get('post_game_valor_clasificacion')
                                    if elo_before is not None and elo_after is not None:
                                        lp_change = elo_after - elo_before
                                    break
                            break

                if lp_change is not None:
                    match['lp_change_this_game'] = lp_change
                    match['pre_game_valor_clasificacion'] = elo_before
                    match['post_game_valor_clasificacion'] = elo_after
                else:
                    match['lp_change_this_game'] = None
                    match['pre_game_valor_clasificacion'] = None
                    match['post_game_valor_clasificacion'] = None
    
    return matches_sorted
