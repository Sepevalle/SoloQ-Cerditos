"""
Registro de blueprints para la aplicación Flask.
"""

from flask import Flask
from blueprints.main import main_bp
from blueprints.player import player_bp
from blueprints.api import api_bp
from blueprints.stats import stats_bp


def register_blueprints(app: Flask):
    """Registra todos los blueprints en la aplicación."""
    app.register_blueprint(main_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(stats_bp)
    
    print("[register_blueprints] Blueprints registrados correctamente")
