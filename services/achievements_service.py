"""
Servicio de logros basado en el historial de partidas.
Genera puntuaciones y desbloqueos por jugador.
"""

from collections import defaultdict

from services.player_service import get_all_accounts, get_all_puuids
from services.match_service import get_player_match_history


ACHIEVEMENTS = [
    {
        "key": "killer_instinct",
        "name": "Instinto Asesino",
        "description": "Consigue 10 o más kills en una partida.",
        "points": 15,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 10,
    },
    {
        "key": "legendary_rampage",
        "name": "Rampage Legendario",
        "description": "Consigue 15 o más kills en una partida.",
        "points": 30,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 15,
    },
    {
        "key": "assist_king",
        "name": "Rey de Asistencias",
        "description": "Consigue 20 o más asistencias en una partida.",
        "points": 18,
        "kind": "good",
        "metric": "assists",
        "op": "ge",
        "threshold": 20,
    },
    {
        "key": "no_death_hero",
        "name": "Sin Miedo a Nada",
        "description": "Gana una partida con 0 muertes.",
        "points": 25,
        "kind": "good",
        "metric": "deaths",
        "op": "eq",
        "threshold": 0,
        "extra": {"win": True},
    },
    {
        "key": "vision_lord",
        "name": "Señor de la Visión",
        "description": "Alcanza 40 o más de vision score.",
        "points": 14,
        "kind": "good",
        "metric": "vision_score",
        "op": "ge",
        "threshold": 40,
    },
    {
        "key": "damage_monster",
        "name": "Monstruo de Daño",
        "description": "Haz 35.000 o más de daño a campeones.",
        "points": 20,
        "kind": "good",
        "metric": "total_damage_dealt_to_champions",
        "op": "ge",
        "threshold": 35000,
    },
    {
        "key": "farmer_pro",
        "name": "Farmeador Pro",
        "description": "Mantén 8 o más CS/min durante la partida.",
        "points": 16,
        "kind": "good",
        "metric": "cs_per_min",
        "op": "ge",
        "threshold": 8.0,
    },
    {
        "key": "objective_hunter",
        "name": "Cazador de Objetivos",
        "description": "Consigue 5 o más objetivos (torres + dragones + barones).",
        "points": 17,
        "kind": "good",
        "metric": "objectives_total",
        "op": "ge",
        "threshold": 5,
    },
    {
        "key": "feed_alarm",
        "name": "Alarma de Feed",
        "description": "Muere 10 o más veces en una partida.",
        "points": -12,
        "kind": "bad",
        "metric": "deaths",
        "op": "ge",
        "threshold": 10,
    },
    {
        "key": "dark_game",
        "name": "Partida Oscura",
        "description": "Termina con 5 o menos de vision score en partidas largas.",
        "points": -9,
        "kind": "bad",
        "metric": "vision_score",
        "op": "le",
        "threshold": 5,
        "extra": {"min_duration": 1200},
    },
    {
        "key": "afk_farm",
        "name": "Farm Fantasma",
        "description": "Baja de 4 CS/min en partidas largas.",
        "points": -8,
        "kind": "bad",
        "metric": "cs_per_min",
        "op": "lt",
        "threshold": 4.0,
        "extra": {"min_duration": 1200},
    },
    {
        "key": "invisible_impact",
        "name": "Impacto Invisible",
        "description": "K+A <= 2 y 6 o más muertes.",
        "points": -10,
        "kind": "bad",
        "metric": "low_impact_flag",
        "op": "eq",
        "threshold": 1,
    },
]


def _metric_value(match, metric):
    kills = match.get("kills", 0) or 0
    deaths = match.get("deaths", 0) or 0
    assists = match.get("assists", 0) or 0
    duration = match.get("game_duration", 0) or 0

    if metric == "kills":
        return kills
    if metric == "deaths":
        return deaths
    if metric == "assists":
        return assists
    if metric == "vision_score":
        return match.get("vision_score", 0) or 0
    if metric == "total_damage_dealt_to_champions":
        return match.get("total_damage_dealt_to_champions", 0) or 0
    if metric == "cs_per_min":
        cs_total = (match.get("total_minions_killed", 0) or 0) + (match.get("neutral_minions_killed", 0) or 0)
        return (cs_total / max(1, duration / 60)) if duration > 0 else 0
    if metric == "objectives_total":
        return (match.get("turret_kills", 0) or 0) + (match.get("dragon_kills", 0) or 0) + (match.get("baron_kills", 0) or 0)
    if metric == "low_impact_flag":
        return 1 if (kills + assists <= 2 and deaths >= 6) else 0
    return 0


def _compare(value, op, threshold):
    if op == "ge":
        return value >= threshold
    if op == "gt":
        return value > threshold
    if op == "le":
        return value <= threshold
    if op == "lt":
        return value < threshold
    if op == "eq":
        return value == threshold
    return False


def _achievement_hit(match, definition):
    extra = definition.get("extra", {})

    if "win" in extra and bool(match.get("win")) != bool(extra["win"]):
        return False, 0

    min_duration = extra.get("min_duration")
    if min_duration and (match.get("game_duration", 0) or 0) < min_duration:
        return False, 0

    value = _metric_value(match, definition["metric"])
    hit = _compare(value, definition["op"], definition["threshold"])
    return hit, value


def _empty_player_row(riot_id, player_name, puuid):
    return {
        "riot_id": riot_id,
        "player_name": player_name,
        "puuid": puuid,
        "total_points": 0,
        "positive_points": 0,
        "negative_points": 0,
        "hits_total": 0,
        "unique_achievements": 0,
        "total_matches": 0,
        "achievement_stats": {},
    }


def calculate_global_achievements():
    """
    Calcula logros de todos los jugadores usando match_history.
    """
    accounts = get_all_accounts()
    puuids = get_all_puuids()

    players = []

    for riot_id, display_name in accounts:
        puuid = puuids.get(riot_id)
        if not puuid:
            continue

        history = get_player_match_history(puuid, limit=-1)
        matches = history.get("matches", []) or []
        player_row = _empty_player_row(riot_id, display_name, puuid)
        player_row["total_matches"] = len(matches)

        by_key = defaultdict(lambda: {
            "key": "",
            "name": "",
            "description": "",
            "points": 0,
            "kind": "good",
            "count": 0,
            "best_value": 0,
            "last_match_id": "",
            "last_champion": "",
        })

        for match in matches:
            for definition in ACHIEVEMENTS:
                hit, value = _achievement_hit(match, definition)
                if not hit:
                    continue

                stat = by_key[definition["key"]]
                stat["key"] = definition["key"]
                stat["name"] = definition["name"]
                stat["description"] = definition["description"]
                stat["points"] = definition["points"]
                stat["kind"] = definition["kind"]
                stat["count"] += 1
                stat["best_value"] = max(stat["best_value"], value or 0)
                stat["last_match_id"] = match.get("match_id", "")
                stat["last_champion"] = match.get("champion_name", "")

                player_row["hits_total"] += 1
                player_row["total_points"] += definition["points"]
                if definition["points"] >= 0:
                    player_row["positive_points"] += definition["points"]
                else:
                    player_row["negative_points"] += definition["points"]

        achievement_stats = sorted(
            by_key.values(),
            key=lambda x: (x["count"] * abs(x["points"]), x["points"], x["count"]),
            reverse=True
        )
        player_row["achievement_stats"] = achievement_stats
        player_row["unique_achievements"] = len(achievement_stats)

        players.append(player_row)

    players.sort(
        key=lambda p: (p["total_points"], p["positive_points"], p["unique_achievements"], p["hits_total"]),
        reverse=True
    )

    total_unlocked = sum(p["hits_total"] for p in players)
    global_stats = {
        "players_count": len(players),
        "total_unlocked": total_unlocked,
        "max_possible_unique": len(ACHIEVEMENTS),
    }

    return {
        "players": players,
        "achievements_catalog": ACHIEVEMENTS,
        "global_stats": global_stats,
    }
