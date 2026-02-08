"""
SoloQ-Cerditos - Punto de entrada principal de la aplicación Flask.

Esta aplicación ha sido reorganizada en una arquitectura de servicios:
- config/: Configuración centralizada
- services/: Lógica de negocio (API Riot, caché, GitHub, etc.)
- blueprints/: Rutas Flask organizadas por funcionalidad
- utils/: Funciones utilitarias y filtros Jinja2
"""

import os
import threading
import time
from flask import Flask

# Importar configuración
from config.settings import (
    TARGET_TIMEZONE, DDRAGON_VERSION, PORT, 
    CACHE_UPDATE_INTERVAL, LP_TRACKER_INTERVAL
)

# Importar blueprints
from blueprints import register_blueprints

# Importar filtros Jinja2
from utils.filters import register_filters

# Importar servicios para inicialización
from services.cache_service import player_cache
from services.lp_tracker import elo_tracker_worker
from services.riot_api import _api_rate_limiter_worker
from services.data_updater import (
    keep_alive, 
    actualizar_cache_periodicamente,
    actualizar_historial_partidas_en_segundo_plano,
    _calculate_and_cache_global_stats_periodically,
    _calculate_and_cache_personal_records_periodically
)



def create_app():
    """Factory function para crear la aplicación Flask."""
    app = Flask(__name__)
    
    # Inyectar 'str' en el contexto de Jinja2
    @app.context_processor
    def utility_processor():
        return dict(str=str)
    
    # Registrar filtros Jinja2 personalizados
    register_filters(app)
    
    # Registrar blueprints
    register_blueprints(app)
    
    print("[create_app] Aplicación Flask creada y configurada")
    return app


def start_background_threads(app):
    """Inicia todos los hilos de background necesarios."""
    threads = []
    
    # 1. Control de tasa de API
    api_rate_limiter_thread = threading.Thread(
        target=_api_rate_limiter_worker,
        name="api_rate_limiter"
    )
    api_rate_limiter_thread.daemon = True
    api_rate_limiter_thread.start()
    threads.append(api_rate_limiter_thread)
    print("[start_background_threads] Hilo 'api_rate_limiter' iniciado")
    
    # 2. Keep-alive (ping periódico)
    keep_alive_thread = threading.Thread(
        target=keep_alive,
        name="keep_alive"
    )
    keep_alive_thread.daemon = True
    keep_alive_thread.start()
    threads.append(keep_alive_thread)
    print("[start_background_threads] Hilo 'keep_alive' iniciado")
    
    # 3. Actualización periódica de caché de jugadores
    cache_thread = threading.Thread(
        target=actualizar_cache_periodicamente,
        name="cache_updater"
    )
    cache_thread.daemon = True
    cache_thread.start()
    threads.append(cache_thread)
    print("[start_background_threads] Hilo 'actualizar_cache_periodicamente' iniciado")
    
    # 4. Actualización de historial de partidas
    stats_thread = threading.Thread(
        target=actualizar_historial_partidas_en_segundo_plano,
        name="match_history_updater"
    )
    stats_thread.daemon = True
    stats_thread.start()
    threads.append(stats_thread)
    print("[start_background_threads] Hilo 'actualizar_historial_partidas_en_segundo_plano' iniciado")
    
    # 5. Cálculo de estadísticas globales
    global_stats_thread = threading.Thread(
        target=_calculate_and_cache_global_stats_periodically,
        name="global_stats_calculator"
    )
    global_stats_thread.daemon = True
    global_stats_thread.start()
    threads.append(global_stats_thread)
    print("[start_background_threads] Hilo 'actualizar_estadisticas_globales_periodicamente' iniciado")
    
    # 6. Cálculo de récords personales
    personal_records_thread = threading.Thread(
        target=_calculate_and_cache_personal_records_periodically,
        name="personal_records_calculator"
    )
    personal_records_thread.daemon = True
    personal_records_thread.start()
    threads.append(personal_records_thread)
    print("[start_background_threads] Hilo 'actualizar_records_personales_periodicamente' iniciado")
    
    # 7. Tracker de ELO/LP (CRÍTICO para calcular cambio de LP)
    from config.settings import RIOT_API_KEY, GITHUB_TOKEN
    if RIOT_API_KEY and GITHUB_TOKEN:
        lp_tracker_thread = threading.Thread(
            target=elo_tracker_worker,
            args=(RIOT_API_KEY, GITHUB_TOKEN),
            name="lp_tracker"
        )
        lp_tracker_thread.daemon = True
        lp_tracker_thread.start()
        threads.append(lp_tracker_thread)
        print("[start_background_threads] Hilo 'elo_tracker_worker' iniciado")
    else:
        print("[start_background_threads] ADVERTENCIA: RIOT_API_KEY o GITHUB_TOKEN no configurados. LP tracker no iniciado.")
    
    return threads


def main():
    """Función principal de entrada."""
    print("=" * 60)
    print("SoloQ-Cerditos - Iniciando aplicación")
    print("=" * 60)
    
    # Crear aplicación Flask
    app = create_app()
    
    # Iniciar hilos de background
    threads = start_background_threads(app)
    
    print(f"\n[main] {len(threads)} hilos de background iniciados")
    print(f"[main] Aplicación Flask ejecutándose en http://0.0.0.0:{PORT}")
    print("=" * 60)
    
    # Iniciar servidor Flask
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=False,  # No usar debug en producción
        threaded=True
    )


if __name__ == "__main__":
    main()
