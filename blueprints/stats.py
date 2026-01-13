from flask import Blueprint, render_template, jsonify, request
from services.data_processing import obtener_datos_jugadores, leer_historial_jugador_github
from collections import Counter

stats_bp = Blueprint('stats', __name__)

@stats_bp.route('/estadisticas')
def estadisticas_globales():
    """Renderiza la página de estadísticas globales."""
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    current_queue = request.args.get('queue', 'all')
    selected_champion = request.args.get('champion', 'all')
    
    datos_jugadores, _ = obtener_datos_jugadores(queue_type=current_queue)
    
    all_champions_for_filtering = set()
    
    # Recopilar todos los campeones de todas las partidas de todos los jugadores
    todos_los_datos_sin_filtro, _ = obtener_datos_jugadores(queue_type='all')
    for j in todos_los_datos_sin_filtro:
        puuid = j.get('puuid')
        if puuid:
            historial = leer_historial_jugador_github(puuid)
            for match in historial.get('matches', []):
                if match.get('champion_name'):
                    all_champions_for_filtering.add(match.get('champion_name'))
    
    champion_list = sorted(list(all_champions_for_filtering))

    # Stats for the first tab (by player)
    stats_por_jugador = []
    for jugador in datos_jugadores:
        total_games = jugador.get('wins', 0) + jugador.get('losses', 0)
        win_rate = (jugador.get('wins', 0) / total_games * 100) if total_games > 0 else 0
        
        stats_por_jugador.append({
            'summonerName': jugador.get('jugador'),
            'queueType': jugador.get('queue_type'),
            'total_partidas': total_games,
            'win_rate': win_rate
        })
    
    stats_por_jugador.sort(key=lambda x: x['total_partidas'], reverse=True)

    # Stats for the second tab (global)
    total_wins = sum(j.get('wins', 0) for j in datos_jugadores)
    total_losses = sum(j.get('losses', 0) for j in datos_jugadores)
    total_games_global = total_wins + total_losses
    overall_win_rate = (total_wins / total_games_global * 100) if total_games_global > 0 else 0

    all_champions = []
    for j in datos_jugadores:
        puuid = j.get('puuid')
        if puuid:
            historial = leer_historial_jugador_github(puuid)
            for match in historial.get('matches', []):
                # Filtrar por campeón si se ha seleccionado uno
                if selected_champion == 'all' or match.get('champion_name') == selected_champion:
                    all_champions.append(match.get('champion_name'))

    most_played_champions = Counter(all_champions).most_common(5)

    player_with_most_games = None
    max_games = -1
    for j in datos_jugadores:
        total_games = j.get('wins', 0) + j.get('losses', 0)
        if total_games > max_games:
            max_games = total_games
            player_with_most_games = j.get('jugador')

    # New global records
    records = {
        'Más Asesinatos': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull-crossbones'},
        'Más Muertes': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull'},
        'Más Asistencias': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-hands-helping'},
        'Mejor KDA': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-star'},
        'Más CS': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-tractor'},
        'Mayor Puntuación de Visión': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-eye'}
    }

    for j in datos_jugadores:
        puuid = j.get('puuid')
        if puuid:
            historial = leer_historial_jugador_github(puuid)
            for match in historial.get('matches', []):
                # Aplicar filtro de campeón aquí también
                if selected_champion != 'all' and match.get('champion_name') != selected_champion:
                    continue

                if match.get('kills') > records['Más Asesinatos']['value']:
                    records['Más Asesinatos']['value'] = match.get('kills')
                    records['Más Asesinatos']['player'] = j.get('jugador')
                    records['Más Asesinatos']['champion'] = match.get('champion_name')
                
                if match.get('deaths') > records['Más Muertes']['value']:
                    records['Más Muertes']['value'] = match.get('deaths')
                    records['Más Muertes']['player'] = j.get('jugador')
                    records['Más Muertes']['champion'] = match.get('champion_name')

                if match.get('assists') > records['Más Asistencias']['value']:
                    records['Más Asistencias']['value'] = match.get('assists')
                    records['Más Asistencias']['player'] = j.get('jugador')
                    records['Más Asistencias']['champion'] = match.get('champion_name')

                kda = (match.get('kills', 0) + match.get('assists', 0)) / max(1, match.get('deaths', 0))
                if kda > records['Mejor KDA']['value']:
                    records['Mejor KDA']['value'] = kda
                    records['Mejor KDA']['player'] = j.get('jugador')
                    records['Mejor KDA']['champion'] = match.get('champion_name')

                total_cs = match.get('total_minions_killed', 0) + match.get('neutral_minions_killed', 0)
                if total_cs > records['Más CS']['value']:
                    records['Más CS']['value'] = total_cs
                    records['Más CS']['player'] = j.get('jugador')
                    records['Más CS']['champion'] = match.get('champion_name')

                if match.get('vision_score') > records['Mayor Puntuación de Visión']['value']:
                    records['Mayor Puntuación de Visión']['value'] = match.get('vision_score')
                    records['Mayor Puntuación de Visión']['player'] = j.get('jugador')
                    records['Mayor Puntuación de Visión']['champion'] = match.get('champion_name')


    global_stats = {
        'overall_win_rate': overall_win_rate,
        'total_games': total_games_global,
        'most_played_champions': most_played_champions,
        'player_with_most_games': player_with_most_games,
        'records': records
    }

    return render_template('estadisticas.html', stats=stats_por_jugador, global_stats=global_stats, ddragon_version="14.9.1", champion_list=champion_list, selected_champion=selected_champion, current_queue=current_queue)

