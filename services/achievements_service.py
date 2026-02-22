"""
Achievement service based on stored match history.
Supports GitHub-driven challenge configuration with safe fallback.
"""

import json
import os
import time
from collections import defaultdict

from config.settings import ACHIEVEMENTS_CONFIG_PATH
from services.github_service import read_file_from_github
from services.player_service import get_all_accounts, get_all_puuids
from services.match_service import get_player_match_history

# ============================================================
# PLANTILLA DE DESAFIO (COPIAR / DESCOMENTAR / EDITAR)
# ============================================================
# Instrucciones:
# 1) Copia uno de los bloques y pegalo dentro de ACHIEVEMENTS.
# 2) Cambia "key" por un identificador unico (sin espacios).
# 3) Ajusta metric, op, threshold y opcionales.
# 4) Si defines "rank_tiers", NO necesitas difficulty/max_ranks.
#
# Metricas soportadas actualmente:
# - kills
# - deaths
# - assists
# - vision_score
# - total_damage_dealt_to_champions
# - turret_kills
# - dragon_kills
# - baron_kills
# - cs_per_min
# - objectives_total
# - low_impact_flag
#
# Operadores soportados:
# - ge (>=)
# - gt (>)
# - le (<=)
# - lt (<)
# - eq (==)
#
# Extras soportados:
# - win: True/False
# - min_duration, max_duration
# - min_kills, max_kills
# - min_deaths, max_deaths
# - min_assists, max_assists
# - min_vision_score, max_vision_score
# - min_damage
# - min_cs_per_min, max_cs_per_min
#
# ------------------------------------------------------------
# PLANTILLA A: desafio normal con rangos automaticos
# ------------------------------------------------------------
# {
#     "key": "mi_desafio_normal",
#     "name": "Mi Desafio Normal",
#     "description": "Haz 12 o mas kills en una partida.",
#     "points": 20,                 # base para calcular puntos por rango
#     "kind": "good",               # "good" o "bad"
#     "metric": "kills",
#     "op": "ge",
#     "threshold": 12,
#     "difficulty": "medium",       # easy | medium | hard | extreme
#     "max_ranks": 5,               # maximo 5
#     # "extra": {"win": True}      # opcional
# },
#
# ------------------------------------------------------------
# PLANTILLA B: desafio con rangos personalizados
# ------------------------------------------------------------
# {
#     "key": "mi_desafio_custom",
#     "name": "Mi Desafio Custom",
#     "description": "Gana con 0 muertes.",
#     "points": 25,                 # se mantiene como referencia
#     "kind": "good",
#     "metric": "deaths",
#     "op": "eq",
#     "threshold": 0,
#     "extra": {"win": True},
#     "rank_tiers": [
#         {"name": "Rango I", "min_count": 1, "points": 8},
#         {"name": "Rango II", "min_count": 3, "points": 16},
#         {"name": "Rango III", "min_count": 5, "points": 24},
#         {"name": "Rango IV", "min_count": 10, "points": 35},
#         {"name": "Rango V", "min_count": 15, "points": 48},
#     ],
# },
#
# ------------------------------------------------------------
# PLANTILLA C: desafio secreto (normalmente 1 rango)
# ------------------------------------------------------------
# {
#     "key": "mi_secreto",
#     "name": "Mi Secreto",
#     "description": "Haz algo dificil en partida.",
#     "points": 40,
#     "kind": "good",
#     "metric": "vision_score",
#     "op": "ge",
#     "threshold": 60,
#     "extra": {"max_deaths": 2},
#     "secret": True,               # se mostrara sombreado hasta descubrirse
#     "difficulty": "extreme",
#     "max_ranks": 1
# },
# ============================================================


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
        "difficulty": "easy",
        "max_ranks": 5,
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
        "difficulty": "hard",
        "max_ranks": 2,
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
        "difficulty": "medium",
        "max_ranks": 4,
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
        "rank_tiers": [
            {"name": "Rango I", "min_count": 1, "points": 6},
            {"name": "Rango II", "min_count": 3, "points": 12},
            {"name": "Rango III", "min_count": 5, "points": 18},
            {"name": "Rango IV", "min_count": 10, "points": 26},
        ],
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
        "difficulty": "medium",
        "max_ranks": 5,
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
        "difficulty": "medium",
        "max_ranks": 4,
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
        "difficulty": "hard",
        "max_ranks": 3,
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
        "difficulty": "hard",
        "max_ranks": 3,
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
        "difficulty": "easy",
        "max_ranks": 5,
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
        "difficulty": "medium",
        "max_ranks": 4,
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
        "difficulty": "medium",
        "max_ranks": 4,
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
        "difficulty": "hard",
        "max_ranks": 3,
    },
    {
        "key": "flawless_commander",
        "name": "Comandante Impecable",
        "description": "Logro secreto: gana con KDA dominante y pocas muertes.",
        "points": 24,
        "kind": "good",
        "metric": "kills",
        "op": "ge",
        "threshold": 12,
        "extra": {"win": True, "max_deaths": 2, "min_assists": 10},
        "secret": True,
        "difficulty": "extreme",
        "max_ranks": 1,
    },
    {
        "key": "vision_ghost",
        "name": "Fantasma del Mapa",
        "description": "Logro secreto: controla la vision con muy bajo riesgo.",
        "points": 18,
        "kind": "good",
        "metric": "vision_score",
        "op": "ge",
        "threshold": 55,
        "extra": {"max_deaths": 3},
        "secret": True,
        "difficulty": "extreme",
        "max_ranks": 1,
    },
    {
        "key": "tower_reaper",
        "name": "Segador de Torres",
        "description": "Logro secreto: participa fuerte en objetivos estructurales.",
        "points": 18,
        "kind": "good",
        "metric": "turret_kills",
        "op": "ge",
        "threshold": 5,
        "secret": True,
        "difficulty": "extreme",
        "max_ranks": 1,
    },
    {
        "key": "phoenix_game",
        "name": "Partida Fenix",
        "description": "Logro secreto: gana una partida de alto riesgo (8/8/8 minimo).",
        "points": 16,
        "kind": "good",
        "metric": "deaths",
        "op": "ge",
        "threshold": 8,
        "extra": {"win": True, "min_kills": 8, "min_assists": 8},
        "secret": True,
        "difficulty": "extreme",
        "max_ranks": 1,
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

# Fixed point levels up to Gold (editable).
LOW_FIXED_LEVELS = [
    {"key": "unranked", "name": "Sin Rango", "min_points": -999999},
    {"key": "iron", "name": "Hierro", "min_points": 0},
    {"key": "bronze", "name": "Bronce", "min_points": 35},
    {"key": "silver", "name": "Plata", "min_points": 70},
    {"key": "gold", "name": "Oro", "min_points": 105},
]

# Percentage-based levels from Platinum onward.
HIGH_PERCENT_LEVELS = [
    {"key": "platinum", "name": "Platino", "pct": 0.60},
    {"key": "emerald", "name": "Esmeralda", "pct": 0.72},
    {"key": "diamond", "name": "Diamante", "pct": 0.82},
    {"key": "master", "name": "Master", "pct": 0.90},
    {"key": "grandmaster", "name": "Grandmaster", "pct": 0.94},
]
CHALLENGER_PCT = 0.95

RANK_LABELS = ["I", "II", "III", "IV", "V"]
RANK_FACTOR_BY_INDEX = [0.35, 0.5, 0.65, 0.85, 1.0]
DIFFICULTY_STEPS = {
    "easy": [1, 4, 8, 14, 22],
    "medium": [1, 3, 6, 10, 16],
    "hard": [1, 3, 5, 8, 12],
    "extreme": [1],
}

CONFIG_CACHE_TTL_SECONDS = 300
_achievements_config_cache = {
    "timestamp": 0,
    "achievements": None,
    "source": "fallback",
    "errors": [],
}

VALID_KINDS = {"good", "bad"}
VALID_METRICS = {
    "kills",
    "deaths",
    "assists",
    "vision_score",
    "total_damage_dealt_to_champions",
    "turret_kills",
    "dragon_kills",
    "baron_kills",
    "cs_per_min",
    "objectives_total",
    "low_impact_flag",
}
VALID_OPS = {"ge", "gt", "le", "lt", "eq"}
VALID_DIFFICULTIES = {"easy", "medium", "hard", "extreme"}


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


def _extract_achievements_payload(content):
    if content is None:
        return None
    if isinstance(content, list):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("achievements"), list):
            return content["achievements"]
    return None


def _validate_rank_tiers(rank_tiers, idx):
    errors = []
    if not isinstance(rank_tiers, list) or not rank_tiers:
        return [f"Entry {idx}: rank_tiers must be a non-empty list"]
    if len(rank_tiers) > 5:
        errors.append(f"Entry {idx}: rank_tiers supports at most 5 tiers")

    prev_min = -1
    for i, tier in enumerate(rank_tiers):
        if not isinstance(tier, dict):
            errors.append(f"Entry {idx}: rank_tiers[{i}] must be an object")
            continue
        name = tier.get("name")
        min_count = tier.get("min_count")
        points = tier.get("points")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"Entry {idx}: rank_tiers[{i}].name must be a non-empty string")
        if not isinstance(min_count, int) or min_count < 1:
            errors.append(f"Entry {idx}: rank_tiers[{i}].min_count must be int >= 1")
        if isinstance(min_count, int) and min_count <= prev_min:
            errors.append(f"Entry {idx}: rank_tiers min_count must be strictly increasing")
        if isinstance(min_count, int):
            prev_min = min_count
        if not isinstance(points, int) or points < 1:
            errors.append(f"Entry {idx}: rank_tiers[{i}].points must be int >= 1")
    return errors


def _validate_achievements_config(entries):
    errors = []
    if not isinstance(entries, list):
        return False, ["Config must be a list of achievements"]
    if not entries:
        return False, ["Config achievements list is empty"]

    keys_seen = set()
    for idx, ach in enumerate(entries):
        if not isinstance(ach, dict):
            errors.append(f"Entry {idx}: must be an object")
            continue

        key = ach.get("key")
        if not isinstance(key, str) or not key.strip():
            errors.append(f"Entry {idx}: key must be a non-empty string")
        elif key in keys_seen:
            errors.append(f"Entry {idx}: duplicate key '{key}'")
        else:
            keys_seen.add(key)

        if ach.get("kind") not in VALID_KINDS:
            errors.append(f"Entry {idx}: kind must be one of {sorted(VALID_KINDS)}")
        if ach.get("metric") not in VALID_METRICS:
            errors.append(f"Entry {idx}: metric '{ach.get('metric')}' is not supported")
        if ach.get("op") not in VALID_OPS:
            errors.append(f"Entry {idx}: op must be one of {sorted(VALID_OPS)}")

        for text_field in ("name", "description"):
            val = ach.get(text_field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"Entry {idx}: {text_field} must be a non-empty string")

        threshold = ach.get("threshold")
        if not isinstance(threshold, (int, float)):
            errors.append(f"Entry {idx}: threshold must be numeric")

        points = ach.get("points")
        if not isinstance(points, int):
            errors.append(f"Entry {idx}: points must be integer")

        if "secret" in ach and not isinstance(ach.get("secret"), bool):
            errors.append(f"Entry {idx}: secret must be boolean")

        if "rank_tiers" in ach:
            errors.extend(_validate_rank_tiers(ach.get("rank_tiers"), idx))
        else:
            difficulty = ach.get("difficulty", "medium")
            if difficulty not in VALID_DIFFICULTIES:
                errors.append(f"Entry {idx}: difficulty must be one of {sorted(VALID_DIFFICULTIES)}")
            max_ranks = ach.get("max_ranks", 5)
            if not isinstance(max_ranks, int) or max_ranks < 1 or max_ranks > 5:
                errors.append(f"Entry {idx}: max_ranks must be int in [1,5]")

    return len(errors) == 0, errors


def _load_local_achievements_file():
    local_path = os.path.join("config", "logros", "achievements_config.json")
    if not os.path.exists(local_path):
        return None
    try:
        with open(local_path, "r", encoding="utf-8") as f:
            content = json.load(f)
        return _extract_achievements_payload(content)
    except Exception as e:
        print(f"[achievements] Failed loading local config file: {e}")
        return None


def get_active_achievements(force_refresh=False):
    now = time.time()
    cache_age = now - _achievements_config_cache["timestamp"]
    if (
        not force_refresh
        and _achievements_config_cache["achievements"] is not None
        and cache_age <= CONFIG_CACHE_TTL_SECONDS
    ):
        return _achievements_config_cache["achievements"], _achievements_config_cache["source"], _achievements_config_cache["errors"]

    errors = []
    github_content, _ = read_file_from_github(ACHIEVEMENTS_CONFIG_PATH)
    github_entries = _extract_achievements_payload(github_content)
    if github_entries is not None:
        is_valid, validation_errors = _validate_achievements_config(github_entries)
        if is_valid:
            _achievements_config_cache.update(
                {
                    "timestamp": now,
                    "achievements": github_entries,
                    "source": f"github:{ACHIEVEMENTS_CONFIG_PATH}",
                    "errors": [],
                }
            )
            return github_entries, _achievements_config_cache["source"], []
        errors.extend([f"github: {e}" for e in validation_errors])
    else:
        errors.append("github: config not found or invalid payload")

    local_entries = _load_local_achievements_file()
    if local_entries is not None:
        is_valid, validation_errors = _validate_achievements_config(local_entries)
        if is_valid:
            _achievements_config_cache.update(
                {
                    "timestamp": now,
                    "achievements": local_entries,
                    "source": "local:config/logros/achievements_config.json",
                    "errors": errors,
                }
            )
            return local_entries, _achievements_config_cache["source"], errors
        errors.extend([f"local: {e}" for e in validation_errors])
    else:
        errors.append("local: config not found")

    _achievements_config_cache.update(
        {
            "timestamp": now,
            "achievements": ACHIEVEMENTS,
            "source": "fallback:embedded",
            "errors": errors,
        }
    )
    return ACHIEVEMENTS, _achievements_config_cache["source"], errors


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


def _build_dynamic_level_track(max_possible_points):
    track = [dict(x) for x in LOW_FIXED_LEVELS]
    last_min = LOW_FIXED_LEVELS[-1]["min_points"]
    for lvl in HIGH_PERCENT_LEVELS:
        min_points = int(round(max_possible_points * lvl["pct"]))
        if min_points <= last_min:
            min_points = last_min + 1
        track.append(
            {
                "key": lvl["key"],
                "name": lvl["name"],
                "min_points": min_points,
                "pct": lvl["pct"],
            }
        )
        last_min = min_points
    challenger_min = int(round(max_possible_points * CHALLENGER_PCT))
    if challenger_min <= last_min:
        challenger_min = last_min + 1
    return track, challenger_min


def _build_level_info(total_points, max_possible_points, top_points, top_count):
    level_track, challenger_min = _build_dynamic_level_track(max_possible_points)

    current = level_track[0]
    next_level = None
    for lvl in level_track:
        if total_points >= lvl["min_points"]:
            current = lvl
        elif next_level is None:
            next_level = lvl
            break

    is_challenger = (
        total_points >= challenger_min
        and total_points == top_points
        and top_count == 1
    )

    if is_challenger:
        return {
            "level_key": "challenger",
            "level_name": "Challenger",
            "level_min_points": challenger_min,
            "next_level_name": None,
            "next_level_min_points": None,
            "points_to_next": 0,
            "level_progress_pct": 100,
            "score_pct": round((max(0, total_points) / max(1, max_possible_points)) * 100, 2),
            "challenger_min_points": challenger_min,
        }

    if next_level is None:
        return {
            "level_key": current["key"],
            "level_name": current["name"],
            "level_min_points": current["min_points"],
            "next_level_name": None,
            "next_level_min_points": None,
            "points_to_next": 0,
            "level_progress_pct": 100,
            "score_pct": round((max(0, total_points) / max(1, max_possible_points)) * 100, 2),
            "challenger_min_points": challenger_min,
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
        "score_pct": round((max(0, total_points) / max(1, max_possible_points)) * 100, 2),
        "challenger_min_points": challenger_min,
    }


def _calculate_max_possible_points(active_achievements):
    total = 0
    for definition in active_achievements:
        if definition.get("kind") != "good":
            continue
        tiers = _get_achievement_tiers(definition)
        if not tiers:
            continue
        total += max(t["points"] for t in tiers)
    return total

def _get_achievement_tiers(definition):
    custom_tiers = definition.get("rank_tiers") or []
    if custom_tiers:
        tiers = []
        for idx, item in enumerate(custom_tiers[:5]):
            tiers.append(
                {
                    "tier_key": f"tier_{idx + 1}",
                    "name": item.get("name") or f'Rango {RANK_LABELS[idx]}',
                    "min_count": int(item.get("min_count", 1)),
                    "points": int(item.get("points", 1)),
                }
            )
        return tiers

    difficulty = definition.get("difficulty", "medium")
    steps = DIFFICULTY_STEPS.get(difficulty, DIFFICULTY_STEPS["medium"])
    max_ranks = max(1, min(5, int(definition.get("max_ranks", 5))))
    steps = steps[:max_ranks]

    base_points = max(1, abs(int(definition.get("points", 1))))
    tiers = []
    for idx, step in enumerate(steps):
        factor = RANK_FACTOR_BY_INDEX[idx]
        if max_ranks == 1:
            factor = 1.0
        points = max(1, int(round(base_points * factor)))
        tiers.append(
            {
                "tier_key": f"tier_{idx + 1}",
                "name": f'Rango {RANK_LABELS[idx]}',
                "min_count": int(step),
                "points": points,
            }
        )
    return tiers


def _build_achievement_rank(definition, count):
    tiers = _get_achievement_tiers(definition)
    if not tiers:
        return {
            "tier_key": "unranked",
            "tier_name": "Sin rango",
            "tier_points": 0,
            "points_to_next_tier": 0,
            "next_tier_name": None,
            "max_tier_reached": False,
        }

    current = None
    next_tier = None
    for tier in tiers:
        if count >= tier["min_count"]:
            current = tier
        elif next_tier is None:
            next_tier = tier
            break

    if current is None:
        return {
            "tier_key": "unranked",
            "tier_name": "Sin rango",
            "tier_points": 0,
            "points_to_next_tier": max(0, tiers[0]["min_count"] - count),
            "next_tier_name": tiers[0]["name"],
            "max_tier_reached": False,
        }

    return {
        "tier_key": current["tier_key"],
        "tier_name": current["name"],
        "tier_points": current["points"],
        "points_to_next_tier": 0 if next_tier is None else max(0, next_tier["min_count"] - count),
        "next_tier_name": None if next_tier is None else next_tier["name"],
        "max_tier_reached": next_tier is None,
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
        "achievement_counts": {},
        "secret_achievements": [],
        "secret_unlocked": 0,
        "secret_locked": 0,
        "tier_points_total": 0,
    }


def calculate_global_achievements():
    """
    Calculate achievements for all players using stored match history.
    """
    accounts = get_all_accounts()
    puuids = get_all_puuids()
    active_achievements, config_source, config_errors = get_active_achievements()

    secret_catalog = [a for a in active_achievements if a.get("secret")]
    public_catalog = [a for a in active_achievements if not a.get("secret")]
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
            for definition in active_achievements:
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

        achievement_stats = sorted(
            by_key.values(),
            key=lambda x: (x["count"] * abs(x["points"]), x["points"], x["count"]),
            reverse=True,
        )
        unlocked_keys = set(by_key.keys())

        definition_by_key = {a["key"]: a for a in active_achievements}

        # Puntos por rangos por desafio individual.
        for stat in achievement_stats:
            definition = definition_by_key.get(stat["key"], {})
            rank_info = _build_achievement_rank(definition, stat["count"])
            stat.update(rank_info)
            signed_points = rank_info["tier_points"]
            if stat["kind"] == "bad":
                signed_points = -signed_points
            stat["rank_points"] = signed_points

            player_row["total_points"] += signed_points
            if signed_points >= 0:
                player_row["positive_points"] += signed_points
            else:
                player_row["negative_points"] += signed_points
            player_row["tier_points_total"] += abs(signed_points)

        secret_stats = []
        for secret_def in secret_catalog:
            if secret_def["key"] in unlocked_keys:
                secret_stats.append(dict(by_key[secret_def["key"]], locked=False))
            else:
                secret_stats.append(
                    {
                        "key": secret_def["key"],
                        "name": "???",
                        "description": "Logro secreto aun no descubierto.",
                        "points": secret_def["points"],
                        "kind": secret_def["kind"],
                        "secret": True,
                        "count": 0,
                        "locked": True,
                    }
                )

        player_row["achievement_stats"] = achievement_stats
        player_row["achievement_counts"] = {
            item["key"]: item["count"] for item in achievement_stats
        }
        player_row["secret_achievements"] = secret_stats
        player_row["secret_unlocked"] = sum(1 for x in secret_stats if not x.get("locked"))
        player_row["secret_locked"] = sum(1 for x in secret_stats if x.get("locked"))
        player_row["unique_achievements"] = len(achievement_stats)

        players.append(player_row)

    max_possible_points = _calculate_max_possible_points(active_achievements)
    top_points = max((p["total_points"] for p in players), default=0)
    top_count = sum(1 for p in players if p["total_points"] == top_points)
    for player_row in players:
        player_row.update(
            _build_level_info(
                player_row["total_points"],
                max_possible_points,
                top_points,
                top_count,
            )
        )

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
    total_unique_unlocks = sum(p["unique_achievements"] for p in players)
    total_rank_points = sum(p["total_points"] for p in players)

    achievements_view = []
    for definition in public_catalog:
        achievers = []
        total_hits = 0
        for player in players:
            count = player["achievement_counts"].get(definition["key"], 0)
            if count <= 0:
                continue
            total_hits += count
            achievers.append(
                {
                    "player_name": player["player_name"],
                    "riot_id": player["riot_id"],
                    "count": count,
                    "level_name": player["level_name"],
                    "total_points": player["total_points"],
                    "challenge_rank": _build_achievement_rank(definition, count),
                }
            )
        achievements_view.append(
            {
                "key": definition["key"],
                "name": definition["name"],
                "description": definition["description"],
                "points": definition["points"],
                "kind": definition["kind"],
                "is_secret": False,
                "achievers": achievers,
                "achievers_count": len(achievers),
                "total_hits": total_hits,
                "global_rank": _build_achievement_rank(definition, total_hits),
                "rank_tiers": _get_achievement_tiers(definition),
            }
        )

    secret_achievements_view = []
    for definition in secret_catalog:
        achievers = []
        total_hits = 0
        for player in players:
            count = player["achievement_counts"].get(definition["key"], 0)
            if count <= 0:
                continue
            total_hits += count
            achievers.append(
                {
                    "player_name": player["player_name"],
                    "riot_id": player["riot_id"],
                    "count": count,
                    "level_name": player["level_name"],
                    "total_points": player["total_points"],
                    "challenge_rank": _build_achievement_rank(definition, count),
                }
            )
        secret_achievements_view.append(
            {
                "key": definition["key"],
                "name": definition["name"] if achievers else "???",
                "description": definition["description"] if achievers else "Logro secreto aun no descubierto.",
                "points": definition["points"],
                "kind": definition["kind"],
                "is_secret": True,
                "locked": len(achievers) == 0,
                "achievers": achievers,
                "achievers_count": len(achievers),
                "total_hits": total_hits,
                "global_rank": _build_achievement_rank(definition, total_hits),
                "rank_tiers": _get_achievement_tiers(definition),
            }
        )

    global_stats = {
        "players_count": len(players),
        "total_unlocked": total_unlocked,
        "total_unique_unlocks": total_unique_unlocks,
        "total_rank_points": total_rank_points,
        "total_secret_unlocked": total_secret_unlocked,
        "max_possible_unique": len(active_achievements),
        "max_possible_public": len(public_catalog),
        "max_possible_secret": len(secret_catalog),
        "max_possible_points": max_possible_points,
        "challenger_min_points": int(round(max_possible_points * CHALLENGER_PCT)),
        "config_source": config_source,
        "config_errors_count": len(config_errors),
    }

    return {
        "players": players,
        "achievements_catalog": active_achievements,
        "achievements_view": achievements_view,
        "secret_achievements_view": secret_achievements_view,
        "levels_catalog": LEVELS,
        "global_stats": global_stats,
        "config_source": config_source,
        "config_errors": config_errors,
    }
