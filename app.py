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
    SECRET_KEY,
    ENABLE_KEEP_ALIVE,
    ENABLE_STATS_CALCULATOR_THREAD,
    ENABLE_BOOT_INDEX_WARMUP,
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

# Importar función para actualizar versión y datos de Data Dragon
from services.riot_api import actualizar_version_ddragon, actualizar_ddragon_data

# Importar generador de JSON para el index

from services.index_json_generator import (
    generate_index_json, 
    load_index_json, 
    is_json_fresh,
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
    
    # 2. Servicio de Caché (en thread daemon)
    cache_thread = threading.Thread(target=start_cache_service, daemon=True)
    cache_thread.start()
    time.sleep(0.5)
    
    # 3. Servicio de GitHub
    start_github_service()

    time.sleep(0.5)
    
    # 4. LP Tracker (tracker de ELO - en thread daemon)
    lp_thread = threading.Thread(target=start_lp_tracker, args=(riot_api_key, github_token), daemon=True)
    lp_thread.start()
    time.sleep(0.5)

    
    # 5. Data Updater (actualización de datos)
    start_data_updater(riot_api_key)
    time.sleep(0.5)
    
    # 6. Stats Calculator (cálculo de estadísticas - en thread daemon)
    if ENABLE_STATS_CALCULATOR_THREAD:
        stats_thread = threading.Thread(target=start_stats_calculator, daemon=True)
        stats_thread.start()
        time.sleep(0.5)

    
    # 7. Keep Alive (mantener app activa)
    if ENABLE_KEEP_ALIVE:
        keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
        keep_alive_thread.start()
        print("[main] ✓ Keep-alive iniciado")
    
    print("\n" + "="*60)
    print("TODOS LOS SERVICIOS INICIADOS CORRECTAMENTE")
    print("="*60 + "\n")


# Crear aplicación Flask a nivel de módulo (para Gunicorn)
print("\n" + "="*60)
print("SOLOQ-CERDITOS - INICIANDO APLICACIÓN")
print("="*60 + "\n")

# Actualizar versión y datos de Data Dragon al inicio (DEBE SER PRIMERO)
print("[main] Actualizando versión de Data Dragon...")
actualizar_version_ddragon()
print("[main] ✓ Versión de Data Dragon actualizada")

print("[main] Cargando datos de campeones, runas y hechizos de Data Dragon...")
actualizar_ddragon_data()
print("[main] ✓ Datos de Data Dragon cargados correctamente")


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

# Precargar JSON del index si no existe o está antiguo
print("[main] Verificando JSON del index...")
json_data = load_index_json()
if ENABLE_BOOT_INDEX_WARMUP:
    if json_data is None or not is_json_fresh(max_age_seconds=300):
        print("[main] Generando JSON del index (primera vez o antiguo)...")
        if generate_index_json(force=True):
            print("[main] ✓ JSON del index generado correctamente")
        else:
            print("[main] ⚠ No se pudo generar el JSON del index")
    else:
        print("[main] ✓ JSON del index ya existe y está actualizado")
else:
    print("[main] Precarga del index desactivada en este entorno")

print(f"\n[main] 🚀 Aplicación lista para servir en http://0.0.0.0:{PORT}")
print(f"[main] Modo DEBUG: {DEBUG}\n")



def main():
    """Punto de entrada principal para desarrollo local."""
    # Iniciar servidor Flask (solo para desarrollo local)
    print(f"[main] Iniciando servidor de desarrollo en http://0.0.0.0:{PORT}")
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
