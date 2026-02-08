from flask import Blueprint, render_template
from datetime import datetime, timezone
from config.settings import TARGET_TIMEZONE, ACTIVE_SPLIT_KEY, SPLITS, DDRAGON_VERSION
from services.cache_service import player_cache
from services.github_service import read_peak_elo, save_peak_elo
from services.stats_service import get_top_champions_for_player
from services.match_service import get_player_match_history
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

    # Calcular estadísticas de campeones para cada jugador
    for jugador in datos_jugadores:
        try:
            puuid = jugador.get('puuid')
            if puuid:
                # Obtener historial de partidas del jugador
                match_history = get_player_match_history(puuid, limit=50)
                matches = match_history.get('matches', [])
                
                # Calcular top campeones
                top_champions = get_top_champions_for_player(matches, limit=3)
                jugador['top_champion_stats'] = top_champions
            else:
                jugador['top_champion_stats'] = []
        except Exception as e:
            print(f"[index] Error calculando top campeones para {jugador.get('jugador', 'unknown')}: {e}")
            jugador['top_champion_stats'] = []


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
