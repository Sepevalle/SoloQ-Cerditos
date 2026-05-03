import time

import config.settings as settings
import services.riot_api as riot_api
from services.cache_service import live_game_cache
from services.player_service import get_all_accounts, get_all_puuids


def _queue_name(queue_id):
    if queue_id is None:
        return "Cola desconocida"
    return settings.QUEUE_NAMES.get(queue_id, f"Cola {queue_id}")


def _duration_seconds(game_data):
    game_length = game_data.get("gameLength")
    if game_length:
        return int(game_length)

    game_start = game_data.get("gameStartTime")
    if game_start:
        return max(0, int(time.time() - (game_start / 1000)))

    return 0


def _known_players_by_puuid():
    accounts = get_all_accounts()
    puuids = get_all_puuids()
    known = {}
    for riot_id, player_name in accounts:
        puuid = puuids.get(riot_id)
        if puuid:
            known[puuid] = {
                "player_name": player_name,
                "riot_id": riot_id,
            }
    return known


def _champion_name(champion_id):
    return riot_api.obtener_nombre_campeon(champion_id) if champion_id else "Desconocido"


def _spell_name(spell_id):
    return riot_api.ALL_SUMMONER_SPELLS.get(spell_id) if spell_id else None


def _participant_summary(participant, known_players):
    puuid = participant.get("puuid")
    known = known_players.get(puuid, {})
    champion_id = participant.get("championId")
    spell1_id = participant.get("spell1Id")
    spell2_id = participant.get("spell2Id")
    riot_id = participant.get("riotId") or known.get("riot_id") or participant.get("summonerName")

    return {
        "puuid": puuid,
        "team_id": participant.get("teamId"),
        "champion_id": champion_id,
        "champion_name": _champion_name(champion_id),
        "spell1_id": spell1_id,
        "spell2_id": spell2_id,
        "spell1_name": _spell_name(spell1_id),
        "spell2_name": _spell_name(spell2_id),
        "profile_icon_id": participant.get("profileIconId"),
        "bot": participant.get("bot", False),
        "riot_id": riot_id,
        "summoner_name": participant.get("summonerName"),
        "player_name": known.get("player_name"),
        "is_known": bool(known),
        "perks": participant.get("perks") or {},
    }


def _ban_summary(ban):
    champion_id = ban.get("championId")
    return {
        "champion_id": champion_id,
        "champion_name": _champion_name(champion_id) if champion_id and champion_id > 0 else "Sin ban",
        "team_id": ban.get("teamId"),
        "pick_turn": ban.get("pickTurn"),
    }


def summarize_live_game(game_data, cache_age_seconds=None, known_players=None):
    known_players = known_players or _known_players_by_puuid()
    game_id = game_data.get("gameId")
    queue_id = game_data.get("gameQueueConfigId") or game_data.get("queueId")
    participants = [
        _participant_summary(participant, known_players)
        for participant in game_data.get("participants", [])
    ]
    blue_team = [p for p in participants if p.get("team_id") == 100]
    red_team = [p for p in participants if p.get("team_id") == 200]
    known_in_game = [p for p in participants if p.get("is_known")]

    return {
        "game_id": str(game_id) if game_id is not None else None,
        "platform_id": game_data.get("platformId"),
        "game_mode": game_data.get("gameMode", "Unknown"),
        "game_type": game_data.get("gameType", "Unknown"),
        "map_id": game_data.get("mapId"),
        "queue_id": queue_id,
        "queue_name": _queue_name(queue_id),
        "game_start_time": game_data.get("gameStartTime"),
        "duration_seconds": _duration_seconds(game_data),
        "cache_age_seconds": int(cache_age_seconds) if cache_age_seconds is not None else None,
        "participants": participants,
        "blue_team": blue_team,
        "red_team": red_team,
        "known_players": known_in_game,
        "bans": [_ban_summary(ban) for ban in game_data.get("bannedChampions", [])],
    }


def get_active_live_games():
    known_players = _known_players_by_puuid()
    games_by_id = {}

    for entry in live_game_cache.snapshot().values():
        game_data = entry.get("data")
        if not game_data:
            continue

        game_id = game_data.get("gameId")
        if game_id is None:
            continue

        game_key = str(game_id)
        cache_age = entry.get("age_seconds")
        existing = games_by_id.get(game_key)
        if existing and existing.get("cache_age_seconds") is not None:
            cache_age = min(cache_age, existing["cache_age_seconds"])

        games_by_id[game_key] = summarize_live_game(
            game_data,
            cache_age_seconds=cache_age,
            known_players=known_players,
        )

    return sorted(
        games_by_id.values(),
        key=lambda game: (len(game.get("known_players", [])), game.get("duration_seconds", 0)),
        reverse=True,
    )


def get_live_game_by_id(game_id):
    game_id = str(game_id)
    for game in get_active_live_games():
        if game.get("game_id") == game_id:
            return game
    return None
