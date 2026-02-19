from flask import Blueprint, jsonify, request
import config.settings as settings
from config.settings import SEASON_START_TIMESTAMP

from services.player_service import get_all_accounts, get_all_puuids, get_player_display_name, get_riot_id_for_puuid
from services.match_service import get_player_match_history
from services.stats_service import calculate_personal_records
from services.cache_service import global_stats_cache
from services.ai_service import check_player_permission, analyze_matches, block_player_permission
from services.riot_api import ALL_CHAMPIONS, esta_en_partida, obtener_nombre_campeon, RIOT_API_KEY

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

        # Calcular récords (con filtros de campeón y cola)
        records = calculate_personal_records(
            puuid, matches, player_name, riot_id, 
            champion_filter=champion_filter,
            queue_filter=queue_filter
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
        
        # Get unique champion names from matches
        played_champions = set()
        for m in matches:
            champ_name = m.get('champion_name')
            if champ_name and champ_name != 'Desconocido':
                played_champions.add(champ_name)
        
        # Return only played champions, sorted alphabetically
        champions_list = sorted(list(played_champions))
        
        return jsonify(champions_list)
    except Exception as e:
        print(f"[get_player_champions] Error: {e}")
        import traceback
        traceback.print_exc()
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
        
        # Obtener partidas de SoloQ (últimas 5 para análisis)
        historial = get_player_match_history(puuid, limit=20)
        matches_soloq = [
            m for m in historial.get('matches', []) 
            if m.get('queue_id') == 420
        ]
        matches_soloq.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
        matches_soloq = matches_soloq[:5]  # Últimas 5 (consistente con el análisis)
        
        if not matches_soloq:
            return jsonify({"error": "No hay partidas de SoloQ para analizar"}), 404
        
        # Generar firma basada en las 5 partidas que se analizarán
        current_signature = "-".join(sorted([str(m.get('match_id')) for m in matches_soloq]))

        
        # Verificar análisis previo
        from services.github_service import read_analysis
        prev_analysis, analysis_sha = read_analysis(puuid)
        
        if prev_analysis:
            prev_signature = prev_analysis.get('signature', '')
            timestamp_analisis = prev_analysis.get('timestamp', 0)
            horas_antiguo = (time.time() - timestamp_analisis) / 3600
            
            # Si es el mismo análisis, devolverlo con indicación clara de caché
            if prev_signature == current_signature:
                result = prev_analysis['data']
                result['_metadata'] = {
                    'generated_at': time.strftime('%d/%m/%Y %H:%M', time.localtime(timestamp_analisis)),
                    'timestamp': timestamp_analisis,
                    'is_outdated': horas_antiguo > 24,
                    'hours_old': round(horas_antiguo, 1),
                    'origen': 'cache',
                    'button_label': f"Análisis en caché ({round(horas_antiguo, 1)}h)"
                }
                return jsonify({
                    "origen": "cache",
                    "mensaje": "Análisis recuperado de caché (mismas partidas)",
                    **result
                })

            
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
            error_result = result[0]
            error_result['_metadata'] = {
                'origen': 'error',
                'button_label': 'Error - Reintentar'
            }
            return jsonify(error_result), result[1]
        
        # Añadir metadata de nuevo análisis
        result['_metadata'] = {
            'generated_at': time.strftime('%d/%m/%Y %H:%M', time.localtime(time.time())),
            'timestamp': time.time(),
            'is_outdated': False,
            'hours_old': 0,
            'origen': 'nuevo',
            'button_label': '✨ Nuevo análisis generado'
        }
        
        return jsonify({
            "origen": "nuevo",
            "mensaje": "Análisis generado con Coach IA Gemini",
            **result
        }), 200

        
    except Exception as e:
        print(f"[analizar_partidas] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error en el servidor", "detalle": str(e)}), 500


@api_bp.route('/player/<puuid>/live-game', methods=['GET'])
def check_live_game(puuid):
    """
    Verifica si un jugador está en partida activa en tiempo real.
    Retorna el estado actual sin depender del caché del JSON.
    """
    try:
        if not puuid:
            print("[check_live_game] ERROR: PUUID no proporcionado")
            return jsonify({"error": "PUUID no proporcionado"}), 400
            
        if not RIOT_API_KEY:
            print("[check_live_game] ERROR: API key no configurada")
            return jsonify({"error": "API key no configurada"}), 500
        
        print(f"[check_live_game] Verificando partida activa para PUUID: {puuid[:8]}...")
        game_data = esta_en_partida(RIOT_API_KEY, puuid)
        
        if game_data:
            # Buscar el campeón del jugador
            champion_name = None
            champion_id = None
            for participant in game_data.get("participants", []):
                if participant.get("puuid") == puuid:
                    champion_id = participant.get("championId")
                    champion_name = obtener_nombre_campeon(champion_id)
                    break
            
            print(f"[check_live_game] ✓ Jugador {puuid[:8]}... EN PARTIDA con {champion_name} (ID: {champion_id})")
            
            return jsonify({
                "en_partida": True,
                "nombre_campeon": champion_name,
                "game_mode": game_data.get("gameMode", "Unknown"),
                "game_type": game_data.get("gameType", "Unknown"),
                "map_id": game_data.get("mapId", 0)
            })
        else:
            print(f"[check_live_game] ✗ Jugador {puuid[:8]}... NO está en partida")
            return jsonify({
                "en_partida": False,
                "nombre_campeon": None
            })
            
    except Exception as e:
        print(f"[check_live_game] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error al verificar estado de partida"}), 500


@api_bp.route('/live-games/all', methods=['GET'])
def get_all_live_games():
    """
    Obtiene el estado de partida de todos los jugadores SOLO desde el caché.
    NO hace llamadas a la API de Riot - solo lee lo que el worker ha cacheado.
    Retorna un diccionario con puuid como clave y datos de partida como valor.
    """
    try:
        from services.cache_service import live_game_cache
        from services.player_service import get_all_accounts, get_all_puuids
        
        cuentas = get_all_accounts()
        puuids = get_all_puuids()
        
        result = {}
        en_partida_count = 0
        inactivo_count = 0
        no_cache_count = 0
        stale_cache_count = 0  # Caché existe pero tiene más de 150 segundos (2.5 min)
        
        print(f"[get_all_live_games] Procesando {len(cuentas)} cuentas...")
        
        for riot_id, jugador_nombre in cuentas:
            puuid = puuids.get(riot_id)
            if not puuid:
                print(f"[get_all_live_games] ⚠ {jugador_nombre}: No tiene PUUID")
                continue
            
            # Obtener datos del caché con información de edad
            game_data, has_cache, cache_age = live_game_cache.get_with_status(puuid)
            
            if game_data:
                # Verificar si el caché es "viejo" (más de 150 segundos = 2.5 min) pero aún válido
                # El worker verifica cada 2 min, así que 2.5 min da margen de seguridad
                is_stale = cache_age and cache_age > 150

                
                # Buscar el campeón del jugador
                champion_name = None
                for participant in game_data.get("participants", []):
                    if participant.get("puuid") == puuid:
                        champion_id = participant.get("championId")
                        champion_name = obtener_nombre_campeon(champion_id)
                        break
                
                result[puuid] = {
                    "en_partida": True,
                    "nombre_campeon": champion_name,
                    "game_mode": game_data.get("gameMode", "Unknown"),
                    "game_type": game_data.get("gameType", "Unknown"),
                    "cache_age_seconds": int(cache_age) if cache_age else None
                }
                en_partida_count += 1
                
                if is_stale:
                    stale_cache_count += 1
                    print(f"[get_all_live_games] ⚠ {jugador_nombre}: EN PARTIDA con {champion_name} (caché viejo: {int(cache_age)}s)")
                else:
                    print(f"[get_all_live_games] ✓ {jugador_nombre}: EN PARTIDA con {champion_name} (caché fresco: {int(cache_age)}s)")
                    
            elif has_cache and game_data is None:
                # Hay entrada en caché pero el jugador está inactivo
                inactivo_count += 1
                is_stale = cache_age and cache_age > 150

                
                result[puuid] = {
                    "en_partida": False,
                    "nombre_campeon": None,
                    "cache_age_seconds": int(cache_age) if cache_age else None
                }
                
                if is_stale:
                    stale_cache_count += 1
                    print(f"[get_all_live_games] ⚠ {jugador_nombre}: Inactivo (caché viejo: {int(cache_age)}s)")
                else:
                    print(f"[get_all_live_games] ✓ {jugador_nombre}: Inactivo (caché fresco: {int(cache_age)}s)")
            else:
                # No hay datos en caché - el worker no ha verificado aún
                no_cache_count += 1
                result[puuid] = {
                    "en_partida": False,
                    "nombre_campeon": None,
                    "cache_age_seconds": None
                }
                print(f"[get_all_live_games] ? {jugador_nombre}: Sin datos en caché")
        
        print(f"[get_all_live_games] Resumen: {en_partida_count} en partida, {inactivo_count} inactivos, {no_cache_count} sin caché, {stale_cache_count} caché viejo")
        return jsonify(result)
        
    except Exception as e:
        print(f"[get_all_live_games] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Error al obtener estados de partida"}), 500







@api_bp.route('/update-global-stats', methods=['POST'])
def request_global_stats_update():

    """
    Endpoint para disparar cálculo manual de estadísticas globales.
    Bloquea peticiones concurrentes para evitar saturación del servidor.
    """
    from services.cache_service import global_stats_cache
    from services.stats_service import calculate_global_stats
    from services.player_service import get_all_accounts, get_all_puuids
    from services.match_service import get_player_match_history
    
    try:
        # Si ya se está calculando, rechazar la petición
        if global_stats_cache.is_calculating():
            return jsonify({
                "status": "already_calculating",
                "message": "El cálculo de estadísticas globales ya está en progreso. Espera a que termine."
            }), 429  # Too Many Requests
        
        # Marcar como calculando
        global_stats_cache.set_calculating(True)
        print("[request_global_stats_update] Iniciando cálculo manual de estadísticas globales...")
        
        try:
            # Compilar todas las partidas
            cuentas = get_all_accounts()
            puuids = get_all_puuids()
            
            all_matches = []
            for riot_id, jugador_nombre in cuentas:
                puuid = puuids.get(riot_id)
                if not puuid:
                    continue
                
                historial = get_player_match_history(puuid, limit=-1)
                matches = historial.get('matches', [])
                
                for match in matches:
                    match['jugador_nombre'] = jugador_nombre
                    match['riot_id'] = riot_id
                    all_matches.append(match)
            
            # Calcular estadísticas
            stats = calculate_global_stats(all_matches)
            
            # Guardar en caché
            global_stats_cache.set(stats, all_matches)
            
            return jsonify({
                "status": "success",
                "message": "Estadísticas globales actualizadas correctamente.",
                "timestamp": time.time(),
                "total_matches": len(all_matches)
            }), 200
            
        finally:
            # Siempre desmarcar como calculando al terminar
            global_stats_cache.set_calculating(False)
            print("[request_global_stats_update] Cálculo de estadísticas globales completado.")
    
    except Exception as e:
        global_stats_cache.set_calculating(False)
        print(f"[request_global_stats_update] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Error al calcular estadísticas: {str(e)}"
        }), 500
