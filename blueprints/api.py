from flask import Blueprint, jsonify, request
from app import leer_cuentas, leer_puuids, RIOT_API_KEY, obtener_id_invocador, obtener_elo, obtener_nombre_campeon, DDRAGON_VERSION, calcular_valor_clasificacion, _create_record_dict, _update_record, SEASON_START_TIMESTAMP
import os
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

api_bp = Blueprint('api', __name__)

@api_bp.route('/players_and_accounts', methods=['GET'])
def get_players_and_accounts():
    cuentas = leer_cuentas("https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt")
    puuids = leer_puuids()

    players_data = {}
    for riot_id, jugador_nombre in cuentas:
        if jugador_nombre not in players_data:
            players_data[jugador_nombre] = []
        
        puuid = puuids.get(riot_id)
        players_data[jugador_nombre].append({
            "riot_id": riot_id,
            "puuid": puuid
        })
    
    return jsonify(players_data)

@api_bp.route('/personal_records/<string:puuid>', methods=['GET'])
def get_personal_records(puuid):
    from app import leer_historial_jugador_github # Import here to avoid circular dependency

    historial = leer_historial_jugador_github(puuid)
    matches = historial.get('matches', [])

    # Get optional query parameters
    queue_filter = request.args.get('queue', 'all')
    champion_filter = request.args.get('champion', 'all') # Not strictly needed for personal records as they are per-player, but good for consistency

    # Filter matches by SEASON_START_TIMESTAMP
    filtered_matches = [
        m for m in matches 
        if m.get('game_end_timestamp', 0) / 1000 >= SEASON_START_TIMESTAMP
    ]

    # Apply queue filter if specified
    if queue_filter != 'all':
        filtered_matches = [
            m for m in filtered_matches
            if str(m.get('queue_id')) == queue_filter
        ]

    # Sort matches by game_end_timestamp in descending order (most recent first)
    filtered_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)

    # Initialize records with default values
    def default_record():
        return {
            'value': 0, 'player': 'N/A', 'riot_id': 'N/A', 'match_id': 'N/A', 'kda': 0,
            'game_date': 0, 'game_duration': 0, 'champion_name': 'N/A',
            'champion_id': 'N/A', 'kills': 0, 'deaths': 0, 'assists': 0,
            'achieved_timestamp': 0, 'is_tied_record': False
        }

    personal_records = {
        'longest_game': default_record(),
        'most_kills': default_record(),
        'most_deaths': default_record(),
        'most_assists': default_record(),
        'highest_kda': default_record(),
        'most_cs': default_record(),
        'most_damage_dealt': default_record(),
        'most_gold_earned': default_record(),
        'most_vision_score': default_record(),
        'largest_killing_spree': default_record(),
        'largest_multikill': default_record(),
        'most_time_spent_dead': default_record(), 
        'most_wards_placed': default_record(),
        'most_wards_killed': default_record(),
        'most_turret_kills': default_record(),
        'most_inhibitor_kills': default_record(),
        'most_baron_kills': default_record(),
        'most_dragon_kills': default_record(),
        'most_damage_taken': default_record(),
        'most_total_heal': default_record(),
        'most_damage_shielded_on_teammates': default_record(),
        'most_time_ccing_others': default_record(), 
        'most_objectives_stolen': default_record(),
        'highest_kill_participation': default_record(),
        'most_double_kills': default_record(), 
        'most_triple_kills': default_record(),  
        'most_quadra_kills': default_record(),  
        'most_penta_kills': default_record(),    
    }

    # Calculate personal records
    for match in filtered_matches:
        # Update longest game
        if match.get('game_duration', 0) > personal_records['longest_game']['value']:
            personal_records['longest_game'] = _create_record_dict(match, match['game_duration'], 'longest_game')
        
        # Update most kills
        if match.get('kills', 0) > personal_records['most_kills']['value']:
            personal_records['most_kills'] = _create_record_dict(match, match['kills'], 'most_kills')

        # Update most deaths
        if match.get('deaths', 0) > personal_records['most_deaths']['value']:
            personal_records['most_deaths'] = _create_record_dict(match, match['deaths'], 'most_deaths')

        # Update most assists
        if match.get('assists', 0) > personal_records['most_assists']['value']:
            personal_records['most_assists'] = _create_record_dict(match, match['assists'], 'most_assists')

        # Update highest KDA
        if match.get('kda', 0) > personal_records['highest_kda']['value']:
            personal_records['highest_kda'] = _create_record_dict(match, match['kda'], 'highest_kda')

        # Update most CS
        total_cs = match.get('total_minions_killed', 0) + match.get('neutral_minions_killed', 0)
        if total_cs > personal_records['most_cs']['value']:
            personal_records['most_cs'] = _create_record_dict(match, total_cs, 'most_cs')

        # Update most damage dealt
        if match.get('total_damage_dealt_to_champions', 0) > personal_records['most_damage_dealt']['value']:
            personal_records['most_damage_dealt'] = _create_record_dict(match, match['total_damage_dealt_to_champions'], 'most_damage_dealt')

        # Update most gold earned
        if match.get('gold_earned', 0) > personal_records['most_gold_earned']['value']:
            personal_records['most_gold_earned'] = _create_record_dict(match, match['gold_earned'], 'most_gold_earned')

        # Update most vision score
        if match.get('vision_score', 0) > personal_records['most_vision_score']['value']:
            personal_records['most_vision_score'] = _create_record_dict(match, match['vision_score'], 'most_vision_score')

        # Update largest killing spree
        if match.get('largest_killing_spree', 0) > personal_records['largest_killing_spree']['value']:
            personal_records['largest_killing_spree'] = _create_record_dict(match, match['largest_killing_spree'], 'largest_killing_spree')

        # Update largest multikill
        if match.get('largestMultiKill', 0) > personal_records['largest_multikill']['value']:
            personal_records['largest_multikill'] = _create_record_dict(match, match['largestMultiKill'], 'largest_multikill')

        # Update most time spent dead
        if match.get('total_time_spent_dead', 0) > personal_records['most_time_spent_dead']['value']:
            personal_records['most_time_spent_dead'] = _create_record_dict(match, match['total_time_spent_dead'], 'most_time_spent_dead')

        # Update most wards placed
        if match.get('wards_placed', 0) > personal_records['most_wards_placed']['value']:
            personal_records['most_wards_placed'] = _create_record_dict(match, match['wards_placed'], 'most_wards_placed')

        # Update most wards killed
        if match.get('wards_killed', 0) > personal_records['most_wards_killed']['value']:
            personal_records['most_wards_killed'] = _create_record_dict(match, match['wards_killed'], 'most_wards_killed')

        # Update most turret kills
        if match.get('turret_kills', 0) > personal_records['most_turret_kills']['value']:
            personal_records['most_turret_kills'] = _create_record_dict(match, match['turret_kills'], 'most_turret_kills')

        # Update most inhibitor kills
        if match.get('inhibitor_kills', 0) > personal_records['most_inhibitor_kills']['value']:
            personal_records['most_inhibitor_kills'] = _create_record_dict(match, match['inhibitor_kills'], 'most_inhibitor_kills')

        # Update most baron kills
        if match.get('baron_kills', 0) > personal_records['most_baron_kills']['value']:
            personal_records['most_baron_kills'] = _create_record_dict(match, match['baron_kills'], 'most_baron_kills')

        # Update most dragon kills
        if match.get('dragon_kills', 0) > personal_records['most_dragon_kills']['value']:
            personal_records['most_dragon_kills'] = _create_record_dict(match, match['dragon_kills'], 'most_dragon_kills')

        # Update most damage taken
        if match.get('total_damage_taken', 0) > personal_records['most_damage_taken']['value']:
            personal_records['most_damage_taken'] = _create_record_dict(match, match['total_damage_taken'], 'most_damage_taken')

        # Update most total heal
        if match.get('total_heal', 0) > personal_records['most_total_heal']['value']:
            personal_records['most_total_heal'] = _create_record_dict(match, match['total_heal'], 'most_total_heal')

        # Update most damage shielded on teammates
        if match.get('total_damage_shielded_on_teammates', 0) > personal_records['most_damage_shielded_on_teammates']['value']:
            personal_records['most_damage_shielded_on_teammates'] = _create_record_dict(match, match['total_damage_shielded_on_teammates'], 'most_damage_shielded_on_teammates')

        # Update most time CCing others
        if match.get('time_ccing_others', 0) > personal_records['most_time_ccing_others']['value']:
            personal_records['most_time_ccing_others'] = _create_record_dict(match, match['time_ccing_others'], 'most_time_ccing_others')

        # Update most objectives stolen
        if match.get('objectives_stolen', 0) > personal_records['most_objectives_stolen']['value']:
            personal_records['most_objectives_stolen'] = _create_record_dict(match, match['objectives_stolen'], 'most_objectives_stolen')

        # Update highest kill participation
        if match.get('kill_participation', 0) > personal_records['highest_kill_participation']['value']:
            personal_records['highest_kill_participation'] = _create_record_dict(match, match['kill_participation'], 'highest_kill_participation')

        # Update multikills
        if match.get('doubleKills', 0) > personal_records['most_double_kills']['value']:
            personal_records['most_double_kills'] = _create_record_dict(match, match['doubleKills'], 'most_double_kills')
        if match.get('tripleKills', 0) > personal_records['most_triple_kills']['value']:
            personal_records['most_triple_kills'] = _create_record_dict(match, match['tripleKills'], 'most_triple_kills')
        if match.get('quadraKills', 0) > personal_records['most_quadra_kills']['value']:
            personal_records['most_quadra_kills'] = _create_record_dict(match, match['quadraKills'], 'most_quadra_kills')
        if match.get('pentaKills', 0) > personal_records['most_penta_kills']['value']:
            personal_records['most_penta_kills'] = _create_record_dict(match, match['pentaKills'], 'most_penta_kills')

    # Filter out records that are still default (value 0 and N/A player)
    # and convert to a list of records for easier iteration in Jinja
    display_records = []
    for key, record in personal_records.items():
        if record['value'] > 0 or (record['value'] == 0 and record['player'] != 'N/A'): # Include 0-value records if player is set
            record['record_type_key'] = key # Add the key for display purposes
            display_records.append(record)
    
    # Sort records by their value (descending) for display, or by key if values are same
    display_records.sort(key=lambda x: (x['value'], x['record_type_key']), reverse=True)

    return jsonify(display_records)
