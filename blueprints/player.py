from flask import Blueprint, render_template, request
from services.data_processing import _get_player_profile_data
from datetime import datetime

player_bp = Blueprint('player', __name__)

@player_bp.route('/jugador/<path:game_name>')
def perfil_jugador(game_name):
    """
    Muestra una página de perfil para un jugador específico, detectando
    el tipo de dispositivo para renderizar la plantilla adecuada.
    """
    print(f"[perfil_jugador] Petición recibida para el perfil de jugador: {game_name}")
    perfil = _get_player_profile_data(game_name)
    if not perfil:
        print(f"[perfil_jugador] Perfil de jugador {game_name} no encontrado. Retornando 404.")
        return render_template('404.html'), 404

    user_agent_string = request.headers.get('User-Agent', '').lower()
    is_mobile = any(keyword in user_agent_string for keyword in ['mobi', 'android', 'iphone', 'ipad'])
    
    template_name = 'jugador.html'
    
    print(f"[perfil_jugador] Dispositivo detectado como {'Móvil' if is_mobile else 'Escritorio'}. Renderizando {template_name} para {game_name}.")

    return render_template(template_name,
                           perfil=perfil,
                           ddragon_version="14.9.1",
                           datetime=datetime,
                           now=datetime.now())
