from flask import Blueprint, render_template, request
from datetime import datetime, timezone, timedelta
from config.settings import TARGET_TIMEZONE, DDRAGON_VERSION, ACTIVE_SPLIT_KEY
from services.cache_service import player_cache
from services.player_service import get_puuid_for_riot_id, get_player_display_name
from services.match_service import get_player_match_history, calculate_streaks, filter_matches_by_queue
from services.stats_service import calculate_personal_records, get_top_champions_for_player
from services.github_service import read_peak_elo, read_lp_history
from utils.helpers import calcular_valor_clasificacion

player_bp = Blueprint('player', __name__)


def _get_peak_elo_key(queue_type, puuid):
    """Genera la clave para peak elo."""
    return f"{ACTIVE_SPLIT_KEY}|{queue_type}|{puuid}"


def _build_player_profile(game_name):
    """Construye el perfil completo de un jugador."""
    # Obtener datos del caché principal
    all_players, _ = player_cache.get()
    player_entries = [p for p in all_players if p.get('game_name') == game_name]
    
    if not player_entries:
        return None
    
    first_entry = player_entries[0]
    puuid = first_entry.get('puuid')
    display_name = first_entry.get('jugador', game_name)
    
    # Leer peak elo
    _, peak_elo_dict = read_peak_elo()
    for entry in player_entries:
        key = _get_peak_elo_key(entry.get('queue_type'), puuid)
        peak = peak_elo_dict.get(key, 0)
        if entry['valor_clasificacion'] > peak:
            peak = entry['valor_clasificacion']
        entry['peak_elo'] = peak
    
    # Obtener historial de partidas
    _, lp_history = read_lp_history()
    player_lp = lp_history.get(puuid, {})

    
    historial = get_player_match_history(puuid, riot_id=game_name, limit=-1)
    matches = historial.get('matches', [])
    
    # Construir perfil base
    perfil = {
        'nombre': display_name,
        'game_name': game_name,
        'puuid': puuid,
        'perfil_icon_url': first_entry.get('perfil_icon_url', ''),
        'historial_partidas': matches
    }
    
    # Añadir datos de colas
    for entry in player_entries:
        queue_type = entry.get('queue_type')
        if queue_type == 'RANKED_SOLO_5x5':
            perfil['soloq'] = entry
        elif queue_type == 'RANKED_FLEX_SR':
            perfil['flexq'] = entry
    
    # Calcular historial de ELO
    soloq_matches = sorted(
        [m for m in matches if m.get('queue_id') == 420 and m.get('post_game_valor_clasificacion')],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    flexq_matches = sorted(
        [m for m in matches if m.get('queue_id') == 440 and m.get('post_game_valor_clasificacion')],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    
    perfil['elo_history_soloq'] = [
        {'timestamp': m['game_end_timestamp'], 'elo': m['post_game_valor_clasificacion']}
        for m in soloq_matches
    ]
    perfil['elo_history_flexq'] = [
        {'timestamp': m['game_end_timestamp'], 'elo': m['post_game_valor_clasificacion']}
        for m in flexq_matches
    ]
    
    # Calcular rachas
    if 'soloq' in perfil:
        soloq_matches_rachas = [m for m in matches if m.get('queue_id') == 420]
        streaks = calculate_streaks(soloq_matches_rachas)
        perfil['soloq'].update(streaks)
    
    if 'flexq' in perfil:
        flexq_matches_rachas = [m for m in matches if m.get('queue_id') == 440]
        streaks = calculate_streaks(flexq_matches_rachas)
        perfil['flexq'].update(streaks)
    
    # Estadísticas por campeón
    champion_stats = {}
    for match in matches:
        champ = match.get('champion_name')
        if champ and champ != 'Desconocido':
            if champ not in champion_stats:
                champion_stats[champ] = {
                    'games_played': 0, 'wins': 0, 'losses': 0,
                    'kills': 0, 'deaths': 0, 'assists': 0
                }
            cs = champion_stats[champ]
            cs['games_played'] += 1
            if match.get('win'):
                cs['wins'] += 1
            else:
                cs['losses'] += 1
            cs['kills'] += match.get('kills', 0)
            cs['deaths'] += match.get('deaths', 0)
            cs['assists'] += match.get('assists', 0)
    
    for champ, stats in champion_stats.items():
        stats['win_rate'] = (stats['wins'] / stats['games_played'] * 100) if stats['games_played'] > 0 else 0
        stats['kda'] = (stats['kills'] + stats['assists']) / max(1, stats['deaths'])
    
    perfil['champion_stats'] = sorted(champion_stats.items(), key=lambda x: x[1]['games_played'], reverse=True)
    
    # Ordenar historial por fecha
    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    
    # Calcular LP 24h
    try:
        now_utc = datetime.now(timezone.utc)
        one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
        
        for queue_key, queue_id in [('soloq', 420), ('flexq', 440)]:
            if queue_key in perfil:
                cola_matches = [m for m in perfil['historial_partidas'] if m.get('queue_id') == queue_id][:30]
                lp_24h = 0
                wins_24h = 0
                losses_24h = 0
                
                for m in cola_matches:
                    if m.get('game_end_timestamp', 0) > one_day_ago:
                        lp_change = m.get('lp_change_this_game')
                        if lp_change is not None:
                            lp_24h += lp_change
                        if m.get('win'):
                            wins_24h += 1
                        else:
                            losses_24h += 1
                
                perfil[queue_key]['lp_change_24h'] = lp_24h
                perfil[queue_key]['wins_24h'] = wins_24h
                perfil[queue_key]['losses_24h'] = losses_24h
    except Exception as e:
        print(f"[_build_player_profile] Error calculando LP 24h: {e}")
    
    return perfil


@player_bp.route('/jugador/<path:game_name>')
def perfil_jugador(game_name):
    """
    Muestra una página de perfil para un jugador específico, detectando
    el tipo de dispositivo para renderizar la plantilla adecuada.
    """
    print(f"[perfil_jugador] Petición recibida para el perfil de jugador: {game_name}")
    perfil = _build_player_profile(game_name)
    if not perfil:
        print(f"[perfil_jugador] Perfil de jugador {game_name} no encontrado. Retornando 404.")
        return render_template('404.html'), 404

    user_agent_string = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent_string for keyword in ['mobi', 'android', 'iphone', 'ipad'])
    
    template_name = 'jugador.html'
    
    print(f"[perfil_jugador] Dispositivo detectado como {'Móvil' if is_mobile else 'Escritorio'}. Renderizando {template_name} para {game_name}.")

    return render_template(template_name,
                           perfil=perfil,
                           ddragon_version=DDRAGON_VERSION,
                           datetime=datetime,
                           now=datetime.now())
