"""
Servicio para seguimiento del rendimiento colectivo de un equipo.
"""

import json
import os
from collections import Counter, defaultdict

from config.settings import QUEUE_NAMES
from services.github_service import read_file_from_github, write_file_to_github
from services.match_service import get_player_match_history
from services.player_service import get_all_puuids


TEAM_CONFIG_PATH = "team_tracker.json"
TEAM_LOGO_UPLOAD_DIR = os.path.join("static", "uploads")
TEAM_LOGO_FILENAME = "team_logo.png"
TEAM_LOGO_STATIC_PATH = "uploads/team_logo.png"
ROLE_ORDER = {
    "TOP": 0,
    "JUNGLE": 1,
    "JUNGLER": 1,
    "MIDDLE": 2,
    "MID": 2,
    "BOTTOM": 3,
    "ADC": 3,
    "UTILITY": 4,
    "SUPPORT": 4,
    "SUP": 4,
}


def get_team_config():
    """Lee la configuracion del equipo o genera un fallback con los 5 primeros PUUIDs."""
    if os.path.exists(TEAM_CONFIG_PATH):
        try:
            with open(TEAM_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            return _normalize_team_config(config)
        except Exception as e:
            print(f"[team_service] Error leyendo {TEAM_CONFIG_PATH}: {e}")

    puuids = _get_known_puuids()
    players = []
    for riot_id, puuid in list(puuids.items())[:5]:
        players.append({
            "riot_id": riot_id,
            "display_name": _get_display_name(riot_id),
            "puuid": puuid,
            "role": "",
        })

    return {
        "name": "Equipo Principal",
        "players": players,
        "logo_path": "",
        "config_path": TEAM_CONFIG_PATH,
        "is_fallback": True,
    }


def save_team_logo(uploaded_file):
    """Guarda el logo del equipo si es un PNG valido y actualiza la configuracion."""
    if not uploaded_file or not uploaded_file.filename:
        return False, "Selecciona un archivo PNG."

    if not uploaded_file.filename.lower().endswith(".png"):
        return False, "El logo debe ser un archivo .png."

    try:
        signature = uploaded_file.stream.read(8)
        uploaded_file.stream.seek(0)
    except Exception:
        return False, "No se pudo leer el archivo."

    if signature != b"\x89PNG\r\n\x1a\n":
        return False, "El archivo no parece ser un PNG valido."

    os.makedirs(TEAM_LOGO_UPLOAD_DIR, exist_ok=True)
    destination = os.path.join(TEAM_LOGO_UPLOAD_DIR, TEAM_LOGO_FILENAME)
    uploaded_file.save(destination)
    _update_team_config({"logo_path": TEAM_LOGO_STATIC_PATH})
    return True, "Logo del equipo actualizado."


TEAM_MATCHES_CACHE_PATH = "team_matches.json"


def build_team_dashboard():
    """Construye el dataset de la pestana de equipo."""
    config = get_team_config()
    players = config.get("players", [])
    complete_roster = [p for p in players if p.get("puuid")]

    if len(complete_roster) != 5:
        return {
            "config": config,
            "summary": _empty_summary(),
            "aggregate_summary": _empty_summary(),
            "team_matches": [],
            "queue_stats": [],
            "recent_form": [],
            "champion_compositions": [],
            "missing_roster": True,
        }

    # Intentar leer del caché
    cached_data = _load_team_matches_cache(config, complete_roster)
    if cached_data:
        team_matches = cached_data["team_matches"]
        print(f"[team_service] Usando caché de {len(team_matches)} partidas del equipo")
    else:
        # Calcular desde cero
        print("[team_service] Calculando partidas del equipo desde historiales...")
        team_matches = _compute_team_matches(complete_roster)
        _save_team_matches_cache(config, complete_roster, team_matches)

    return {
        "config": config,
        "summary": _build_summary(team_matches),
        "aggregate_summary": _build_aggregate_summary(complete_roster),
        "team_matches": team_matches,
        "queue_stats": _build_queue_stats(team_matches),
        "recent_form": team_matches[:10],
        "champion_compositions": _build_champion_compositions(team_matches),
        "missing_roster": False,
    }


def _normalize_team_config(config):
    puuids = _get_known_puuids()
    players = []
    for player in config.get("players", []):
        riot_id = player.get("riot_id", "").strip()
        puuid = player.get("puuid") or puuids.get(riot_id)
        players.append({
            "riot_id": riot_id,
            "display_name": player.get("display_name") or _get_display_name(riot_id),
            "puuid": puuid,
            "role": player.get("role", ""),
        })

    return {
        "name": config.get("name", "Equipo Principal"),
        "players": players,
        "logo_path": config.get("logo_path", ""),
        "config_path": TEAM_CONFIG_PATH,
        "is_fallback": False,
    }


def _update_team_config(updates):
    config = _read_json_file(TEAM_CONFIG_PATH)
    if not isinstance(config, dict):
        current = get_team_config()
        config = {
            "name": current.get("name", "Equipo Principal"),
            "players": [
                {
                    "riot_id": p.get("riot_id", ""),
                    "role": p.get("role", ""),
                }
                for p in current.get("players", [])
            ],
        }

    config.update(updates)
    with open(TEAM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _get_known_puuids():
    local_puuids = _read_json_file("puuids.json")
    if isinstance(local_puuids, dict) and local_puuids:
        return local_puuids
    return get_all_puuids()


def _get_display_name(riot_id):
    accounts = _read_local_accounts()
    return accounts.get(riot_id) or riot_id


def _read_local_accounts():
    if not os.path.exists("cuentas.txt"):
        return {}

    try:
        with open("cuentas.txt", "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {}

    accounts = {}
    for raw_line in content.strip().split(";"):
        if not raw_line.strip() or "," not in raw_line:
            continue
        riot_id, display_name = raw_line.split(",", 1)
        accounts[riot_id.strip()] = display_name.strip()
    return accounts


def _get_match_history(puuid, riot_id):
    local_history = _read_local_match_history(puuid)
    if local_history is not None:
        return local_history
    if not _allow_remote_reads():
        return {"matches": [], "remakes": [], "last_updated": 0}
    return get_player_match_history(puuid, riot_id=riot_id, limit=-1)


def _allow_remote_reads():
    explicit = os.environ.get("TEAM_TRACKER_ALLOW_REMOTE")
    if explicit is not None:
        return explicit == "1"

    render_env_vars = (
        "RENDER",
        "RENDER_SERVICE_ID",
        "RENDER_EXTERNAL_URL",
        "RENDER_EXTERNAL_HOSTNAME",
    )
    return bool(os.environ.get("GITHUB_TOKEN") or any(os.environ.get(k) for k in render_env_vars))


def _read_local_match_history(puuid):
    legacy_path = os.path.join("match_history", f"{puuid}.json")
    legacy_history = _read_json_file(legacy_path)
    if isinstance(legacy_history, dict):
        return legacy_history

    index_path = os.path.join("match_history", puuid, "index.json")
    index = _read_json_file(index_path)
    if not isinstance(index, dict):
        return None

    matches = []
    remakes = []
    for file_path in index.get("files", []):
        chunk_path = os.path.join("match_history", puuid, file_path)
        chunk = _read_json_file(chunk_path)
        if not isinstance(chunk, dict):
            continue
        matches.extend(chunk.get("matches", []))
        remakes.extend(chunk.get("remakes", []))

    seen = set()
    unique_matches = []
    for match in sorted(matches, key=lambda x: x.get("game_end_timestamp", 0), reverse=True):
        match_id = match.get("match_id")
        if match_id and match_id in seen:
            continue
        if match_id:
            seen.add(match_id)
        unique_matches.append(match)

    return {
        "matches": unique_matches,
        "remakes": remakes,
        "last_updated": index.get("last_updated", 0),
    }


def _read_json_file(path):
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[team_service] Error leyendo {path}: {e}")
        return None


def _match_contains_roster(match, team_puuids):
    participants = match.get("all_participants") or []
    participant_by_puuid = {p.get("puuid"): p for p in participants if p.get("puuid")}
    if not team_puuids.issubset(set(participant_by_puuid.keys())):
        return False

    team_ids = {participant_by_puuid[puuid].get("team_id") for puuid in team_puuids}
    return len(team_ids) == 1


def _build_team_match(match_id, representative, player_matches, roster):
    participants = representative.get("all_participants") or []
    participant_by_puuid = {p.get("puuid"): p for p in participants if p.get("puuid")}
    roster_puuids = [p["puuid"] for p in roster]
    team_participants = [participant_by_puuid.get(puuid) for puuid in roster_puuids]
    known_participants = [p for p in team_participants if p]

    team_id = None
    same_team = True
    if len(known_participants) == 5:
        team_ids = {p.get("team_id") for p in known_participants}
        if len(team_ids) != 1:
            same_team = False
        else:
            team_id = next(iter(team_ids))

    if not same_team:
        return None

    player_rows = []
    for roster_player in roster:
        puuid = roster_player["puuid"]
        match = player_matches.get(puuid) or {}
        participant = participant_by_puuid.get(puuid) or {}
        position = match.get("individual_position") or roster_player.get("role") or ""
        player_rows.append({
            "display_name": roster_player.get("display_name") or roster_player.get("riot_id"),
            "riot_id": roster_player.get("riot_id"),
            "puuid": puuid,
            "role": roster_player.get("role", ""),
            "position": position,
            "champion_name": match.get("champion_name") or participant.get("champion_name") or "Desconocido",
            "kills": _num(match.get("kills", participant.get("kills"))),
            "deaths": _num(match.get("deaths", participant.get("deaths"))),
            "assists": _num(match.get("assists", participant.get("assists"))),
            "damage": _num(match.get("total_damage_dealt_to_champions", participant.get("total_damage_dealt_to_champions"))),
            "vision": _num(match.get("vision_score", participant.get("vision_score"))),
            "gold": _num(match.get("gold_earned")),
            "cs": _num(match.get("total_minions_killed")) + _num(match.get("neutral_minions_killed")),
            "team_id": participant.get("team_id", team_id),
        })

    player_rows.sort(key=lambda p: ROLE_ORDER.get((p.get("position") or p.get("role") or "").upper(), 99))

    kills = sum(p["kills"] for p in player_rows)
    deaths = sum(p["deaths"] for p in player_rows)
    assists = sum(p["assists"] for p in player_rows)
    win = bool(known_participants[0].get("win") if known_participants else representative.get("win"))
    lp_change = sum(_num(m.get("lp_change_this_game")) for m in player_matches.values())

    return {
        "match_id": match_id,
        "game_end_timestamp": representative.get("game_end_timestamp", 0),
        "queue_id": representative.get("queue_id"),
        "queue_name": QUEUE_NAMES.get(representative.get("queue_id"), "Desconocida"),
        "game_duration": representative.get("game_duration", 0),
        "duration_label": _format_duration(representative.get("game_duration", 0)),
        "win": win,
        "result_label": "Victoria" if win else "Derrota",
        "team_id": team_id,
        "players": player_rows,
        "composition": [p["champion_name"] for p in player_rows],
        "team_kills": kills,
        "team_deaths": deaths,
        "team_assists": assists,
        "team_kda": (kills + assists) / max(1, deaths),
        "team_damage": sum(p["damage"] for p in player_rows),
        "team_vision": sum(p["vision"] for p in player_rows),
        "team_gold": sum(p["gold"] for p in player_rows),
        "team_cs": sum(p["cs"] for p in player_rows),
        "turret_kills": sum(_num(m.get("turret_kills")) for m in player_matches.values()),
        "inhibitor_kills": sum(_num(m.get("inhibitor_kills")) for m in player_matches.values()),
        "dragon_kills": sum(_num(m.get("dragon_kills")) for m in player_matches.values()),
        "baron_kills": sum(_num(m.get("baron_kills")) for m in player_matches.values()),
        "objectives_stolen": sum(_num(m.get("objectives_stolen")) for m in player_matches.values()),
        "lp_change": lp_change,
    }


def _build_summary(team_matches):
    if not team_matches:
        return _empty_summary()

    wins = sum(1 for m in team_matches if m.get("win"))
    losses = len(team_matches) - wins
    recent = team_matches[:10]

    return {
        "total_matches": len(team_matches),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(team_matches) * 100,
        "current_streak": _calculate_streak(team_matches),
        "recent_wins": sum(1 for m in recent if m.get("win")),
        "recent_losses": sum(1 for m in recent if not m.get("win")),
        "recent_win_rate": (sum(1 for m in recent if m.get("win")) / len(recent) * 100) if recent else 0,
        "lp_change": sum(m.get("lp_change", 0) for m in team_matches),
        "avg_duration": _format_duration(sum(m.get("game_duration", 0) for m in team_matches) / len(team_matches)),
        "avg_kda": sum(m.get("team_kda", 0) for m in team_matches) / len(team_matches),
        "avg_damage": sum(m.get("team_damage", 0) for m in team_matches) / len(team_matches),
        "avg_vision": sum(m.get("team_vision", 0) for m in team_matches) / len(team_matches),
        "avg_dragons": sum(m.get("dragon_kills", 0) for m in team_matches) / len(team_matches),
        "avg_barons": sum(m.get("baron_kills", 0) for m in team_matches) / len(team_matches),
        "last_match": team_matches[0],
    }


def _build_queue_stats(team_matches):
    grouped = defaultdict(list)
    for match in team_matches:
        grouped[match.get("queue_id")].append(match)

    stats = []
    for queue_id, matches in grouped.items():
        wins = sum(1 for m in matches if m.get("win"))
        stats.append({
            "queue_id": queue_id,
            "queue_name": QUEUE_NAMES.get(queue_id, "Desconocida"),
            "matches": len(matches),
            "wins": wins,
            "losses": len(matches) - wins,
            "win_rate": wins / len(matches) * 100 if matches else 0,
            "lp_change": sum(m.get("lp_change", 0) for m in matches),
        })

    return sorted(stats, key=lambda s: s["matches"], reverse=True)


def _build_champion_compositions(team_matches):
    counter = Counter()
    wins = Counter()
    last_seen = {}

    for match in team_matches:
        composition = tuple(match.get("composition") or [])
        if not composition:
            continue
        counter[composition] += 1
        if match.get("win"):
            wins[composition] += 1
        last_seen.setdefault(composition, match.get("game_end_timestamp", 0))

    rows = []
    for composition, count in counter.most_common(8):
        rows.append({
            "composition": list(composition),
            "matches": count,
            "wins": wins[composition],
            "losses": count - wins[composition],
            "win_rate": wins[composition] / count * 100,
            "last_seen": last_seen.get(composition, 0),
        })
    return rows


def _calculate_streak(team_matches):
    if not team_matches:
        return {"type": "none", "count": 0, "label": "Sin racha"}

    current_result = bool(team_matches[0].get("win"))
    count = 0
    for match in team_matches:
        if bool(match.get("win")) == current_result:
            count += 1
        else:
            break

    label = f"{count} victorias" if current_result else f"{count} derrotas"
    return {"type": "win" if current_result else "loss", "count": count, "label": label}


def _empty_summary():
    return {
        "total_matches": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0,
        "current_streak": {"type": "none", "count": 0, "label": "Sin racha"},
        "recent_wins": 0,
        "recent_losses": 0,
        "recent_win_rate": 0,
        "lp_change": 0,
        "avg_duration": "0:00",
        "avg_kda": 0,
        "avg_damage": 0,
        "avg_vision": 0,
        "avg_dragons": 0,
        "avg_barons": 0,
        "last_match": None,
    }


def _format_duration(seconds):
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes}:{remaining_seconds:02d}"


def _num(value):
    try:
        return value if isinstance(value, (int, float)) else int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_team_matches_cache(config, roster):
    """Carga las partidas del equipo desde el caché si es válido."""
    current_team_name = config.get("name")
    current_puuids = {p["puuid"] for p in roster}

    def _validate_cache(cached):
        if not isinstance(cached, dict):
            return False
        cached_team_name = cached.get("team_name")
        cached_players = cached.get("players", [])
        if (cached_team_name != current_team_name or
            len(cached_players) != len(roster) or
            {p["puuid"] for p in cached_players} != current_puuids):
            return False
        return True

    if os.path.exists(TEAM_MATCHES_CACHE_PATH):
        try:
            with open(TEAM_MATCHES_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)

            if _validate_cache(cached):
                import time
                last_updated = cached.get("last_updated", 0)
                if time.time() - last_updated <= 24 * 3600:
                    return cached
                print("[team_service] Caché local expirado")
            else:
                print("[team_service] Caché local desactualizado")
        except Exception as e:
            print(f"[team_service] Error leyendo caché local: {e}")

    # Intentar recuperar el caché desde GitHub si no hay local válido
    remote_cached, _ = read_file_from_github(TEAM_MATCHES_CACHE_PATH, use_raw=False)
    if remote_cached and _validate_cache(remote_cached):
        try:
            with open(TEAM_MATCHES_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(remote_cached, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print("[team_service] Caché descargado desde GitHub")
        except Exception as e:
            print(f"[team_service] Error guardando caché local desde GitHub: {e}")
        return remote_cached

    return None


def _save_team_matches_cache(config, roster, team_matches):
    """Guarda las partidas del equipo en el caché local y en GitHub."""
    import time
    cache_data = {
        "team_name": config.get("name"),
        "players": roster,
        "total_team_matches": len(team_matches),
        "last_updated": time.time(),
        "team_matches": team_matches
    }

    try:
        with open(TEAM_MATCHES_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"[team_service] Caché local guardado con {len(team_matches)} partidas")
    except Exception as e:
        print(f"[team_service] Error guardando caché local: {e}")

    # Persistir el archivo en GitHub cuando sea posible
    if write_file_to_github(TEAM_MATCHES_CACHE_PATH, cache_data, message="Actualización automática de team_matches.json"):
        print("[team_service] team_matches.json persistido en GitHub")
    else:
        print("[team_service] No se pudo persistir team_matches.json en GitHub")


def _build_aggregate_summary(roster):
    """Construye estadísticas agregadas del equipo desde los historiales individuales."""
    total_wins = 0
    total_losses = 0
    total_lp_change = 0
    recent_matches = []
    all_matches = []

    for player in roster:
        puuid = player["puuid"]
        riot_id = player.get("riot_id")
        historial = _get_match_history(puuid, riot_id)
        matches = historial.get("matches", []) if historial else []

        for match in matches:
            if match.get("win"):
                total_wins += 1
            else:
                total_losses += 1
            total_lp_change += match.get("lp_change", 0)
            all_matches.append(match)

    # Ordenar todas las partidas por timestamp descendente
    all_matches.sort(key=lambda m: m.get("game_end_timestamp", 0), reverse=True)
    recent_matches = all_matches[:50]  # Últimas 50 para calcular forma reciente

    recent_wins = sum(1 for m in recent_matches if m.get("win"))
    recent_losses = len(recent_matches) - recent_wins

    total_matches = total_wins + total_losses
    win_rate = total_wins / total_matches * 100 if total_matches > 0 else 0

    # Calcular racha actual (simplificada)
    current_streak = {"type": "none", "count": 0, "label": "Sin racha"}
    if all_matches:
        streak_type = "win" if all_matches[0].get("win") else "loss"
        streak_count = 0
        for match in all_matches:
            if match.get("win") == (streak_type == "win"):
                streak_count += 1
            else:
                break
        current_streak = {
            "type": streak_type,
            "count": streak_count,
            "label": f"{streak_count}W" if streak_type == "win" else f"{streak_count}L"
        }

    return {
        "total_matches": total_matches,
        "wins": total_wins,
        "losses": total_losses,
        "win_rate": win_rate,
        "current_streak": current_streak,
        "recent_wins": recent_wins,
        "recent_losses": recent_losses,
        "recent_win_rate": recent_wins / (recent_wins + recent_losses) * 100 if (recent_wins + recent_losses) > 0 else 0,
        "lp_change": total_lp_change,
        "avg_duration": "N/A",  # No calculable fácilmente
        "avg_kda": 0,  # No calculable fácilmente
        "avg_damage": 0,
        "avg_vision": 0,
        "avg_dragons": 0,
        "avg_barons": 0,
        "last_match": all_matches[0] if all_matches else None,
    }
