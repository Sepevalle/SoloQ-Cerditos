#!/usr/bin/env python3
"""
Script para generar team_matches.json con las partidas donde participaron todos los miembros del equipo.
Minimiza lecturas de archivos al precomputar las estadísticas del equipo.
"""

import json
import os
import sys
from collections import defaultdict

# Añadir el directorio raíz al path para importar módulos
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.github_service import write_file_to_github
from services.team_service import get_team_config
from services.match_service import get_player_match_history


def generate_team_matches_json():
    """Genera team_matches.json con todas las partidas del equipo."""
    print("Generando team_matches.json...")

    # Obtener configuración del equipo
    config = get_team_config()
    players = config.get("players", [])

    if len(players) < 5:
        print(f"Error: El equipo debe tener al menos 5 jugadores. Encontrados: {len(players)}")
        return False

    # Verificar que al menos 5 tienen PUUID
    complete_roster = [p for p in players if p.get("puuid")]
    if len(complete_roster) < 5:
        print("Error: Se necesitan al menos 5 jugadores con PUUID asignado.")
        return False

    print(f"Equipo: {config.get('name', 'Equipo Principal')}")
    print(f"Jugadores en plantilla: {len(complete_roster)}")

    # Obtener historiales de cada jugador
    matches_by_id = defaultdict(dict)
    candidate_matches = {}

    for player in complete_roster:
        puuid = player["puuid"]
        riot_id = player.get("riot_id")
        print(f"Procesando {riot_id} ({puuid[:16]}...)")

        historial = get_player_match_history(puuid, riot_id=riot_id, limit=-1)
        matches = historial.get("matches", []) if historial else []

        for match in matches:
            match_id = match.get("match_id")
            if match_id:
                matches_by_id[match_id][puuid] = match
                candidate_matches.setdefault(match_id, match)

    # Encontrar partidas del equipo
    team_puuids = {p["puuid"] for p in complete_roster}
    team_matches = []

    print(f"Analizando {len(candidate_matches)} partidas candidatas...")

    for match_id, representative_match in candidate_matches.items():
        participants = representative_match.get("all_participants") or []
        by_team = defaultdict(list)
        for p in participants:
            puuid = p.get("puuid")
            if puuid and puuid in team_puuids:
                by_team[p.get("team_id")].append(puuid)

        has_team = any(len(members) >= 5 for members in by_team.values())
        if not has_team and len(matches_by_id.get(match_id, {})) < 5:
            continue

        team_match = build_team_match_data(
            match_id,
            representative_match,
            matches_by_id.get(match_id, {}),
            complete_roster,
        )

        if team_match:
            team_matches.append(team_match)

    # Ordenar por timestamp descendente
    team_matches.sort(key=lambda m: m.get("game_end_timestamp", 0), reverse=True)

    # Guardar el JSON
    output_data = {
        "team_name": config.get("name", "Equipo Principal"),
        "players": complete_roster,
        "total_team_matches": len(team_matches),
        "last_updated": config.get("last_updated", 0),
        "team_matches": team_matches
    }

    output_path = "team_matches.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if write_file_to_github(output_path, output_data, message="Actualizar team_matches.json desde script"):
        print("✓ team_matches.json guardado en GitHub")
    else:
        print("⚠️ No se pudo guardar team_matches.json en GitHub")

    print(f"✓ Generado {output_path} con {len(team_matches)} partidas del equipo")
    return True


def build_team_match_data(match_id, representative, player_matches, roster):
    """Construye los datos de una partida del equipo."""
    participants = representative.get("all_participants") or []
    for m in player_matches.values():
        participants.extend(m.get("all_participants") or [])

    participant_by_puuid = {p.get("puuid"): p for p in participants if p.get("puuid")}
    roster_by_puuid = {p["puuid"]: p for p in roster}
    team_puuids = set(roster_by_puuid.keys())

    by_team_id = defaultdict(list)
    for puuid in team_puuids:
        if puuid in participant_by_puuid:
            p_data = participant_by_puuid[puuid]
            t_id = p_data.get("team_id")
            by_team_id[t_id].append((roster_by_puuid[puuid], p_data))

    selected_team_id = None
    active_roster_pairs = []
    for t_id, pairs in by_team_id.items():
        if len(pairs) >= 5:
            selected_team_id = t_id
            active_roster_pairs = pairs
            break

    if not active_roster_pairs and len(player_matches) >= 5:
        wins_groups = defaultdict(list)
        for puuid, p_match in player_matches.items():
            if puuid in roster_by_puuid:
                wins_groups[bool(p_match.get("win"))].append((roster_by_puuid[puuid], p_match))
        for win_val, pairs in wins_groups.items():
            if len(pairs) >= 5:
                active_roster_pairs = pairs
                break

    if len(active_roster_pairs) < 5:
        return None

    active_roster_pairs = active_roster_pairs[:5]

    team_participants = []
    for roster_player, p_or_m in active_roster_pairs:
        puuid = roster_player["puuid"]
        p = participant_by_puuid.get(puuid) or {}
        p_match = player_matches.get(puuid) or {}
        team_participants.append({
            "puuid": puuid,
            "riot_id": roster_player.get("riot_id", ""),
            "champion_name": p_match.get("champion_name") or p.get("champion_name", ""),
            "champion_id": p_match.get("champion_id") or p.get("champion_id", 0),
            "kills": p_match.get("kills", p.get("kills", 0)),
            "deaths": p_match.get("deaths", p.get("deaths", 0)),
            "assists": p_match.get("assists", p.get("assists", 0)),
            "win": p_match.get("win", p.get("win", False)),
            "lp_change": p_match.get("lp_change_this_game", p_match.get("lp_change", 0)),
        })

    # Calcular estadísticas del equipo
    total_kills = sum(p["kills"] for p in team_participants)
    total_deaths = sum(p["deaths"] for p in team_participants)
    total_assists = sum(p["assists"] for p in team_participants)
    team_win = all(p["win"] for p in team_participants)
    total_lp_change = sum(p["lp_change"] for p in team_participants)

    return {
        "match_id": match_id,
        "game_end_timestamp": representative.get("game_end_timestamp", 0),
        "queue_id": representative.get("queue_id", 0),
        "game_duration": representative.get("game_duration", 0),
        "team_id": selected_team_id,
        "win": team_win,
        "lp_change": total_lp_change,
        "team_kills": total_kills,
        "team_deaths": total_deaths,
        "team_assists": total_assists,
        "team_kda": (total_kills + total_assists) / max(total_deaths, 1),
        "participants": team_participants,
        # Estadísticas adicionales del partido completo
        "dragon_kills": representative.get("dragon_kills", 0),
        "baron_kills": representative.get("baron_kills", 0),
        "tower_kills": representative.get("tower_kills", 0),
        "inhibitor_kills": representative.get("inhibitor_kills", 0),
    }


if __name__ == "__main__":
    success = generate_team_matches_json()
    sys.exit(0 if success else 1)
