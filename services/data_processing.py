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

    Args:
        matches (list): A list of matches for the player.
        player_lp_history (dict): A dictionary of LP snapshots for the player, keyed by queue name.

    Returns:
        list: The list of matches with LP change information added.
    """
    for match in matches:
        # Only calculate if the key is missing or None
        if match.get('lp_change_this_game') is None:
            match['lp_change_this_game'] = None # Initialize to None
            queue_id = match.get('queue_id')
            queue_name = "RANKED_SOLO_5x5" if queue_id == 420 else "RANKED_FLEX_SR" if queue_id == 440 else None

            if queue_name and queue_name in player_lp_history:
                lp_change, elo_before, elo_after = calculate_lp_change(match, matches, player_lp_history[queue_name])
                match['lp_change_this_game'] = lp_change
                match['pre_game_valor_clasificacion'] = elo_before
                match['post_game_valor_clasificacion'] = elo_after
    return matches
