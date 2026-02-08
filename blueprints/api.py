from flask import Blueprint, jsonify, request
from config.settings import DDRAGON_VERSION, SEASON_START_TIMESTAMP
from services.player_service import get_all_accounts, get_all_puuids, get_player_display_name, get_riot_id_for_puuid
from services.match_service import get_player_match_history
from services.stats_service import calculate_personal_records
from services.cache_service import global_stats_cache
from services.ai_service import check_player_permission, analyze_matches, block_player_permission
from services.riot_api import ALL_CHAMPIONS
import time

api_bp = Blueprint('api', __name__)


@api_bp.route('/players_and_accounts', methods=['GET'])
def get_players_and_accounts():
    """Obtiene todos los jugadores y sus cuentas."""
    try:
        cuentas = get_all_accounts()
        puuids = get_all_puuids()

        players_data = {}
        for riot_id, jugador_nombre in cuentas:
            if jugador_nombre not in players_data:
                players_data[jugador_nombre] = []
            
            puuid = puuids.get(riot_id)
            if puuid:  # Solo incluir si tiene PUUID
                players_data[jugador_nombre].append({
                    "riot_id": riot_id,
                    "puuid": puuid
                })
        
        return jsonify(players_data)
    except Exception as e:
        print(f"[get_players_and_accounts] Error: {e}")
        return jsonify({"error": "Error al obtener jugadores"}), 500


@api_bp.route('/personal_records/<string:puuid>', methods=['GET'])
def get_personal_records(puuid):
    """Obtiene los récords personales de un jugador."""
    try:
        # Obtener info del jugador
        riot_id = get_riot_id_for_puuid(puuid) or 'N/A'
        player_name = get_player_display_name(riot_id) if riot_id != 'N/A' else 'Desconocido'
        
        # Parámetros opcionales
        champion_filter = request.args.get('champion')
        if champion_filter == 'all':
            champion_filter = None
            
        queue_filter = request.args.get('queue')
        if queue_filter == 'all':
            queue_filter = None

        # Obtener historial de partidas
        historial = get_player_match_history(puuid, riot_id=riot_id, limit=-1)
        matches = historial.get('matches', [])
        
        # Aplicar filtro de cola si está presente
        if queue_filter:
            try:
                queue_id = int(queue_filter)
                matches = [m for m in matches if m.get('queue_id') == queue_id]
            except (ValueError, TypeError):
                pass

        # Calcular récords
        records = calculate_personal_records(
            puuid, matches, player_name, riot_id, 
            champion_filter=champion_filter
        )


        # Convertir a lista para JSON
        display_records = []
        for key, record in records.items():
            if record and isinstance(record, dict):
                if record.get('player') == 'N/A':
                    record['player'] = player_name
                if record.get('riot_id') == 'N/A':
                    record['riot_id'] = riot_id
                
                record['record_type_key'] = key
                
                if record.get('value') is not None and record.get('value') != 0:
                    display_records.append(record)
        
        # Ordenar por valor descendente
        display_records.sort(key=lambda x: (x.get('value', 0) or 0), reverse=True)

        return jsonify(display_records)
    except Exception as e:
        print(f"[get_personal_records] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error al obtener récords"}), 500


@api_bp.route('/player/<puuid>/champions', methods=['GET'])
def get_player_champions(puuid):
    """Obtiene la lista de campeones jugados por un jugador."""
    try:
        if not puuid:
            return jsonify({"error": "PUUID no proporcionado"}), 400

        # Obtener campeones jugados
        historial = get_player_match_history(puuid, limit=-1)
        matches = historial.get('matches', [])
        played_champions = set(m.get('champion_name') for m in matches if m.get('champion_name'))
        
        # Construir lista con todos los campeones
        champions_list = [
            {
                'id': champ_id,
                'name': champ_name,
                'played': champ_name in played_champions
            }
            for champ_id, champ_name in ALL_CHAMPIONS.items()
        ]
        
        # Ordenar: primero jugados, luego alfabético
        champions_list.sort(key=lambda x: (not x['played'], x['name']))
        
        return jsonify(champions_list)
    except Exception as e:
        print(f"[get_player_champions] Error: {e}")
        return jsonify({"error": "Error al obtener campeones"}), 500


@api_bp.route('/global_stats', methods=['GET'])
def get_global_stats():
    """Obtiene las estadísticas globales cacheadas."""
    try:
        stats = global_stats_cache.get()
        return jsonify({
            "data": stats.get('data'),
            "timestamp": stats.get('timestamp'),
            "is_stale": global_stats_cache.is_stale()
        })
    except Exception as e:
        print(f"[get_global_stats] Error: {e}")
        return jsonify({"error": "Error al obtener estadísticas"}), 500


@api_bp.route('/analisis-ia/<puuid>', methods=['GET'])
def analizar_partidas(puuid):
    """Endpoint para análisis de partidas con Gemini AI."""
    try:
        # Verificar permiso
        tiene_permiso, permiso_sha, _ = check_player_permission(puuid)
        
        # Obtener partidas de SoloQ
        historial = get_player_match_history(puuid, limit=20)
        matches_soloq = [
            m for m in historial.get('matches', []) 
            if m.get('queue_id') == 420
        ]
        matches_soloq.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
        matches_soloq = matches_soloq[:10]  # Últimas 10
        
        if not matches_soloq:
            return jsonify({"error": "No hay partidas de SoloQ para analizar"}), 404
        
        # Generar firma
        current_signature = "-".join(sorted([str(m.get('match_id')) for m in matches_soloq]))
        
        # Verificar análisis previo
        from services.github_service import read_analysis
        prev_analysis, analysis_sha = read_analysis(puuid)
        
        if prev_analysis:
            prev_signature = prev_analysis.get('signature', '')
            timestamp_analisis = prev_analysis.get('timestamp', 0)
            horas_antiguo = (time.time() - timestamp_analisis) / 3600
            
            # Si es el mismo análisis, devolverlo
            if prev_signature == current_signature:
                result = prev_analysis['data']
                result['_metadata'] = {
                    'generated_at': time.strftime('%d/%m/%Y %H:%M', time.localtime(timestamp_analisis)),
                    'is_outdated': horas_antiguo > 24,
                    'hours_old': round(horas_antiguo, 1)
                }
                return jsonify({"origen": "github", **result})
            
            # Si no tiene permiso y análisis reciente, aplicar cooldown
            if not tiene_permiso and horas_antiguo < 24:
                horas_espera = int(24 - horas_antiguo)
                result = prev_analysis['data']
                result['_metadata'] = {
                    'generated_at': time.strftime('%d/%m/%Y %H:%M', time.localtime(timestamp_analisis)),
                    'is_outdated': True,
                    'hours_old': round(horas_antiguo, 1)
                }
                return jsonify({
                    "error": "Cooldown",
                    "mensaje": f"Espera {horas_espera}h más o pide rehabilitación manual.",
                    "analisis_previo": result
                }), 429
        
        # Si no tiene permiso y no hay análisis previo
        if not tiene_permiso:
            if prev_analysis:
                # Devolver análisis anterior
                result = prev_analysis['data']
                result['_metadata'] = {
                    'generated_at': time.strftime('%d/%m/%Y %H:%M', time.localtime(prev_analysis.get('timestamp', 0))),
                    'is_outdated': True
                }
                return jsonify({"origen": "github_antiguo", **result}), 200
            else:
                return jsonify({
                    "error": "Bloqueado",
                    "mensaje": "No tienes permiso activo y no hay análisis anterior disponible."
                }), 403
        
        # Tiene permiso, generar nuevo análisis
        riot_id = get_riot_id_for_puuid(puuid) or puuid
        player_name = get_player_display_name(riot_id) if riot_id != puuid else None
        
        result = analyze_matches(puuid, matches_soloq, player_name)
        
        # Bloquear permiso después de usar
        block_player_permission(puuid, permiso_sha)
        
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]
        
        return jsonify({"origen": "nuevo", **result}), 200
        
    except Exception as e:
        print(f"[analizar_partidas] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error en el servidor", "detalle": str(e)}), 500
