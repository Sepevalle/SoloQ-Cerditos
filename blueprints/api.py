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
    from app import (
        leer_historial_jugador_github, _get_player_personal_records,
        leer_cuentas, leer_puuids
    ) # Import here to avoid circular dependency

    # Get player info from cuentas to populate jugador_nombre and riot_id
    cuentas = leer_cuentas("https://raw.githubusercontent.com/Sepevalle/SoloQ-Cerditos/main/cuentas.txt")
    puuids_mapping = leer_puuids()
    
    # Find the riot_id for this puuid
    player_display_name = 'N/A'
    riot_id = 'N/A'
    
    for riot_id_candidate, puuid_candidate in puuids_mapping.items():
        if puuid_candidate == puuid:
            riot_id = riot_id_candidate
            # Find the display name from cuentas
            for riot_id_cuenta, display_name in cuentas:
                if riot_id_cuenta == riot_id:
                    player_display_name = display_name
                    break
            break

    # Get optional query parameters
    queue_filter = request.args.get('queue', 'all')
    champion_filter = request.args.get('champion', 'all')

    # Use the app's _get_player_personal_records function with champion filter
    try:
        personal_records_dict = _get_player_personal_records(
            puuid, player_display_name, riot_id, champion_filter=(champion_filter if champion_filter != 'all' else None)
        )
    except Exception as e:
        print(f"[get_personal_records] Error calling _get_player_personal_records: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])

    # Convert the records dictionary to a list for JSON response
    display_records = []
    for key, record in personal_records_dict.items():
        if record and isinstance(record, dict):  # Only process valid records
            # Ensure player and riot_id are set
            if 'player' not in record or record['player'] == 'N/A':
                record['player'] = player_display_name
            if 'riot_id' not in record or record['riot_id'] == 'N/A':
                record['riot_id'] = riot_id
            
            record['record_type_key'] = key
            
            # Only include records that have meaningful values
            # (non-zero value OR has player info)
            if record.get('value') is not None and record.get('value') != 0:
                display_records.append(record)
    
    # Sort records by their value (descending) for display
    display_records.sort(key=lambda x: (x.get('value', 0) or 0), reverse=True)

    return jsonify(display_records)
