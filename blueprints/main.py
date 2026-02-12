from flask import Blueprint, render_template
from datetime import datetime, timezone, timedelta
import time
import threading
import config.settings as settings

from config.settings import TARGET_TIMEZONE, ACTIVE_SPLIT_KEY, SPLITS
from services.cache_service import player_cache, player_stats_cache


from services.github_service import read_peak_elo, save_peak_elo, read_lp_history
from services.stats_service import get_top_champions_for_player
from services.match_service import get_player_match_history, calculate_streaks
from services.riot_api import esta_en_partida, obtener_nombre_campeon, RIOT_API_KEY
from utils.helpers import calcular_valor_clasificacion





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
    """Renderiza la página principal con la lista de jugadores (Stale-While-Revalidate)."""
    print("[index] Petición recibida para la página principal.")
    datos_jugadores, timestamp = player_cache.get()
    
    # Verificar si el caché está antiguo
    cache_stale = player_cache.is_stale()
    stats_cache_stale = False
    
    # Verificar si las estadísticas individuales están antiguas
    for jugador in datos_jugadores:
        puuid = jugador.get('puuid')
        queue_type = jugador.get('queue_type')
        if puuid and queue_type:
            if not player_stats_cache.get(puuid, queue_type):
                stats_cache_stale = True
                break
    
    # Si algún caché está antiguo, iniciar actualización en background
    if cache_stale or stats_cache_stale:
        print(f"[index] Caché antiguo detectado (player: {cache_stale}, stats: {stats_cache_stale}), actualizando en background...")
        # Crear copia de datos para el thread
        import copy
        datos_copy = copy.deepcopy(datos_jugadores)
        _, lp_history = read_lp_history()
        thread = threading.Thread(
            target=_actualizar_stats_en_background,
            args=(datos_copy, lp_history),
            daemon=True
        )
        thread.start()
    
    lectura_exitosa, peak_elo_dict = read_peak_elo()


    if lectura_exitosa:
        actualizado = False
        for jugador in datos_jugadores:
            key = _get_peak_elo_key(jugador)
            peak = peak_elo_dict.get(key, 0)

            valor = jugador["valor_clasificacion"]
            if valor > peak:
                peak_elo_dict[key] = valor
                peak = valor
                actualizado = True
                print(f"[index] Peak Elo actualizado para {jugador['game_name']} en {jugador['queue_type']}: {peak}")
            jugador["peak_elo"] = peak

        if actualizado:
            save_peak_elo(peak_elo_dict)
    else:
        print("[index] ADVERTENCIA: No se pudo leer el archivo peak_elo.json. Se omitirá la actualización de picos.")
        for jugador in datos_jugadores:
            jugador["peak_elo"] = jugador["valor_clasificacion"]

    # Leer historial de LP para calcular cambios
    _, lp_history = read_lp_history()
    
    # USAR CACHÉ INMEDIATAMENTE (Stale-While-Revalidate)
    # Si hay caché: usarlo inmediatamente (incluso si está antiguo)
    # Si NO hay caché: calcular sincrónicamente (primera carga tras reinicio)
    for jugador in datos_jugadores:
        try:
            puuid = jugador.get('puuid')
            queue_type = jugador.get('queue_type')
            queue_id = 420 if queue_type == 'RANKED_SOLO_5x5' else 440 if queue_type == 'RANKED_FLEX_SR' else None
            
            # Intentar obtener estadísticas del caché PRIMERO
            cached_stats = player_stats_cache.get(puuid, queue_type) if puuid and queue_type else None
            
            if cached_stats:
                # HAY caché: usarlo inmediatamente (Stale-While-Revalidate)
                # El background thread actualizará para la próxima vez
                jugador['top_champion_stats'] = cached_stats.get('top_champion_stats', [])
                jugador['current_win_streak'] = cached_stats.get('current_win_streak', 0)
                jugador['current_loss_streak'] = cached_stats.get('current_loss_streak', 0)
                jugador['lp_change_24h'] = cached_stats.get('lp_change_24h', 0)
                jugador['wins_24h'] = cached_stats.get('wins_24h', 0)
                jugador['losses_24h'] = cached_stats.get('losses_24h', 0)
                jugador['en_partida'] = cached_stats.get('en_partida', False)
                jugador['nombre_campeon'] = cached_stats.get('nombre_campeon', None)
            else:
                # NO hay caché: calcular sincrónicamente (primera carga)
                print(f"[index] Sin caché para {jugador.get('jugador', 'unknown')}, calculando sincrónicamente...")
                if puuid:
                    # Obtener historial de partidas
                    match_history = get_player_match_history(puuid, limit=20)
                    matches = match_history.get('matches', [])
                    
                    # Calcular top campeones
                    if queue_id:
                        queue_matches_for_champs = [m for m in matches if m.get('queue_id') == queue_id]
                        top_champions = get_top_champions_for_player(queue_matches_for_champs, limit=3)
                    else:
                        top_champions = get_top_champions_for_player(matches, limit=3)
                    jugador['top_champion_stats'] = top_champions
                    
                    # Calcular rachas
                    if queue_id:
                        queue_matches = [m for m in matches if m.get('queue_id') == queue_id]
                        streaks = calculate_streaks(queue_matches)
                        jugador['current_win_streak'] = streaks.get('current_win_streak', 0)
                        jugador['current_loss_streak'] = streaks.get('current_loss_streak', 0)
                    else:
                        jugador['current_win_streak'] = 0
                        jugador['current_loss_streak'] = 0
                    
                    # Calcular LP 24h
                    if queue_id:
                        now_utc = datetime.now(timezone.utc)
                        one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
                        lp_24h = wins_24h = losses_24h = 0
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
                        jugador['lp_change_24h'] = lp_24h
                        jugador['wins_24h'] = wins_24h
                        jugador['losses_24h'] = losses_24h
                    else:
                        jugador['lp_change_24h'] = 0
                        jugador['wins_24h'] = 0
                        jugador['losses_24h'] = 0
                    
                    # Verificar si está en partida (con caché de 60s)
                    try:
                        if RIOT_API_KEY:
                            live_game_key = f"live_{puuid}"
                            now = time.time()
                            game_data = esta_en_partida(RIOT_API_KEY, puuid)
                            if game_data:
                                jugador['en_partida'] = True
                                champion_name = None
                                for participant in game_data.get("participants", []):
                                    if participant.get("puuid") == puuid:
                                        champion_id = participant.get("championId")
                                        champion_name = obtener_nombre_campeon(champion_id)
                                        jugador['nombre_campeon'] = champion_name
                                        break
                                # Guardar en caché de partida
                                if not hasattr(player_stats_cache, '_live_game_cache'):
                                    player_stats_cache._live_game_cache = {}
                                player_stats_cache._live_game_cache[live_game_key] = {
                                    'en_partida': True,
                                    'nombre_campeon': champion_name,
                                    'timestamp': now
                                }
                            else:
                                jugador['en_partida'] = False
                                jugador['nombre_campeon'] = None
                                if not hasattr(player_stats_cache, '_live_game_cache'):
                                    player_stats_cache._live_game_cache = {}
                                player_stats_cache._live_game_cache[live_game_key] = {
                                    'en_partida': False,
                                    'nombre_campeon': None,
                                    'timestamp': now
                                }
                        else:
                            jugador['en_partida'] = False
                            jugador['nombre_campeon'] = None
                    except Exception as e:
                        print(f"[index] Error verificando partida: {e}")
                        jugador['en_partida'] = False
                        jugador['nombre_campeon'] = None
                    
                    # Guardar en caché para próximas veces
                    stats_to_cache = {
                        'top_champion_stats': jugador['top_champion_stats'],
                        'current_win_streak': jugador['current_win_streak'],
                        'current_loss_streak': jugador['current_loss_streak'],
                        'lp_change_24h': jugador['lp_change_24h'],
                        'wins_24h': jugador['wins_24h'],
                        'losses_24h': jugador['losses_24h'],
                        'en_partida': jugador['en_partida'],
                        'nombre_campeon': jugador['nombre_campeon']
                    }
                    player_stats_cache.set(puuid, queue_type, stats_to_cache)
                    # Agregar timestamp de estadísticas para debugging
                    jugador['stats_timestamp'] = time.time()
                else:
                    # No hay PUUID
                    jugador['top_champion_stats'] = []
                    jugador['current_win_streak'] = 0
                    jugador['current_loss_streak'] = 0
                    jugador['lp_change_24h'] = 0
                    jugador['wins_24h'] = 0
                    jugador['losses_24h'] = 0
                    jugador['en_partida'] = False
                    jugador['nombre_campeon'] = None
                
        except Exception as e:
            print(f"[index] Error calculando estadísticas para {jugador.get('jugador', 'unknown')}: {e}")
            jugador['top_champion_stats'] = []
            jugador['current_win_streak'] = 0
            jugador['current_loss_streak'] = 0
            jugador['lp_change_24h'] = 0
            jugador['wins_24h'] = 0
            jugador['losses_24h'] = 0
            jugador['en_partida'] = False
            jugador['nombre_campeon'] = None







    # El timestamp de la caché está en segundos UTC (de time.time())
    dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # Convertir a la zona horaria de visualización deseada (UTC+2)
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    ultima_actualizacion = dt_target.strftime("%d/%m/%Y %H:%M:%S")
    
    split_activo_nombre = SPLITS[ACTIVE_SPLIT_KEY]['name']
    
    # Calcular minutos desde última actualización
    minutos_desde_actualizacion = int((time.time() - timestamp) / 60) if timestamp else 0
    
    print("[index] Renderizando index.html.")
    return render_template('index.html', 
                           datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=settings.DDRAGON_VERSION,
                           split_activo_nombre=split_activo_nombre,
                           has_player_data=bool(datos_jugadores),
                           cache_stale=cache_stale or stats_cache_stale,
                           minutos_desde_actualizacion=minutos_desde_actualizacion)



@main_bp.route('/historial_global')
def historial_global():
    """Renderiza la página de historial global de partidas."""
    print("[historial_global] Petición recibida.")
    
    from services.player_service import get_all_accounts, get_all_puuids
    from services.match_service import get_player_match_history
    
    try:
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
        
        # Ordenar por fecha descendente
        all_matches.sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
        
        return render_template('historial_global.html',
                             matches=all_matches,
                             ddragon_version=settings.DDRAGON_VERSION)
    except Exception as e:
        print(f"[historial_global] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500


@main_bp.route('/records_personales')
def records_personales():
    """Renderiza la página de récords personales."""
    print("[records_personales] Petición recibida.")
    
    from services.player_service import get_all_accounts, get_all_puuids, get_player_display_name
    from services.match_service import get_player_match_history
    from services.stats_service import calculate_personal_records
    
    try:
        cuentas = get_all_accounts()
        puuids = get_all_puuids()
        
        all_records = []
        
        for riot_id, jugador_nombre in cuentas:
            puuid = puuids.get(riot_id)
            if not puuid:
                continue
            
            try:
                historial = get_player_match_history(puuid, limit=-1)
                matches = historial.get('matches', [])
                
                records = calculate_personal_records(
                    puuid, matches, jugador_nombre, riot_id
                )
                
                # Convertir a lista
                for key, record in records.items():
                    if record and isinstance(record, dict):
                        if record.get('player') == 'N/A':
                            record['player'] = jugador_nombre
                        if record.get('riot_id') == 'N/A':
                            record['riot_id'] = riot_id
                        record['record_type_key'] = key
                        
                        if record.get('value') is not None and record.get('value') != 0:
                            all_records.append(record)
            except Exception as e:
                print(f"[records_personales] Error procesando {jugador_nombre}: {e}")
                continue
        
        # Ordenar por valor descendente
        all_records.sort(key=lambda x: (x.get('value', 0) or 0), reverse=True)
        
        return render_template('records_personales.html',
                             records=all_records,
                             ddragon_version=settings.DDRAGON_VERSION)
    except Exception as e:
        print(f"[records_personales] Error: {e}")
        import traceback
        traceback.print_exc()
        return render_template('404.html'), 500
