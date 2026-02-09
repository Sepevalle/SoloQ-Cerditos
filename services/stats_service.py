"""
Servicio de estadísticas y récords.
Calcula estadísticas globales y personales, récords de partidas.
"""

import time
from collections import Counter, defaultdict

from config.constants import (
    RECORD_DISPLAY_NAMES, 
    RECORD_ICONS, 
    RECORDS_NA_IF_ZERO,
    PERSONAL_RECORD_KEYS
)
from services.match_service import calculate_streaks, filter_matches_by_season
from services.cache_service import personal_records_cache, global_stats_cache
from utils.helpers import calcular_valor_clasificacion


def _default_record():
    """Plantilla de récord por defecto."""
    return {
        "value": 0,
        "player": "N/A",
        "riot_id": "N/A",
        "match_id": "N/A",
        "kda": 0,
        "game_date": 0,
        "game_duration": 0,
        "champion_name": "N/A",
        "champion_id": "N/A",
        "kills": 0,
        "deaths": 0,
        "assists": 0,
        "achieved_timestamp": 0,
        "is_tied_record": False
    }


def _create_record_from_match(match, value, record_type):
    """Crea un diccionario de récord desde una partida."""
    # Determinar valor final (N/A si es 0 en ciertos récords)
    final_value = value
    if record_type in RECORDS_NA_IF_ZERO and value == 0:
        final_value = None
    
    return {
        "value": final_value,
        "player": match.get("jugador_nombre", "N/A"),
        "riot_id": match.get("riot_id", "N/A"),
        "match_id": match.get("match_id", "N/A"),
        "kda": match.get("kda", 0),
        "game_date": match.get("game_end_timestamp", 0),
        "game_duration": int(match.get("game_duration", 0)),
        "champion_name": match.get("champion_name", "N/A"),
        "champion_id": match.get("championId", "N/A"),
        "kills": match.get("kills", 0),
        "deaths": match.get("deaths", 0),
        "assists": match.get("assists", 0),
        "achieved_timestamp": match.get("game_end_timestamp", 0),
        "record_type": record_type,
        "record_display_name": RECORD_DISPLAY_NAMES.get(record_type, record_type),
        "icon": RECORD_ICONS.get(record_type, "fas fa-trophy")
    }


def _update_record(current, new_value, match, record_type):
    """Actualiza un récord si el nuevo valor es mejor."""
    new_record = _create_record_from_match(match, new_value, record_type)
    
    current_value = current.get("value") if current.get("value") is not None else -1
    new_value_cmp = new_value if new_value is not None else -1
    
    # Actualizar si:
    # 1. Nuevo valor es mayor
    # 2. Valores iguales pero timestamp más antiguo (desempate)
    # 3. Récord actual es default y nuevo es válido
    is_default = (current.get("value") == 0 and 
                  current.get("player") == "N/A" and 
                  current.get("achieved_timestamp") == 0)
    
    if (new_value_cmp > current_value or 
        (new_value_cmp == current_value and 
         new_record["achieved_timestamp"] < current.get("achieved_timestamp", float('inf'))) or
        (is_default and new_value_cmp >= 0)):
        return new_record
    
    return current


def calculate_personal_records(puuid, matches, player_name, riot_id, champion_filter=None, queue_filter=None):
    """
    Calcula los récords personales de un jugador.
    
    Args:
        puuid: ID único del jugador
        matches: Lista de partidas del jugador
        player_name: Nombre del jugador
        riot_id: Riot ID del jugador
        champion_filter: Filtro opcional por nombre de campeón
        queue_filter: Filtro opcional por ID de cola (420 para SoloQ, 440 para Flex)
    """
    # Crear cache key que incluya ambos filtros
    cache_key = f"{puuid}_{champion_filter or 'all'}_{queue_filter or 'all'}"
    
    # Verificar caché
    cached = personal_records_cache.get(cache_key)
    if cached:
        return cached
    
    # Aplicar filtros
    filtered_matches = matches
    
    # Filtrar por campeón si es necesario
    if champion_filter:
        filtered_matches = [m for m in filtered_matches if m.get("champion_name") == champion_filter]
    
    # Filtrar por cola si es necesario
    if queue_filter:
        try:
            queue_id = int(queue_filter)
            filtered_matches = [m for m in filtered_matches if m.get("queue_id") == queue_id]
            print(f"[calculate_personal_records] Filtrando por cola {queue_id}: {len(filtered_matches)} partidas")
        except (ValueError, TypeError) as e:
            print(f"[calculate_personal_records] Error convirtiendo queue_filter '{queue_filter}': {e}")
    
    if not filtered_matches:
        print(f"[calculate_personal_records] No hay partidas después de aplicar filtros")
        return {key: _default_record() for key in PERSONAL_RECORD_KEYS}
    
    # Inicializar récords
    records = {key: _default_record() for key in PERSONAL_RECORD_KEYS}
    
    # Añadir metadata a las partidas
    for match in filtered_matches:
        match["jugador_nombre"] = player_name
        match["riot_id"] = riot_id
    
    # Calcular rachas
    streaks = calculate_streaks(filtered_matches)
    if streaks["max_win_streak"] > 0:
        # Encontrar última partida de la racha
        win_streak_end = None
        current = 0
        for match in filtered_matches:
            if match.get("win"):
                current += 1
                if current == streaks["max_win_streak"]:
                    win_streak_end = match
                    break
            else:
                current = 0
        
        if win_streak_end:
            records["longest_win_streak"] = _update_record(
                records["longest_win_streak"], 
                streaks["max_win_streak"], 
                win_streak_end, 
                "longest_win_streak"
            )
    
    if streaks["max_loss_streak"] > 0:
        loss_streak_end = None
        current = 0
        for match in filtered_matches:
            if not match.get("win"):
                current += 1
                if current == streaks["max_loss_streak"]:
                    loss_streak_end = match
                    break
            else:
                current = 0
        
        if loss_streak_end:
            records["longest_loss_streak"] = _update_record(
                records["longest_loss_streak"],
                streaks["max_loss_streak"],
                loss_streak_end,
                "longest_loss_streak"
            )
    
    # Calcular récords individuales
    for match in filtered_matches:
        # Campeón
        champion_id = match.get("championId")
        champion_name = match.get("champion_name", "Desconocido")
        
        # CS total
        total_cs = match.get("total_minions_killed", 0) + match.get("neutral_minions_killed", 0)
        
        # KDA
        kda = (match.get("kills", 0) + match.get("assists", 0)) / max(1, match.get("deaths", 0))
        
        # Kill participation
        kp = match.get("kill_participation", 0)
        
        # Actualizar récords
        records["longest_game"] = _update_record(
            records["longest_game"], 
            match.get("game_duration", 0), 
            match, 
            "longest_game"
        )
        records["most_kills"] = _update_record(
            records["most_kills"], 
            match.get("kills", 0), 
            match, 
            "most_kills"
        )
        records["most_deaths"] = _update_record(
            records["most_deaths"], 
            match.get("deaths", 0), 
            match, 
            "most_deaths"
        )
        records["most_assists"] = _update_record(
            records["most_assists"], 
            match.get("assists", 0), 
            match, 
            "most_assists"
        )
        records["highest_kda"] = _update_record(
            records["highest_kda"], 
            kda, 
            match, 
            "highest_kda"
        )
        records["most_cs"] = _update_record(
            records["most_cs"], 
            total_cs, 
            match, 
            "most_cs"
        )
        records["most_damage_dealt"] = _update_record(
            records["most_damage_dealt"], 
            match.get("total_damage_dealt_to_champions", 0), 
            match, 
            "most_damage_dealt"
        )
        records["most_gold_earned"] = _update_record(
            records["most_gold_earned"], 
            match.get("gold_earned", 0), 
            match, 
            "most_gold_earned"
        )
        records["most_vision_score"] = _update_record(
            records["most_vision_score"], 
            match.get("vision_score", 0), 
            match, 
            "most_vision_score"
        )
        records["largest_killing_spree"] = _update_record(
            records["largest_killing_spree"], 
            match.get("largest_killing_spree", 0), 
            match, 
            "largest_killing_spree"
        )
        records["largest_multikill"] = _update_record(
            records["largest_multikill"], 
            match.get("largestMultiKill", 0), 
            match, 
            "largest_multikill"
        )
        records["most_time_spent_dead"] = _update_record(
            records["most_time_spent_dead"], 
            match.get("total_time_spent_dead", 0), 
            match, 
            "most_time_spent_dead"
        )
        records["most_wards_placed"] = _update_record(
            records["most_wards_placed"], 
            match.get("wards_placed", 0), 
            match, 
            "most_wards_placed"
        )
        records["most_wards_killed"] = _update_record(
            records["most_wards_killed"], 
            match.get("wards_killed", 0), 
            match, 
            "most_wards_killed"
        )
        records["most_turret_kills"] = _update_record(
            records["most_turret_kills"], 
            match.get("turret_kills", 0), 
            match, 
            "most_turret_kills"
        )
        records["most_inhibitor_kills"] = _update_record(
            records["most_inhibitor_kills"], 
            match.get("inhibitor_kills", 0), 
            match, 
            "most_inhibitor_kills"
        )
        records["most_baron_kills"] = _update_record(
            records["most_baron_kills"], 
            match.get("baron_kills", 0), 
            match, 
            "most_baron_kills"
        )
        records["most_dragon_kills"] = _update_record(
            records["most_dragon_kills"], 
            match.get("dragon_kills", 0), 
            match, 
            "most_dragon_kills"
        )
        records["most_damage_taken"] = _update_record(
            records["most_damage_taken"], 
            match.get("total_damage_taken", 0), 
            match, 
            "most_damage_taken"
        )
        records["most_total_heal"] = _update_record(
            records["most_total_heal"], 
            match.get("total_heal", 0), 
            match, 
            "most_total_heal"
        )
        records["most_damage_shielded_on_teammates"] = _update_record(
            records["most_damage_shielded_on_teammates"], 
            match.get("total_damage_shielded_on_teammates", 0), 
            match, 
            "most_damage_shielded_on_teammates"
        )
        records["most_time_ccing_others"] = _update_record(
            records["most_time_ccing_others"], 
            match.get("time_ccing_others", 0), 
            match, 
            "most_time_ccing_others"
        )
        records["most_objectives_stolen"] = _update_record(
            records["most_objectives_stolen"], 
            match.get("objectives_stolen", 0), 
            match, 
            "most_objectives_stolen"
        )
        records["highest_kill_participation"] = _update_record(
            records["highest_kill_participation"], 
            kp, 
            match, 
            "highest_kill_participation"
        )
        records["most_double_kills"] = _update_record(
            records["most_double_kills"], 
            match.get("doubleKills", 0), 
            match, 
            "most_double_kills"
        )
        records["most_triple_kills"] = _update_record(
            records["most_triple_kills"], 
            match.get("tripleKills", 0), 
            match, 
            "most_triple_kills"
        )
        records["most_quadra_kills"] = _update_record(
            records["most_quadra_kills"], 
            match.get("quadraKills", 0), 
            match, 
            "most_quadra_kills"
        )
        records["most_penta_kills"] = _update_record(
            records["most_penta_kills"], 
            match.get("pentaKills", 0), 
            match, 
            "most_penta_kills"
        )
    
    # Guardar en caché
    personal_records_cache.set(cache_key, records)
    
    return records


def calculate_global_stats(all_matches, queue_id_filter=None, champion_filter=None):
    """
    Calcula estadísticas globales para un conjunto de partidas.
    """
    # Filtrar partidas
    filtered = all_matches
    if queue_id_filter is not None:
        if isinstance(queue_id_filter, list):
            filtered = [m for m in filtered if m.get("queue_id") in queue_id_filter]
        else:
            filtered = [m for m in filtered if m.get("queue_id") == queue_id_filter]
    
    if champion_filter:
        filtered = [m for m in filtered if m.get("champion_name") == champion_filter]
    
    if not filtered:
        return {
            "overall_win_rate": 0,
            "total_games": 0,
            "most_played_champions": [],
            "global_records": {key: _default_record() for key in PERSONAL_RECORD_KEYS}
        }
    
    # Calcular win rate
    wins = sum(1 for m in filtered if m.get("win"))
    total = len(filtered)
    win_rate = (wins / total * 100) if total > 0 else 0
    
    # Campeones más jugados
    champion_counts = Counter(m.get("champion_name") for m in filtered if m.get("champion_name"))
    most_played = champion_counts.most_common(5)
    
    # Calcular récords globales
    global_records = {key: _default_record() for key in PERSONAL_RECORD_KEYS}
    
    # Agrupar por jugador para rachas
    matches_by_player = defaultdict(list)
    for match in filtered:
        matches_by_player[match.get("puuid")].append(match)
    
    # Calcular rachas por jugador
    for puuid, player_matches in matches_by_player.items():
        streaks = calculate_streaks(player_matches)
        
        if streaks["max_win_streak"] > global_records["longest_win_streak"]["value"]:
            # Encontrar partida final de la racha
            current = 0
            end_match = None
            for m in player_matches:
                if m.get("win"):
                    current += 1
                    if current == streaks["max_win_streak"]:
                        end_match = m
                        break
                else:
                    current = 0
            
            if end_match:
                global_records["longest_win_streak"] = _create_record_from_match(
                    end_match, streaks["max_win_streak"], "longest_win_streak"
                )
        
        if streaks["max_loss_streak"] > global_records["longest_loss_streak"]["value"]:
            current = 0
            end_match = None
            for m in player_matches:
                if not m.get("win"):
                    current += 1
                    if current == streaks["max_loss_streak"]:
                        end_match = m
                        break
                else:
                    current = 0
            
            if end_match:
                global_records["longest_loss_streak"] = _create_record_from_match(
                    end_match, streaks["max_loss_streak"], "longest_loss_streak"
                )
    
    # Calcular récords individuales
    for match in filtered:
        total_cs = match.get("total_minions_killed", 0) + match.get("neutral_minions_killed", 0)
        kda = (match.get("kills", 0) + match.get("assists", 0)) / max(1, match.get("deaths", 0))
        kp = match.get("kill_participation", 0)
        
        records_to_check = {
            "longest_game": match.get("game_duration", 0),
            "most_kills": match.get("kills", 0),
            "most_deaths": match.get("deaths", 0),
            "most_assists": match.get("assists", 0),
            "highest_kda": kda,
            "most_cs": total_cs,
            "most_damage_dealt": match.get("total_damage_dealt_to_champions", 0),
            "most_gold_earned": match.get("gold_earned", 0),
            "most_vision_score": match.get("vision_score", 0),
            "largest_killing_spree": match.get("largest_killing_spree", 0),
            "largest_multikill": match.get("largestMultiKill", 0),
            "most_time_spent_dead": match.get("total_time_spent_dead", 0),
            "most_wards_placed": match.get("wards_placed", 0),
            "most_wards_killed": match.get("wards_killed", 0),
            "most_turret_kills": match.get("turret_kills", 0),
            "most_inhibitor_kills": match.get("inhibitor_kills", 0),
            "most_baron_kills": match.get("baron_kills", 0),
            "most_dragon_kills": match.get("dragon_kills", 0),
            "most_damage_taken": match.get("total_damage_taken", 0),
            "most_total_heal": match.get("total_heal", 0),
            "most_damage_shielded_on_teammates": match.get("total_damage_shielded_on_teammates", 0),
            "most_time_ccing_others": match.get("time_ccing_others", 0),
            "most_objectives_stolen": match.get("objectives_stolen", 0),
            "highest_kill_participation": kp,
            "most_double_kills": match.get("doubleKills", 0),
            "most_triple_kills": match.get("tripleKills", 0),
            "most_quadra_kills": match.get("quadraKills", 0),
            "most_penta_kills": match.get("pentaKills", 0),
        }
        
        for record_key, value in records_to_check.items():
            global_records[record_key] = _update_record(
                global_records[record_key], value, match, record_key
            )
    
    return {
        "overall_win_rate": win_rate,
        "total_games": total,
        "most_played_champions": most_played,
        "global_records": global_records
    }


def get_top_champions_for_player(matches, limit=3):
    """Obtiene los campeones más jugados por un jugador con estadísticas."""
    if not matches:
        return []
    
    # Contar partidas por campeón
    champion_matches = defaultdict(list)
    for match in matches:
        champ = match.get("champion_name")
        if champ and champ != "Desconocido":
            champion_matches[champ].append(match)
    
    if not champion_matches:
        return []
    
    # Calcular estadísticas por campeón
    champion_stats = []
    for champ, champ_matches in champion_matches.items():
        total = len(champ_matches)
        wins = sum(1 for m in champ_matches if m.get("win"))
        win_rate = (wins / total * 100) if total > 0 else 0
        
        total_kills = sum(m.get("kills", 0) for m in champ_matches)
        total_deaths = sum(m.get("deaths", 0) for m in champ_matches)
        total_assists = sum(m.get("assists", 0) for m in champ_matches)
        
        kda = (total_kills + total_assists) / max(1, total_deaths)
        
        # Encontrar mejor KDA
        best_kda_match = None
        best_kda = 0
        for m in champ_matches:
            m_kda = (m.get("kills", 0) + m.get("assists", 0)) / max(1, m.get("deaths", 0))
            if m_kda > best_kda:
                best_kda = m_kda
                best_kda_match = m
        
        champion_stats.append({
            "champion_name": champ,
            "games_played": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": win_rate,
            "kda": kda,
            "kills": total_kills,
            "deaths": total_deaths,
            "assists": total_assists,
            "avg_kills": total_kills / total,
            "avg_deaths": total_deaths / total,
            "avg_assists": total_assists / total,
            "best_kda_match": {
                "kda": best_kda,
                "kills": best_kda_match.get("kills", 0) if best_kda_match else 0,
                "deaths": best_kda_match.get("deaths", 0) if best_kda_match else 0,
                "assists": best_kda_match.get("assists", 0) if best_kda_match else 0,
                "timestamp": best_kda_match.get("game_end_timestamp", 0) if best_kda_match else 0
            } if best_kda_match else None
        })
    
    # Ordenar por partidas jugadas y limitar
    champion_stats.sort(key=lambda x: x["games_played"], reverse=True)
    return champion_stats[:limit]


def extract_global_records(all_matches):
    """
    Extrae récords globales de una lista de partidas.
    
    Args:
        all_matches: Lista de tuplas (player_name, match)
    
    Returns:
        dict: Diccionario de récords globales
    """
    if not all_matches:
        return {key: _default_record() for key in PERSONAL_RECORD_KEYS}
    
    # Inicializar récords
    records = {key: _default_record() for key in PERSONAL_RECORD_KEYS}
    
    # Agrupar por jugador para rachas
    matches_by_player = defaultdict(list)
    for player_name, match in all_matches:
        # Fix: Handle nested tuple case from caller
        # Sometimes match can be a tuple if caller passes [(p, m) for m in (p, m) tuples]
        actual_match = match
        if isinstance(match, tuple):
            # If match is a tuple, it could be (player_name, match_dict) or deeper nesting
            actual_match = match[1] if len(match) > 1 else match[0]
        
        # Ensure we have a dictionary, not a tuple
        if isinstance(actual_match, tuple):
            actual_match = actual_match[1] if len(actual_match) > 1 else actual_match[0]
        
        # Skip if we couldn't extract a valid match dict
        if not isinstance(actual_match, dict):
            continue
            
        # Create a copy to avoid modifying original data
        match_copy = dict(actual_match)
        match_copy["jugador_nombre"] = player_name
        matches_by_player[match_copy.get("puuid")].append(match_copy)


    
    # Calcular rachas por jugador
    for puuid, player_matches in matches_by_player.items():
        streaks = calculate_streaks(player_matches)
        
        if streaks["max_win_streak"] > records["longest_win_streak"]["value"]:
            current = 0
            end_match = None
            for m in player_matches:
                if m.get("win"):
                    current += 1
                    if current == streaks["max_win_streak"]:
                        end_match = m
                        break
                else:
                    current = 0
            
            if end_match:
                records["longest_win_streak"] = _create_record_from_match(
                    end_match, streaks["max_win_streak"], "longest_win_streak"
                )
        
        if streaks["max_loss_streak"] > records["longest_loss_streak"]["value"]:
            current = 0
            end_match = None
            for m in player_matches:
                if not m.get("win"):
                    current += 1
                    if current == streaks["max_loss_streak"]:
                        end_match = m
                        break
                else:
                    current = 0
            
            if end_match:
                records["longest_loss_streak"] = _create_record_from_match(
                    end_match, streaks["max_loss_streak"], "longest_loss_streak"
                )
    
    # Calcular récords individuales
    for player_name, match in all_matches:
        # Fix: Handle nested tuple case from caller
        actual_match = match
        if isinstance(match, tuple):
            actual_match = match[1] if len(match) > 1 else match[0]
        
        # Ensure we have a dictionary
        if isinstance(actual_match, tuple):
            actual_match = actual_match[1] if len(actual_match) > 1 else actual_match[0]
        
        if not isinstance(actual_match, dict):
            continue
        
        # Use the match copy from earlier if available, otherwise create new copy
        total_cs = actual_match.get("total_minions_killed", 0) + actual_match.get("neutral_minions_killed", 0)
        kda = (actual_match.get("kills", 0) + actual_match.get("assists", 0)) / max(1, actual_match.get("deaths", 0))
        kp = actual_match.get("kill_participation", 0)


        
        records_to_check = {
            "longest_game": actual_match.get("game_duration", 0),
            "most_kills": actual_match.get("kills", 0),
            "most_deaths": actual_match.get("deaths", 0),
            "most_assists": actual_match.get("assists", 0),
            "highest_kda": kda,
            "most_cs": total_cs,
            "most_damage_dealt": actual_match.get("total_damage_dealt_to_champions", 0),
            "most_gold_earned": actual_match.get("gold_earned", 0),
            "most_vision_score": actual_match.get("vision_score", 0),
            "largest_killing_spree": actual_match.get("largest_killing_spree", 0),
            "largest_multikill": actual_match.get("largestMultiKill", 0),
            "most_time_spent_dead": actual_match.get("total_time_spent_dead", 0),
            "most_wards_placed": actual_match.get("wards_placed", 0),
            "most_wards_killed": actual_match.get("wards_killed", 0),
            "most_turret_kills": actual_match.get("turret_kills", 0),
            "most_inhibitor_kills": actual_match.get("inhibitor_kills", 0),
            "most_baron_kills": actual_match.get("baron_kills", 0),
            "most_dragon_kills": actual_match.get("dragon_kills", 0),
            "most_damage_taken": actual_match.get("total_damage_taken", 0),
            "most_total_heal": actual_match.get("total_heal", 0),
            "most_damage_shielded_on_teammates": actual_match.get("total_damage_shielded_on_teammates", 0),
            "most_time_ccing_others": actual_match.get("time_ccing_others", 0),
            "most_objectives_stolen": actual_match.get("objectives_stolen", 0),
            "highest_kill_participation": kp,
            "most_double_kills": actual_match.get("doubleKills", 0),
            "most_triple_kills": actual_match.get("tripleKills", 0),
            "most_quadra_kills": actual_match.get("quadraKills", 0),
            "most_penta_kills": actual_match.get("pentaKills", 0),
        }
        
        # Create a copy with player_name for record creation
        match_for_record = dict(actual_match)
        match_for_record["jugador_nombre"] = player_name
        
        for record_key, value in records_to_check.items():
            records[record_key] = _update_record(
                records[record_key], value, match_for_record, record_key
            )

    
    return records


def start_stats_calculator():
    """
    Función de inicio para el servicio de cálculo de estadísticas.
    Mantiene el caché de estadísticas actualizado periódicamente.
    """
    print("[stats_service] Servicio de cálculo de estadísticas iniciado")
    
    while True:
        try:
            # Verificar si el caché necesita actualización
            if global_stats_cache.is_stale():
                print("[stats_service] Actualizando estadísticas globales...")
                # La actualización real se hace en data_updater
                # Este servicio solo mantiene el estado del caché
            
            time.sleep(60)  # Verificar cada minuto
            
        except Exception as e:
            print(f"[stats_service] Error: {e}")
            time.sleep(60)
