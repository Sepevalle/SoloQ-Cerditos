"""
Servicio de gestión de partidas.
Procesa historial de partidas, cálculo de LP, y datos de partidas individuales.
"""

import time
from collections import defaultdict
from services.github_service import read_player_match_history, save_player_match_history
from services.cache_service import player_match_history_cache
from services.player_service import get_riot_id_for_puuid
from config.settings import SEASON_START_TIMESTAMP, QUEUE_TYPE_MAP


def get_player_match_history(puuid, riot_id=None, limit=None):
    """
    Obtiene el historial de partidas de un jugador.
    Usa caché en memoria primero, luego GitHub.
    """
    # Intentar caché primero
    cached = player_match_history_cache.get(puuid)
    if cached:
        return _apply_limit(cached, limit)
    
    # Leer de GitHub
    if not riot_id:
        riot_id = get_riot_id_for_puuid(puuid) or puuid
    
    historial = read_player_match_history(puuid)
    if not historial:
        historial = {"matches": [], "remakes": [], "last_updated": time.time()}
    
    # Guardar en caché
    player_match_history_cache.set(puuid, historial)
    
    return _apply_limit(historial, limit)


def _apply_limit(historial_data, limit):
    """Aplica límite de partidas al historial."""
    if not historial_data or limit is None or limit == -1:
        return historial_data
    
    matches = historial_data.get("matches", [])
    if len(matches) > limit:
        result = dict(historial_data)
        result["matches"] = matches[:limit]
        result["_total_matches"] = len(matches)
        return result
    
    return historial_data


def save_player_matches(puuid, historial_data, riot_id=None):
    """Guarda el historial de partidas de un jugador."""
    if not riot_id:
        riot_id = get_riot_id_for_puuid(puuid) or puuid
    
    # Actualizar caché
    player_match_history_cache.set(puuid, historial_data)
    
    # Guardar en GitHub
    return save_player_match_history(puuid, historial_data)


def calculate_lp_for_match(match, all_matches, player_lp_history):
    """
    Calcula el cambio de LP para una partida específica.
    Usa múltiples estrategias de fallback, incluyendo una especial para la última partida.
    """
    game_end_ts = match.get("game_end_timestamp", 0)
    queue_id = match.get("queue_id")
    queue_name = QUEUE_TYPE_MAP.get(queue_id)
    match_id = match.get("match_id")
    
    if not game_end_ts or not queue_name:
        return None, None, None
    
    # Estrategia 1: Usar snapshots de LP
    queue_history = player_lp_history.get(queue_name, [])
    if queue_history:
        lp_info = _calculate_from_snapshots(game_end_ts, queue_history, all_matches, queue_id, match_id)
        if lp_info:
            return lp_info
    
    # Estrategia 2: Usar partidas consecutivas
    lp_info = _calculate_from_consecutive_matches(match, all_matches, queue_id)
    if lp_info:
        return lp_info
    
    # Estrategia 3: Para la partida más reciente, usar el snapshot más reciente
    lp_info = _calculate_for_latest_match(match, all_matches, queue_id, queue_history)
    if lp_info:
        return lp_info
    
    return None, None, None


def _calculate_from_snapshots(game_end_ts, snapshots, all_matches, queue_id, match_id):
    """Calcula LP usando snapshots históricos."""
    sorted_snapshots = sorted(snapshots, key=lambda x: x["timestamp"])
    
    # Encontrar snapshots antes y después
    before = None
    after = None
    
    for snap in sorted_snapshots:
        if snap["timestamp"] < game_end_ts:
            before = snap
        elif snap["timestamp"] > game_end_ts and after is None:
            after = snap
            break
    
    if not before or not after:
        return None
    
    # Verificar si hay múltiples partidas entre snapshots
    matches_between = [
        m for m in all_matches
        if m.get("queue_id") == queue_id
        and before["timestamp"] < m.get("game_end_timestamp", 0) < after["timestamp"]
    ]
    
    elo_before = before.get("elo", 0)
    elo_after = after.get("elo", 0)
    
    if elo_before > 0 and elo_after > 0:
        # Si hay múltiples partidas, asignar todo al último
        if len(matches_between) > 1:
            last_match = max(matches_between, key=lambda x: x.get("game_end_timestamp", 0))
            if last_match.get("game_end_timestamp") == game_end_ts:
                return {
                    "lp_change": elo_after - elo_before,
                    "pre_game_elo": elo_before,
                    "post_game_elo": elo_after
                }
        else:
            return {
                "lp_change": elo_after - elo_before,
                "pre_game_elo": elo_before,
                "post_game_elo": elo_after
            }
    
    return None


def _calculate_from_consecutive_matches(match, all_matches, queue_id):
    """Calcula LP usando partidas consecutivas."""
    # Filtrar y ordenar partidas de la misma cola
    queue_matches = sorted(
        [m for m in all_matches if m.get("queue_id") == queue_id],
        key=lambda x: x.get("game_end_timestamp", 0)
    )
    
    # Encontrar índice de la partida actual
    try:
        current_idx = next(i for i, m in enumerate(queue_matches) 
                          if m.get("match_id") == match.get("match_id"))
    except StopIteration:
        return None
    
    # Buscar partida anterior con ELO post-game
    if current_idx > 0:
        prev_match = queue_matches[current_idx - 1]
        elo_before = prev_match.get("post_game_valor_clasificacion")
        
        # Buscar partida siguiente con ELO pre-game
        if current_idx < len(queue_matches) - 1:
            next_match = queue_matches[current_idx + 1]
            elo_after = next_match.get("pre_game_valor_clasificacion")
            
            if elo_before is not None and elo_after is not None:
                return {
                    "lp_change": elo_after - elo_before,
                    "pre_game_elo": elo_before,
                    "post_game_elo": elo_after
                }
    
    return None


def _calculate_for_latest_match(match, all_matches, queue_id, snapshots):
    """
    Estrategia especial para la partida más reciente.
    Usa el snapshot más reciente disponible para estimar el LP.
    """
    if not snapshots:
        return None
    
    match_id = match.get("match_id")
    game_end_ts = match.get("game_end_timestamp", 0)
    
    # Verificar si esta es la partida más reciente de la cola
    queue_matches_sorted = sorted(
        [m for m in all_matches if m.get("queue_id") == queue_id],
        key=lambda x: x.get("game_end_timestamp", 0),
        reverse=True
    )
    
    if not queue_matches_sorted or queue_matches_sorted[0].get("match_id") != match_id:
        return None  # No es la partida más reciente
    
    # Es la partida más reciente - usar el snapshot más reciente
    latest_snapshot = max(snapshots, key=lambda x: x["timestamp"])
    latest_snapshot_ts = latest_snapshot["timestamp"]
    latest_elo = latest_snapshot.get("elo", 0)
    
    # El snapshot debe ser posterior a la partida
    if latest_snapshot_ts <= game_end_ts or latest_elo <= 0:
        return None
    
    # Buscar el snapshot anterior más cercano
    previous_snapshot = None
    min_time_diff = float("inf")
    
    for snapshot in snapshots:
        if snapshot["timestamp"] < game_end_ts:
            time_diff = game_end_ts - snapshot["timestamp"]
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                previous_snapshot = snapshot
    
    if previous_snapshot:
        elo_before = previous_snapshot.get("elo", 0)
        elo_after = latest_elo
        
        if elo_before > 0:
            return {
                "lp_change": elo_after - elo_before,
                "pre_game_elo": elo_before,
                "post_game_elo": elo_after
            }
    else:
        # No hay snapshot anterior, usar estimación basada en victoria/derrota
        is_win = match.get("win", False)
        estimated_lp = 15 if is_win else -15  # Estimación estándar
        
        return {
            "lp_change": estimated_lp,
            "pre_game_elo": latest_elo - estimated_lp,
            "post_game_elo": latest_elo
        }
    
    return None


def process_matches_lp(matches, player_lp_history):
    """
    Procesa una lista de partidas calculando el LP para cada una.
    """
    # Ordenar por timestamp descendente
    sorted_matches = sorted(matches, key=lambda x: x.get("game_end_timestamp", 0), reverse=True)
    
    for match in sorted_matches:
        if match.get("lp_change_this_game") is None:
            lp_info = calculate_lp_for_match(match, sorted_matches, player_lp_history)
            if lp_info:
                match["lp_change_this_game"] = lp_info["lp_change"]
                match["pre_game_valor_clasificacion"] = lp_info["pre_game_elo"]
                match["post_game_valor_clasificacion"] = lp_info["post_game_elo"]
    
    return sorted_matches


def filter_matches_by_queue(matches, queue_id):
    """Filtra partidas por ID de cola."""
    return [m for m in matches if m.get("queue_id") == queue_id]


def filter_matches_by_champion(matches, champion_name):
    """Filtra partidas por campeón."""
    return [m for m in matches if m.get("champion_name") == champion_name]


def filter_matches_by_season(matches):
    """Filtra partidas que pertenecen a la temporada actual."""
    return [
        m for m in matches 
        if m.get("game_end_timestamp", 0) / 1000 >= SEASON_START_TIMESTAMP
    ]


def get_matches_by_queue_indexed(matches):
    """Crea un índice de partidas por cola para búsqueda rápida."""
    by_queue = defaultdict(list)
    for match in matches:
        by_queue[match.get("queue_id")].append(match)
    
    # Ordenar cada lista
    for queue_id in by_queue:
        by_queue[queue_id].sort(key=lambda x: x.get("game_end_timestamp", 0))
    
    return by_queue


def calculate_streaks(matches):
    """
    Calcula rachas de victorias y derrotas.
    Las partidas deben estar ordenadas de más reciente a más antigua.
    """
    if not matches:
        return {
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "current_win_streak": 0,
            "current_loss_streak": 0
        }
    
    max_win = 0
    max_loss = 0
    current_win = 0
    current_loss = 0
    
    # Calcular rachas máximas (de más antigua a más nueva)
    for match in reversed(matches):
        if match.get("win"):
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        
        max_win = max(max_win, current_win)
        max_loss = max(max_loss, current_loss)
    
    # Calcular racha actual (de más nueva a más antigua)
    current_streak_type = "win" if matches[0].get("win") else "loss"
    current_streak_count = 0
    
    for match in matches:
        is_win = match.get("win")
        if (is_win and current_streak_type == "win") or (not is_win and current_streak_type == "loss"):
            current_streak_count += 1
        else:
            break
    
    return {
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
        "current_win_streak": current_streak_count if current_streak_type == "win" else 0,
        "current_loss_streak": current_streak_count if current_streak_type == "loss" else 0
    }
