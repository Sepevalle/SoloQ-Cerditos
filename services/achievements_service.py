"""
Achievement service based on stored match history.
Adds points, secret achievements, and player levels.
"""

from collections import defaultdict

from services.player_service import get_all_accounts, get_all_puuids
from services.match_service import get_player_match_history


ACHIEVEMENTS = [
    {
        "key": "killer_instinct",
        "name": "Instinto Asesino",
        "description": "Consigue 10 o mas kills en una partida.",
        "points": 15,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 10,
    },
    {
        "key": "legendary_rampage",
        "name": "Rampage Legendario",
        "description": "Consigue 15 o mas kills en una partida.",
        "points": 30,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 15,
    },
    {
        "key": "assist_king",
        "name": "Rey de Asistencias",
        "description": "Consigue 20 o mas asistencias en una partida.",
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
        "name": "Senor de la Vision",
        "description": "Alcanza 40 o mas de vision score.",
        "points": 14,
        "kind": "good",
        "metric": "vision_score",
        "op": "ge",
        "threshold": 40,
    },
    {
        "key": "damage_monster",
        "name": "Monstruo de Dano",
        "description": "Haz 35000 o mas de dano a campeones.",
        "points": 20,
        "kind": "good",
        "metric": "total_damage_dealt_to_champions",
        "op": "ge",
        "threshold": 35000,
    },
    {
        "key": "farmer_pro",
        "name": "Farmeador Pro",
        "description": "Manten 8 o mas CS/min durante la partida.",
        "points": 16,
        "kind": "good",
        "metric": "cs_per_min",
        "op": "ge",
        "threshold": 8.0,
    },
    {
        "key": "objective_hunter",
        "name": "Cazador de Objetivos",
        "description": "Consigue 5 o mas objetivos (torres + dragones + barones).",
        "points": 17,
        "kind": "good",
        "metric": "objectives_total",
        "op": "ge",
        "threshold": 5,
    },
    {
        "key": "feed_alarm",
        "name": "Alarma de Feed",
        "description": "Muere 10 o mas veces en una partida.",
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
        "description": "K+A <= 2 y 6 o mas muertes.",
        "points": -10,
        "kind": "bad",
        "metric": "low_impact_flag",
        "op": "eq",
        "threshold": 1,
    },
    {
        "key": "flawless_commander",
        "name": "Comandante Impecable",
        "description": "Logro secreto: gana con KDA dominante y pocas muertes.",
        "points": 40,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 12,
        "extra": {"win": True, "max_deaths": 2, "min_assists": 10},
        "secret": True,
    },
    {
        "key": "vision_ghost",
        "name": "Fantasma del Mapa",
        "description": "Logro secreto: controla la vision con muy bajo riesgo.",
        "points": 28,
        "kind": "good",
        "metric": "vision_score",
        "op": "ge",
        "threshold": 55,
        "extra": {"max_deaths": 3},
        "secret": True,
    },
    {
        "key": "tower_reaper",
        "name": "Segador de Torres",
        "description": "Logro secreto: participa fuerte en objetivos estructurales.",
        "points": 26,
        "kind": "good",
        "metric": "turret_kills",
        "op": "ge",
        "threshold": 5,
        "secret": True,
    },
    {
        "key": "phoenix_game",
        "name": "Partida Fenix",
        "description": "Logro secreto: gana una partida de alto riesgo (8/8/8 minimo).",
        "points": 22,
        "kind": "good",
        "metric": "deaths",
        "op": "ge",
        "threshold": 8,
        "extra": {"win": True, "min_kills": 8, "min_assists": 8},
        "secret": True,
    },
]


LEVELS = [
    {"key": "unranked", "name": "Sin Rango", "min_points": -999999},
    {"key": "iron", "name": "Hierro", "min_points": 0},
    {"key": "bronze", "name": "Bronce", "min_points": 120},
    {"key": "silver", "name": "Plata", "min_points": 260},
    {"key": "gold", "name": "Oro", "min_points": 430},
    {"key": "platinum", "name": "Platino", "min_points": 640},
    {"key": "emerald", "name": "Esmeralda", "min_points": 880},
    {"key": "diamond", "name": "Diamante", "min_points": 1150},
    {"key": "master", "name": "Master", "min_points": 1450},
    {"key": "grandmaster", "name": "Grandmaster", "min_points": 1800},
    {"key": "challenger", "name": "Challenger", "min_points": 2200},
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
    if metric == "turret_kills":
        return match.get("turret_kills", 0) or 0
    if metric == "dragon_kills":
        return match.get("dragon_kills", 0) or 0
    if metric == "baron_kills":
        return match.get("baron_kills", 0) or 0
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


def _extra_conditions_pass(match, extra):
    if not extra:
        return True

    duration = match.get("game_duration", 0) or 0
    kills = match.get("kills", 0) or 0
    deaths = match.get("deaths", 0) or 0
    assists = match.get("assists", 0) or 0
    vision = match.get("vision_score", 0) or 0
    damage = match.get("total_damage_dealt_to_champions", 0) or 0
    cs_per_min = _metric_value(match, "cs_per_min")

    if "win" in extra and bool(match.get("win")) != bool(extra["win"]):
        return False
    if "min_duration" in extra and duration < extra["min_duration"]:
        return False
    if "max_duration" in extra and duration > extra["max_duration"]:
        return False

    if "min_kills" in extra and kills < extra["min_kills"]:
        return False
    if "max_kills" in extra and kills > extra["max_kills"]:
        return False
    if "min_deaths" in extra and deaths < extra["min_deaths"]:
        return False
    if "max_deaths" in extra and deaths > extra["max_deaths"]:
        return False
    if "min_assists" in extra and assists < extra["min_assists"]:
        return False
    if "max_assists" in extra and assists > extra["max_assists"]:
        return False

    if "min_vision_score" in extra and vision < extra["min_vision_score"]:
        return False
    if "max_vision_score" in extra and vision > extra["max_vision_score"]:
        return False
    if "min_damage" in extra and damage < extra["min_damage"]:
        return False
    if "min_cs_per_min" in extra and cs_per_min < extra["min_cs_per_min"]:
        return False
    if "max_cs_per_min" in extra and cs_per_min > extra["max_cs_per_min"]:
        return False

    return True


def _achievement_hit(match, definition):
    extra = definition.get("extra", {})

    if not _extra_conditions_pass(match, extra):
        return False, 0

    value = _metric_value(match, definition["metric"])
    hit = _compare(value, definition["op"], definition["threshold"])
    return hit, value


def _build_level_info(total_points):
    current = LEVELS[0]
    next_level = None

    for lvl in LEVELS:
        if total_points >= lvl["min_points"]:
            current = lvl
        elif next_level is None:
            next_level = lvl
            break

    if next_level is None:
        return {
            "level_key": current["key"],
            "level_name": current["name"],
            "level_min_points": current["min_points"],
            "next_level_name": None,
            "next_level_min_points": None,
            "points_to_next": 0,
            "level_progress_pct": 100,
        }

    span = max(1, next_level["min_points"] - current["min_points"])
    progress = int(((total_points - current["min_points"]) / span) * 100)
    progress = max(0, min(100, progress))

    return {
        "level_key": current["key"],
        "level_name": current["name"],
        "level_min_points": current["min_points"],
        "next_level_name": next_level["name"],
        "next_level_min_points": next_level["min_points"],
        "points_to_next": max(0, next_level["min_points"] - total_points),
        "level_progress_pct": progress,
    }


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
        "achievement_stats": [],
        "secret_achievements": [],
        "secret_unlocked": 0,
        "secret_locked": 0,
    }


def calculate_global_achievements():
    """
    Calculate achievements for all players using stored match history.
    """
    accounts = get_all_accounts()
    puuids = get_all_puuids()

    secret_catalog = [a for a in ACHIEVEMENTS if a.get("secret")]
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
            "secret": False,
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
                stat["secret"] = bool(definition.get("secret", False))
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
            reverse=True,
        )
        unlocked_keys = set(by_key.keys())

        secret_stats = []
        for secret_def in secret_catalog:
            if secret_def["key"] in unlocked_keys:
                secret_stats.append(dict(by_key[secret_def["key"]], locked=False))
            else:
                secret_stats.append({
                    "key": secret_def["key"],
                    "name": "???",
                    "description": "Logro secreto. Descubre como desbloquearlo.",
                    "points": secret_def["points"],
                    "kind": secret_def["kind"],
                    "secret": True,
                    "count": 0,
                    "locked": True,
                })

        player_row["achievement_stats"] = achievement_stats
        player_row["secret_achievements"] = secret_stats
        player_row["secret_unlocked"] = sum(1 for s in secret_stats if not s.get("locked"))
        player_row["secret_locked"] = sum(1 for s in secret_stats if s.get("locked"))
        player_row["unique_achievements"] = len(achievement_stats)
        player_row.update(_build_level_info(player_row["total_points"]))

        players.append(player_row)

    players.sort(
        key=lambda p: (
            p["total_points"],
            p["positive_points"],
            p["secret_unlocked"],
            p["unique_achievements"],
            p["hits_total"],
        ),
        reverse=True,
    )

    total_unlocked = sum(p["hits_total"] for p in players)
    total_secret_unlocked = sum(p["secret_unlocked"] for p in players)

    global_stats = {
        "players_count": len(players),
        "total_unlocked": total_unlocked,
        "total_secret_unlocked": total_secret_unlocked,
        "max_possible_unique": len(ACHIEVEMENTS),
        "max_possible_secret": len(secret_catalog),
    }

    return {
        "players": players,
        "achievements_catalog": ACHIEVEMENTS,
        "levels_catalog": LEVELS,
        "global_stats": global_stats,
    }
