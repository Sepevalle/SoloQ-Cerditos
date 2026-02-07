from flask import Blueprint, render_template, jsonify, request
from services.data_processing import (
    obtener_datos_jugadores, 
    leer_historial_jugador_github,
    get_cached_global_stats,
    cache_global_stats,
    invalidate_global_stats_cache,
    filter_matches_by_queue,
    filter_matches_by_champion,
    calculate_player_stats_from_matches,
    get_top_champions,
    extract_global_records
)
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
    """
    Renderiza la página de estadísticas globales.
    OPTIMIZADO: Usa caché de 5 minutos y carga partidas de forma eficiente.
    """
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    current_queue = request.args.get('queue', 'all')
    selected_champion = request.args.get('champion', 'all')
    
    # OPTIMIZACIÓN 1: Intentar obtener datos del caché
    cached_data = get_cached_global_stats()
    if cached_data:
        # Si tenemos caché, usarla como base y aplicar filtros solo en cliente
        all_matches = cached_data['all_matches']
        all_champions_for_filtering = cached_data['all_champions']
        available_queue_ids = cached_data['available_queue_ids']
    else:
        # OPTIMIZACIÓN 2: Cargar datos de forma eficiente
        print("[estadisticas_globales] Compilando datos desde historiales de jugadores...")
        datos_jugadores, _ = obtener_datos_jugadores(queue_type='all')
        
        all_champions_for_filtering = set()
        all_matches = []
        available_queue_ids = set()

        # Recopilar partidas de todos los jugadores con límite de 100 partidas por jugador
        # Esto evita cargar historiales enormes que ralentizan la aplicación
        for j in datos_jugadores:
            puuid = j.get('puuid')
            if puuid:
                historial = leer_historial_jugador_github(puuid)
                # OPTIMIZACIÓN CRÍTICA: Limitar a últimas 100 partidas por jugador
                matches = historial.get('matches', [])[:100]
                
                for match in matches:
                    all_matches.append((j.get('jugador'), match))
                    if match.get('champion_name'):
                        all_champions_for_filtering.add(match.get('champion_name'))
                    if match.get('queue_id'):
                        available_queue_ids.add(match.get('queue_id'))
        
        # Cachear los datos compilados
        cache_global_stats({
            'all_matches': all_matches,
            'all_champions': all_champions_for_filtering,
            'available_queue_ids': available_queue_ids
        })

    champion_list = sorted(list(all_champions_for_filtering))
    
    available_queues = [{'id': q_id, 'name': queue_names.get(q_id, f"Unknown ({q_id})")} for q_id in sorted(list(available_queue_ids))]

    # OPTIMIZACIÓN 3: Filtrar partidas por cola PRIMERO (antes de procesar)
    all_matches = filter_matches_by_queue(all_matches, current_queue)

    # OPTIMIZACIÓN 4: Calcular estadísticas de forma eficiente
    stats_por_jugador = []
    player_stats_dict = {}
    
    for player_name, match in all_matches:
        if player_name not in player_stats_dict:
            player_stats_dict[player_name] = {'wins': 0, 'losses': 0, 'matches': []}
        
        if match.get('win'):
            player_stats_dict[player_name]['wins'] += 1
        else:
            player_stats_dict[player_name]['losses'] += 1
        
        player_stats_dict[player_name]['matches'].append(match)

    # Construir lista de estadísticas por jugador
    for player_name, stats in player_stats_dict.items():
        total_games = stats['wins'] + stats['losses']
        win_rate = (stats['wins'] / total_games * 100) if total_games > 0 else 0
        stats_por_jugador.append({
            'summonerName': player_name,
            'total_partidas': total_games,
            'win_rate': win_rate
        })
    
    stats_por_jugador.sort(key=lambda x: x['total_partidas'], reverse=True)

    # OPTIMIZACIÓN 5: Calcular estadísticas globales de forma más eficiente
    total_wins = sum(1 for _, match in all_matches if match.get('win'))
    total_losses = len(all_matches) - total_wins
    total_games_global = len(all_matches)
    overall_win_rate = (total_wins / total_games_global * 100) if total_games_global > 0 else 0

    # Filtrar por campeón si se especifica
    filtered_for_champion = filter_matches_by_champion(all_matches, selected_champion)
    
    # Obtener campeones más jugados
    champions_in_filtered_matches = [m[1].get('champion_name') for m in filtered_for_champion if m[1].get('champion_name')]
    most_played_champions = Counter(champions_in_filtered_matches).most_common(5)

    player_with_most_games = None
    if stats_por_jugador:
        player_with_most_games = stats_por_jugador[0]['summonerName']

    # OPTIMIZACIÓN 6: Extraer records de forma eficiente
    records = extract_global_records(filtered_for_champion)

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

