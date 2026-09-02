from flask import Blueprint, abort, render_template, request, Response
from datetime import datetime, timezone, timedelta
import time
import threading
import config.settings as settings

from config.settings import TARGET_TIMEZONE, ACTIVE_SPLIT_KEY, SPLITS, INDEX_JSON_MAX_AGE
from services.cache_service import (
    player_cache,
    player_stats_cache,
    page_data_cache,
)

from services.github_service import read_peak_elo, save_peak_elo, read_lp_history
from services.github_service import (
    read_player_permission,
    save_player_permission,
    get_permission_file_path,
    ensure_permission_files_for_players,
    read_stats_reload_config,
    save_stats_reload_config,
)
from services.stats_service import get_top_champions_for_player
from services.match_service import get_player_match_history, calculate_streaks
from services.riot_api import esta_en_partida, obtener_nombre_campeon, RIOT_API_KEY
from services.live_game_service import get_active_live_games, get_live_game_by_id
from services.player_service import get_all_players_with_puuids
from utils.helpers import calcular_valor_clasificacion

# Importar el generador de JSON para el index
from services.index_json_generator import (
    load_index_json, 
    generate_index_json, 
    is_json_fresh,
    INDEX_JSON_PATH
)
from services.precompute_service import read_fresh as pre_read_fresh, write_async as pre_write_async, write_all_async as pre_write_all_async, write_github as pre_write_github






main_bp = Blueprint('main', __name__)


def _get_peak_elo_key(jugador):
    """Genera la clave para peak elo basada en jugador."""
    return f"{ACTIVE_SPLIT_KEY}|{jugador['queue_type']}|{jugador['puuid']}"


def _actualizar_stats_en_background(datos_jugadores, lp_history):
    """Actualiza estadísticas en background sin bloquear la página."""
    try:
        print("[background] Iniciando actualización de estadísticas...")
        for jugador in datos_jugadores:
            try:
                puuid = jugador.get('puuid')
                queue_type = jugador.get('queue_type')
                queue_id = 420 if queue_type == 'RANKED_SOLO_5x5' else 440 if queue_type == 'RANKED_FLEX_SR' else None
                
                if not puuid or not queue_type:
                    continue
                
                # Verificar si el jugador está en partida (solo en background)
                if RIOT_API_KEY:
                    live_game_key = f"live_{puuid}"
                    now = time.time()
                    live_cached = getattr(player_stats_cache, '_live_game_cache', {}).get(live_game_key, {})
                    
                    if not live_cached or (now - live_cached.get('timestamp', 0)) >= 60:
                        # Caché expirado o no existe, actualizar
                        from services.riot_api import esta_en_partida, obtener_nombre_campeon
                        game_data = esta_en_partida(RIOT_API_KEY, puuid)
                        if game_data:
                            champion_name = None
                            for participant in game_data.get("participants", []):
                                if participant.get("puuid") == puuid:
                                    champion_id = participant.get("championId")
                                    champion_name = obtener_nombre_campeon(champion_id)
                                    break
                            if not hasattr(player_stats_cache, '_live_game_cache'):
                                player_stats_cache._live_game_cache = {}
                            player_stats_cache._live_game_cache[live_game_key] = {
                                'en_partida': True,
                                'nombre_campeon': champion_name,
                                'timestamp': now
                            }
                        else:
                            if not hasattr(player_stats_cache, '_live_game_cache'):
                                player_stats_cache._live_game_cache = {}
                            player_stats_cache._live_game_cache[live_game_key] = {
                                'en_partida': False,
                                'nombre_campeon': None,
                                'timestamp': now
                            }
                
                # Calcular estadísticas si no están en caché o están antiguas
                cached_stats = player_stats_cache.get(puuid, queue_type)
                if not cached_stats:
                    match_history = get_player_match_history(puuid, limit=20)
                    matches = match_history.get('matches', [])
                    
                    if queue_id:
                        queue_matches_for_champs = [m for m in matches if m.get('queue_id') == queue_id]
                        top_champions = get_top_champions_for_player(queue_matches_for_champs, limit=3)
                    else:
                        top_champions = get_top_champions_for_player(matches, limit=3)
                    
                    if queue_id:
                        queue_matches = [m for m in matches if m.get('queue_id') == queue_id]
                        streaks = calculate_streaks(queue_matches)
                    else:
                        streaks = {'current_win_streak': 0, 'current_loss_streak': 0}
                    
                    # Calcular LP 24h
                    lp_24h = wins_24h = losses_24h = 0
                    if queue_id:
                        now_utc = datetime.now(timezone.utc)
                        one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
                        recent_matches = [
                            m for m in matches 
                            if m.get('queue_id') == queue_id and m.get('game_end_timestamp', 0) > one_day_ago
                        ]
                        for m in recent_matches:
                            lp_change = m.get('lp_change_this_game')
                            if lp_change is not None:
                                lp_24h += lp_change
                            if m.get('win'):
                                wins_24h += 1
                            else:
                                losses_24h += 1
                    
                    # Guardar en caché
                    stats_to_cache = {
                        'top_champion_stats': top_champions,
                        'current_win_streak': streaks.get('current_win_streak', 0),
                        'current_loss_streak': streaks.get('current_loss_streak', 0),
                        'lp_change_24h': lp_24h,
                        'wins_24h': wins_24h,
                        'losses_24h': losses_24h,
                        'en_partida': False,
                        'nombre_campeon': None
                    }
                    player_stats_cache.set(puuid, queue_type, stats_to_cache)
                    
            except Exception as e:
                print(f"[background] Error actualizando {jugador.get('jugador', 'unknown')}: {e}")
                continue
        
        print("[background] Actualización de estadísticas completada")
    except Exception as e:
        print(f"[background] Error general: {e}")


@main_bp.route('/')
def index():
    """
    Renderiza la página principal con la lista de jugadores.
    Usa un JSON pre-generado para carga instantánea.
    """
    print("[index] Petición recibida para la página principal.")
    
    # Intentar servir HTML precomputado si existe
    pre_key = 'index'
    try:
        # index: 5 minutes
        content = pre_read_fresh(pre_key, max_age_seconds=300)
        if content:
            return Response(content, mimetype='text/html')
    except Exception:
        pass

    # Intentar cargar desde JSON pre-generado
    json_data = load_index_json()
    
    # Si no existe JSON o está muy antiguo (>10 min), generarlo sincrónicamente
    if json_data is None:
        print("[index] JSON no encontrado, generando sincrónicamente...")
        if generate_index_json(force=True):
            json_data = load_index_json()
        else:
            # Fallback: usar datos del caché básico sin estadísticas
            print("[index] ERROR: No se pudo generar JSON, usando caché básico")
            datos_jugadores, timestamp = player_cache.get()
            return render_template('index.html',
                                   datos_jugadores=datos_jugadores,
                                   active_live_games=get_active_live_games(),
                                   ultima_actualizacion="N/A",
                                   ddragon_version=settings.DDRAGON_VERSION,
                                   split_activo_nombre=SPLITS[ACTIVE_SPLIT_KEY]['name'],
                                   has_player_data=bool(datos_jugadores),
                                   cache_stale=True,
                                   minutos_desde_actualizacion=999)
    
    # Si el JSON existe pero está antiguo (>5 min), iniciar regeneración en background
    elif not is_json_fresh(max_age_seconds=INDEX_JSON_MAX_AGE):
        print("[index] JSON antiguo detectado, iniciando regeneración en background...")
        # Iniciar thread para regenerar sin bloquear
        thread = threading.Thread(target=generate_index_json, daemon=True)
        thread.start()
    
    # Extraer datos del JSON
    datos_jugadores = json_data.get('datos_jugadores', [])
    ultima_actualizacion = json_data.get('ultima_actualizacion', 'N/A')
    minutos_desde_actualizacion = json_data.get('minutos_desde_actualizacion', 0)
    cache_stale = json_data.get('cache_stale', False)
    split_activo_nombre = json_data.get('split_activo_nombre', SPLITS[ACTIVE_SPLIT_KEY]['name'])
    
    print(f"[index] Renderizando index.html con JSON ({len(datos_jugadores)} jugadores, "
          f"actualizado hace {minutos_desde_actualizacion} min)")
    
    # Añadir timestamp de generación y renderizar a string para posible precomputado
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rendered = render_template('index.html', 
                           datos_jugadores=datos_jugadores,
                           active_live_games=get_active_live_games(),
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=settings.DDRAGON_VERSION,
                           split_activo_nombre=split_activo_nombre,
                           has_player_data=bool(datos_jugadores),
                           cache_stale=cache_stale,
                           minutos_desde_actualizacion=minutos_desde_actualizacion,
                           generated_at=generated_at)

    # Guardar el HTML generado en background para próximas peticiones (local + GitHub si está configurado)
    try:
        pre_write_all_async(pre_key, rendered)
    except Exception:
        try:
            pre_write_async(pre_key, rendered)
        except Exception:
            pass

    return Response(rendered, mimetype='text/html')


@main_bp.route('/partida-en-vivo/<game_id>')
def detalle_partida_en_vivo(game_id):
    live_game = get_live_game_by_id(game_id)
    if not live_game:
        abort(404)

    return render_template(
        'partida_en_vivo.html',
        live_game=live_game,
        ddragon_version=settings.DDRAGON_VERSION,
    )


@main_bp.route('/configops', methods=['GET', 'POST'])
def configops():
    """
    Panel privado de operaciones:
    - Permisos de analisis IA por jugador (scope jugador / partida)
    - Config de recarga forzada de estadisticas
    """
    status_message = None
    status_kind = "info"

    try:
        players = get_all_players_with_puuids()
        player_keys = [riot_id for riot_id, _display, _puuid in players if riot_id]
        ensure_permission_files_for_players(player_keys)

        if request.method == 'POST':
            action = (request.form.get('action') or '').strip()

            if action == 'save_permission':
                player_key = (request.form.get('player_key') or '').strip()
                scope = (request.form.get('scope') or 'jugador').strip().lower()
                if scope not in ('jugador', 'partida'):
                    scope = 'jugador'
                permitir = (request.form.get('permitir_llamada') or 'NO').strip().upper()
                permitir = "SI" if permitir == "SI" else "NO"
                razon = (request.form.get('razon') or '').strip()

                if not player_key:
                    status_message = "player_key vacio."
                    status_kind = "danger"
                else:
                    _, permiso_sha, content, _ = read_player_permission(player_key, scope=scope)
                    if not isinstance(content, dict):
                        content = {}
                    content["permitir_llamada"] = permitir
                    content["razon"] = razon or ("Habilitado manualmente desde /configops." if permitir == "SI" else "Deshabilitado manualmente desde /configops.")
                    content["modo_forzado"] = False
                    content["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ok = save_player_permission(player_key, content, sha=permiso_sha, scope=scope)
                    if ok:
                        status_message = f"Permiso actualizado: {player_key} ({scope}) -> {permitir}."
                        status_kind = "success"
                    else:
                        status_message = f"No se pudo guardar permiso para {player_key} ({scope})."
                        status_kind = "danger"

            elif action == 'save_stats_reload':
                forzar = (request.form.get('forzar_recarga') or 'NO').strip().upper()
                forzar = "SI" if forzar == "SI" else "NO"
                razon = (request.form.get('razon_reload') or '').strip()
                _, reload_sha, reload_content = read_stats_reload_config()
                if not isinstance(reload_content, dict):
                    reload_content = {}
                reload_content["forzar_recarga"] = forzar
                reload_content["razon"] = razon or ("Habilitado manualmente desde /configops." if forzar == "SI" else "Deshabilitado manualmente desde /configops.")
                ok = save_stats_reload_config(reload_content, sha=reload_sha)
                if ok:
                    status_message = f"Config stats_reload guardada: {forzar}."
                    status_kind = "success"
                else:
                    status_message = "No se pudo guardar config stats_reload."
                    status_kind = "danger"

        rows = []
        for riot_id, display_name, puuid in players:
            player_key = riot_id
            p_ok, _p_sha, p_content, _ = read_player_permission(player_key, scope='jugador')
            m_ok, _m_sha, m_content, _ = read_player_permission(player_key, scope='partida')
            rows.append({
                "riot_id": riot_id,
                "display_name": display_name,
                "puuid": puuid,
                "player_key": player_key,
                "perm_jugador": "SI" if p_ok else "NO",
                "perm_partida": "SI" if m_ok else "NO",
                "reason_jugador": (p_content or {}).get("razon", ""),
                "reason_partida": (m_content or {}).get("razon", ""),
                "path_jugador": get_permission_file_path(player_key, scope='jugador'),
                "path_partida": get_permission_file_path(player_key, scope='partida'),
            })

        forzar_recarga, _reload_sha, reload_content = read_stats_reload_config()

        return render_template(
            'configops.html',
            players=rows,
            forzar_recarga="SI" if forzar_recarga else "NO",
            razon_reload=(reload_content or {}).get("razon", ""),
            status_message=status_message,
            status_kind=status_kind,
            ddragon_version=settings.DDRAGON_VERSION,
            has_player_data=True,
        )
    except Exception as e:
        print(f"[configops] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500
