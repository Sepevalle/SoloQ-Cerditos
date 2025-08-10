from flask import Flask
import os
import threading
import requests
import time

from blueprints.main import main_bp
from blueprints.player import player_bp
from blueprints.stats import stats_bp
from blueprints.api import api_bp
from services.data_processing import actualizar_cache
from services.riot_api import _api_rate_limiter_worker, actualizar_historial_partidas_en_segundo_plano

app = Flask(__name__)

app.register_blueprint(main_bp)
app.register_blueprint(player_bp)
app.register_blueprint(stats_bp)
app.register_blueprint(api_bp, url_prefix='/api')

def keep_alive():
    """Envía una solicitud periódica a la propia aplicación para mantenerla activa en servicios como Render."""
    print("[keep_alive] Hilo de keep_alive iniciado.")
    while True:
        try:
            requests.get('https://soloq-cerditos-34kd.onrender.com/')
            print("[keep_alive] Manteniendo la aplicación activa con una solicitud.")
        except requests.exceptions.RequestException as e:
            print(f"[keep_alive] Error en keep_alive: {e}")
        time.sleep(200)

def actualizar_cache_periodicamente():
    """Actualiza la caché de datos de los jugadores de forma periódica."""
    print("[actualizar_cache_periodicamente] Hilo de actualización de caché periódica iniciado.")
    while True:
        actualizar_cache()
        time.sleep(130)

if __name__ == "__main__":
    print("[main] Iniciando la aplicación Flask.")
    
    api_rate_limiter_thread = threading.Thread(target=_api_rate_limiter_worker)
    api_rate_limiter_thread.daemon = True
    api_rate_limiter_thread.start()
    print("[main] Hilo 'api_rate_limiter_thread' iniciado.")

    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()
    print("[main] Hilo 'keep_alive' iniciado.")

    cache_thread = threading.Thread(target=actualizar_cache_periodicamente)
    cache_thread.daemon = True
    cache_thread.start()
    print("[main] Hilo 'actualizar_cache_periodicamente' iniciado.")

    stats_thread = threading.Thread(target=actualizar_historial_partidas_en_segundo_plano)
    stats_thread.daemon = True
    stats_thread.start()
    print("[main] Hilo 'actualizar_historial_partidas_en_segundo_plano' iniciado.")

    port = int(os.environ.get("PORT", 5000))
    print(f"[main] Aplicación Flask ejecutándose en http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)
