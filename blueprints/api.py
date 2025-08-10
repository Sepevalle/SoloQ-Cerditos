from flask import Blueprint, jsonify
from services.data_processing import obtener_datos_jugadores, leer_historial_jugador_github

api_bp = Blueprint('api', __name__)

@api_bp.route('/api/compare/<player1_name>/<player2_name>')
def compare_players_api(player1_name, player2_name):
    """API endpoint para comparar dos jugadores."""
    print(f"[compare_players_api] Petición de API para comparar a {player1_name} y {player2_name}.")
    
    datos_jugadores, _ = obtener_datos_jugadores()
    
    player1_data = next((j for j in datos_jugadores if j.get('jugador') == player1_name), None)
    player2_data = next((j for j in datos_jugadores if j.get('jugador') == player2_name), None)

    if not player1_data or not player2_data:
        return jsonify({'error': 'No se encontraron datos para uno o ambos jugadores.'}), 404

    def get_player_stats(player_data):
        total_games = player_data.get('wins', 0) + player_data.get('losses', 0)
        win_rate = (player_data.get('wins', 0) / total_games * 100) if total_games > 0 else 0
        
        puuid = player_data.get('puuid')
        historial = leer_historial_jugador_github(puuid) if puuid else {'matches': []}
        
        total_kills = sum(m.get('kills', 0) for m in historial['matches'])
        total_deaths = sum(m.get('deaths', 0) for m in historial['matches'])
        total_assists = sum(m.get('assists', 0) for m in historial['matches'])
        
        avg_kda = (total_kills + total_assists) / max(1, total_deaths)
        
        total_cs = sum(m.get('total_minions_killed', 0) + m.get('neutral_minions_killed', 0) for m in historial['matches'])
        total_duration_minutes = sum(m.get('game_duration', 0) for m in historial['matches']) / 60
        avg_cs_per_min = total_cs / total_duration_minutes if total_duration_minutes > 0 else 0
        
        total_vision_score = sum(m.get('vision_score', 0) for m in historial['matches'])
        avg_vision_score_per_min = total_vision_score / total_duration_minutes if total_duration_minutes > 0 else 0

        return {
            "Elo": f"{player_data.get('tier')} {player_data.get('rank')} ({player_data.get('league_points')} LPs)",
            "Victorias": player_data.get('wins', 0),
            "Derrotas": player_data.get('losses', 0),
            "Total de Partidas": total_games,
            "Win Rate": f"{win_rate:.2f}%",
            "KDA Promedio": f"{avg_kda:.2f}",
            "CS/min Promedio": f"{avg_cs_per_min:.1f}",
            "Vision Score/min Promedio": f"{avg_vision_score_per_min:.1f}"
        }

    comparison = {
        'player1': get_player_stats(player1_data),
        'player2': get_player_stats(player2_data)
    }
    
    return jsonify(comparison)
