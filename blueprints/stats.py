from flask import Blueprint, render_template, jsonify, request
from services.data_processing import obtener_datos_jugadores, leer_historial_jugador_github
from collections import Counter

stats_bp = Blueprint('stats', __name__)

# Copied from app.py to have access to queue names
queue_names = {
    400: "Normal (Blind Pick)",
    420: "Clasificatoria Solo/Duo",
    430: "Normal (Draft Pick)",
    440: "Clasificatoria Flexible",
    450: "ARAM",
    700: "Clash",
    800: "Co-op vs. AI (Beginner)",
    810: "Co-op vs. AI (Intermediate)",
    820: "Co-op vs. AI (Intro)",
    830: "Co-op vs. AI (Twisted Treeline)",
    840: "Co-op vs. AI (Summoner's Rift)",
    850: "Co-op vs. AI (ARAM)",
    900: "URF",
    1020: "One For All",
    1090: "Arena",
    1100: "Arena",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1900: "URF (ARAM)",
    2000: "Tutorial",
    2010: "Tutorial",
    2020: "Tutorial",
}

@stats_bp.route('/estadisticas')
def estadisticas_globales():
    """Renderiza la página de estadísticas globales."""
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    current_queue = request.args.get('queue', 'all')
    selected_champion = request.args.get('champion', 'all')
    
    # We get all players, filtering by queue type is done later
    datos_jugadores, _ = obtener_datos_jugadores(queue_type='all')
    
    all_champions_for_filtering = set()
    all_matches = []
    available_queue_ids = set()

    # Recopilar todas las partidas y campeones de todos los jugadores
    for j in datos_jugadores:
        puuid = j.get('puuid')
        if puuid:
            historial = leer_historial_jugador_github(puuid)
            for match in historial.get('matches', []):
                all_matches.append((j.get('jugador'), match))
                if match.get('champion_name'):
                    all_champions_for_filtering.add(match.get('champion_name'))
                if match.get('queue_id'):
                    available_queue_ids.add(match.get('queue_id'))

    champion_list = sorted(list(all_champions_for_filtering))
    
    available_queues = [{'id': q_id, 'name': queue_names.get(q_id, f"Unknown ({q_id})")} for q_id in sorted(list(available_queue_ids))]

    # Filter matches based on current_queue
    if current_queue != 'all':
        all_matches = [(player, match) for player, match in all_matches if str(match.get('queue_id')) == current_queue]

    # Stats for the first tab (by player) - This part might need reconsideration as it's based on aggregated data
    stats_por_jugador = []
    # This calculation is difficult with the new filtering model, so we will simplify it for now
    # Or we can calculate it from the filtered matches
    
    player_stats = {}
    for player_name, match in all_matches:
        if player_name not in player_stats:
            player_stats[player_name] = {'wins': 0, 'losses': 0}
        if match.get('win'):
            player_stats[player_name]['wins'] += 1
        else:
            player_stats[player_name]['losses'] += 1

    for player_name, stats in player_stats.items():
        total_games = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total_games * 100) if total_games > 0 else 0
        stats_por_jugador.append({
            'summonerName': player_name,
            'total_partidas': total_games,
            'win_rate': win_rate
        })
    
    stats_por_jugador.sort(key=lambda x: x['total_partidas'], reverse=True)


    # Stats for the second tab (global)
    total_wins = sum(1 for _, match in all_matches if match.get('win'))
    total_losses = len(all_matches) - total_wins
    total_games_global = len(all_matches)
    overall_win_rate = (total_wins / total_games_global * 100) if total_games_global > 0 else 0

    champions_in_filtered_matches = []
    for _, match in all_matches:
        if selected_champion == 'all' or match.get('champion_name') == selected_champion:
            champions_in_filtered_matches.append(match.get('champion_name'))

    most_played_champions = Counter(champions_in_filtered_matches).most_common(5)

    player_with_most_games = None
    if stats_por_jugador:
        player_with_most_games = stats_por_jugador[0]['summonerName']

    # New global records
    records = {
        'Más Asesinatos': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull-crossbones'},
        'Más Muertes': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull'},
        'Más Asistencias': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-hands-helping'},
        'Mejor KDA': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-star'},
        'Más CS': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-tractor'},
        'Mayor Puntuación de Visión': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-eye'}
    }

    for player_name, match in all_matches:
        # Apply champion filter
        if selected_champion != 'all' and match.get('champion_name') != selected_champion:
            continue

        if match.get('kills', 0) > records['Más Asesinatos']['value']:
            records['Más Asesinatos']['value'] = match.get('kills')
            records['Más Asesinatos']['player'] = player_name
            records['Más Asesinatos']['champion'] = match.get('champion_name')
        
        if match.get('deaths', 0) > records['Más Muertes']['value']:
            records['Más Muertes']['value'] = match.get('deaths')
            records['Más Muertes']['player'] = player_name
            records['Más Muertes']['champion'] = match.get('champion_name')

        if match.get('assists', 0) > records['Más Asistencias']['value']:
            records['Más Asistencias']['value'] = match.get('assists')
            records['Más Asistencias']['player'] = player_name
            records['Más Asistencias']['champion'] = match.get('champion_name')

        kda = (match.get('kills', 0) + match.get('assists', 0)) / max(1, match.get('deaths', 0))
        if kda > records['Mejor KDA']['value']:
            records['Mejor KDA']['value'] = kda
            records['Mejor KDA']['player'] = player_name
            records['Mejor KDA']['champion'] = match.get('champion_name')

        total_cs = match.get('total_minions_killed', 0) + match.get('neutral_minions_killed', 0)
        if total_cs > records['Más CS']['value']:
            records['Más CS']['value'] = total_cs
            records['Más CS']['player'] = player_name
            records['Más CS']['champion'] = match.get('champion_name')

        if match.get('vision_score', 0) > records['Mayor Puntuación de Visión']['value']:
            records['Mayor Puntuación de Visión']['value'] = match.get('vision_score')
            records['Mayor Puntuación de Visión']['player'] = player_name
            records['Mayor Puntuación de Visión']['champion'] = match.get('champion_name')

    global_stats = {
        'overall_win_rate': overall_win_rate,
        'total_games': total_games_global,
        'most_played_champions': most_played_champions,
        'player_with_most_games': player_with_most_games,
        'global_records': records
    }

    return render_template(
        'estadisticas.html', 
        stats=stats_por_jugador, 
        global_stats=global_stats, 
        ddragon_version="14.9.1", 
        champion_list=champion_list, 
        selected_champion=selected_champion, 
        current_queue=current_queue,
        available_queues=available_queues
    )

