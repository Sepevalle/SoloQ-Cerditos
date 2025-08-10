from flask import Blueprint, render_template
from services.data_processing import obtener_datos_jugadores, leer_peak_elo, get_peak_elo_key, guardar_peak_elo_en_github
from datetime import datetime, timezone, timedelta

main_bp = Blueprint('main', __name__)

# --- CONFIGURACIÓN DE ZONA HORARIA ---
TARGET_TIMEZONE = timezone(timedelta(hours=2))

@main_bp.route('/')
def index():
    """Renderiza la página principal con la lista de jugadores."""
    print("[index] Petición recibida para la página principal.")
    datos_jugadores, timestamp = obtener_datos_jugadores()
    
    lectura_exitosa, peak_elo_dict = leer_peak_elo()

    if lectura_exitosa:
        actualizado = False
        for jugador in datos_jugadores:
            key = get_peak_elo_key(jugador)
            peak = peak_elo_dict.get(key, 0)

            valor = jugador["valor_clasificacion"]
            if valor > peak:
                peak_elo_dict[key] = valor
                peak = valor
                actualizado = True
                print(f"[index] Peak Elo actualizado para {jugador['game_name']} en {jugador['queue_type']}: {peak}")
            jugador["peak_elo"] = peak

        if actualizado:
            guardar_peak_elo_en_github(peak_elo_dict)
    else:
        print("[index] ADVERTENCIA: No se pudo leer el archivo peak_elo.json. Se omitirá la actualización de picos.")
        for jugador in datos_jugadores:
            jugador["peak_elo"] = jugador["valor_clasificacion"]

    # El timestamp de la caché está en segundos UTC (de time.time())
    dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # Convertir a la zona horaria de visualización deseada (UTC+2)
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    ultima_actualizacion = dt_target.strftime("%d/%m/%Y %H:%M:%S")
    
    print("[index] Renderizando index.html.")
    return render_template('index.html', datos_jugadores=datos_jugadores,
                           ultima_actualizacion=ultima_actualizacion,
                           ddragon_version="14.9.1")
