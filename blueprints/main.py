from flask import Blueprint, render_template
from datetime import datetime, timezone, timedelta
from config.settings import TARGET_TIMEZONE, ACTIVE_SPLIT_KEY, SPLITS, DDRAGON_VERSION
from services.cache_service import player_cache
from services.github_service import read_peak_elo, save_peak_elo, read_lp_history
from services.stats_service import get_top_champions_for_player
from services.match_service import get_player_match_history, calculate_streaks
from utils.helpers import calcular_valor_clasificacion



main_bp = Blueprint('main', __name__)


def _get_peak_elo_key(jugador):
    """Genera la clave para peak elo basada en jugador."""
    return f"{ACTIVE_SPLIT_KEY}|{jugador['queue_type']}|{jugador['puuid']}"


@main_bp.route('/')
def index():
    """Renderiza la página principal con la lista de jugadores."""
    print("[index] Petición recibida para la página principal.")
    datos_jugadores, timestamp = player_cache.get()
    
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
    
    # Calcular estadísticas adicionales para cada jugador
    for jugador in datos_jugadores:
        try:
            puuid = jugador.get('puuid')
            queue_type = jugador.get('queue_type')
            queue_id = 420 if queue_type == 'RANKED_SOLO_5x5' else 440 if queue_type == 'RANKED_FLEX_SR' else None
            
            if puuid:
                # Obtener historial de partidas del jugador
                match_history = get_player_match_history(puuid, limit=50)
                matches = match_history.get('matches', [])
                
                # Calcular top campeones
                top_champions = get_top_champions_for_player(matches, limit=3)
                jugador['top_champion_stats'] = top_champions
                
                # Calcular racha actual para esta cola
                if queue_id:
                    queue_matches = [m for m in matches if m.get('queue_id') == queue_id]
                    streaks = calculate_streaks(queue_matches)
                    jugador['current_win_streak'] = streaks.get('current_win_streak', 0)
                    jugador['current_loss_streak'] = streaks.get('current_loss_streak', 0)
                else:
                    jugador['current_win_streak'] = 0
                    jugador['current_loss_streak'] = 0
                
                # Calcular LP 24h para esta cola
                if queue_id:
                    now_utc = datetime.now(timezone.utc)
                    one_day_ago = int((now_utc - timedelta(days=1)).timestamp() * 1000)
                    
                    lp_24h = 0
                    wins_24h = 0
                    losses_24h = 0
                    
                    # Filtrar partidas de las últimas 24h en esta cola
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
            else:
                jugador['top_champion_stats'] = []
                jugador['current_win_streak'] = 0
                jugador['current_loss_streak'] = 0
                jugador['lp_change_24h'] = 0
                jugador['wins_24h'] = 0
                jugador['losses_24h'] = 0
        except Exception as e:
            print(f"[index] Error calculando estadísticas para {jugador.get('jugador', 'unknown')}: {e}")
            jugador['top_champion_stats'] = []
            jugador['current_win_streak'] = 0
            jugador['current_loss_streak'] = 0
            jugador['lp_change_24h'] = 0
            jugador['wins_24h'] = 0
            jugador['losses_24h'] = 0



    # El timestamp de la caché está en segundos UTC (de time.time())
    dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # Convertir a la zona horaria de visualización deseada (UTC+2)
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    ultima_actualizacion = dt_target.strftime("%d/%m/%Y %H:%M:%S")
    
    split_activo_nombre = SPLITS[ACTIVE_SPLIT_KEY]['name']
    
    print("[index] Renderizando index.html.")
    return render_template('index.html', 
                           datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version=DDRAGON_VERSION,
                           split_activo_nombre=split_activo_nombre,
                           has_player_data=bool(datos_jugadores))
