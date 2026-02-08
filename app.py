"""
SoloQ-Cerditos - Aplicación Flask principal

Punto de entrada principal de la aplicación.
Organiza y coordina todos los servicios en segundo plano.
"""

import os
import sys
import threading
import time
from datetime import datetime, timezone

from flask import Flask

# Importar configuración
from config.settings import (
    RIOT_API_KEY,
    GITHUB_TOKEN,
    PORT,
    DEBUG,
    SECRET_KEY
)

# Importar utilidades
from utils.filters import register_filters
from utils.helpers import keep_alive

# Importar blueprints
from blueprints import (
    main_bp,
    player_bp,
    stats_bp,
    api_bp
)

# Importar servicios
from services import (
    start_cache_service,
    start_github_service,
    start_lp_tracker,
    start_data_updater,
    start_stats_calculator,
    start_rate_limiter
)


def create_app():
    """
    Factory function para crear la aplicación Flask.
    Configura la app, registra blueprints y filtros.
    """
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    
    # Inyectar 'str' en el contexto de Jinja2
    @app.context_processor
    def utility_processor():
        return dict(str=str)
    
    # Registrar filtros personalizados
    register_filters(app)
    
    # Registrar blueprints con prefijos URL
    app.register_blueprint(main_bp, url_prefix='/')
    app.register_blueprint(player_bp, url_prefix='/jugador')
    app.register_blueprint(stats_bp, url_prefix='/stats')
    app.register_blueprint(api_bp, url_prefix='/api')
    
    # Manejador de error 404
    @app.errorhandler(404)
    def not_found_error(error):
        from flask import render_template
        return render_template('404.html'), 404
    
    # Manejador de error 500
    @app.errorhandler(500)
    def internal_error(error):
        from flask import render_template
        return render_template('404.html'), 500
    
    return app


def start_background_services(riot_api_key, github_token):
    """
    Inicia todos los servicios en segundo plano.
    
    Args:
        riot_api_key: API key de Riot Games
        github_token: Token de GitHub para operaciones de archivo
    """
    print("\n" + "="*60)
    print("INICIANDO SERVICIOS EN SEGUNDO PLANO")
    print("="*60 + "\n")
    
    # 1. Rate Limiter (para API de Riot)
    start_rate_limiter()
    time.sleep(0.5)  # Pequeña pausa entre inicios
    
    # 2. Servicio de Caché
    start_cache_service()
    time.sleep(0.5)
    
    # 3. Servicio de GitHub
    start_github_service(github_token)
    time.sleep(0.5)
    
    # 4. LP Tracker (tracker de ELO)
    start_lp_tracker(riot_api_key, github_token)
    time.sleep(0.5)
    
    # 5. Data Updater (actualización de datos)
    start_data_updater(riot_api_key)
    time.sleep(0.5)
    
    # 6. Stats Calculator (cálculo de estadísticas)
    start_stats_calculator()
    time.sleep(0.5)
    
    # 7. Keep Alive (mantener app activa)
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("[main] ✓ Keep-alive iniciado")
    
    print("\n" + "="*60)
    print("TODOS LOS SERVICIOS INICIADOS CORRECTAMENTE")
    print("="*60 + "\n")


def main():
    """Punto de entrada principal de la aplicación."""
    print("\n" + "="*60)
    print("SOLOQ-CERDITOS - INICIANDO APLICACIÓN")
    print("="*60 + "\n")
    
    # Validar configuración esencial
    if not RIOT_API_KEY:
        print("⚠️  ADVERTENCIA: RIOT_API_KEY no está configurada")
        print("    Algunas funciones no estarán disponibles")
    
    if not GITHUB_TOKEN:
        print("⚠️  ADVERTENCIA: GITHUB_TOKEN no está configurado")
        print("    El almacenamiento persistente no funcionará")
    
    # Crear aplicación Flask
    app = create_app()
    print("[main] ✓ Aplicación Flask creada")
    
    # Iniciar servicios en segundo plano
    start_background_services(RIOT_API_KEY, GITHUB_TOKEN)
    
    # Iniciar servidor Flask
    print(f"\n[main] 🚀 Iniciando servidor en http://0.0.0.0:{PORT}")
    print(f"[main] Modo DEBUG: {DEBUG}")
    print("[main] Presiona Ctrl+C para detener\n")
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=DEBUG,
        threaded=True,
        use_reloader=False  # Importante: desactivar reloader para evitar doble inicio de servicios
    )


if __name__ == "__main__":
    main()
