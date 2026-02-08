from flask import Blueprint, render_template, jsonify, request
from collections import Counter
from config.settings import DDRAGON_VERSION, QUEUE_NAMES
from services.cache_service import player_cache, global_stats_cache
from services.player_service import get_all_accounts, get_all_puuids
from services.match_service import get_player_match_history, filter_matches_by_queue, filter_matches_by_champion
from services.stats_service import extract_global_records

stats_bp = Blueprint('stats', __name__)


@stats_bp.route('/estadisticas')
def estadisticas_globales():
    """
    Renderiza la página de estadísticas globales.
    OPTIMIZADO: Usa caché inteligente (5 minutos) pero carga TODAS las partidas para precisión.
    """
    print("[estadisticas_globales] Petición recibida para la página de estadísticas globales.")
    
    current_queue = request.args.get('queue', 'all')
    selected_champion = request.args.get('champion', 'all')
    
    # OPTIMIZACIÓN 1: Intentar obtener datos del caché global
    cached_data = global_stats_cache.get()
    if cached_data and cached_data.get('data'):
        data = cached_data['data']
        print("[estadisticas_globales] Devolviendo datos del caché global")
        all_matches = data.get('all_matches', [])
        all_champions_for_filtering = data.get('all_champions', set())
        available_queue_ids = data.get('available_queue_ids', set())
    else:
        # CACHÉ EXPIRADO: Recompilación de datos frescos
        print("[estadisticas_globales] Caché expirado - compilando datos frescos...")
        datos_jugadores, _ = player_cache.get()
        
        all_champions_for_filtering = set()
        all_matches = []
        available_queue_ids = set()

        # Cargar TODAS las partidas para cada jugador
        for j in datos_jugadores:
            puuid = j.get('puuid')
            if puuid:
                historial = get_player_match_history(puuid, limit=-1)
                matches = historial.get('matches', [])
                
                for match in matches:
                    all_matches.append((j.get('jugador'), match))
                    if match.get('champion_name'):
                        all_champions_for_filtering.add(match.get('champion_name'))
                    if match.get('queue_id'):
                        available_queue_ids.add(match.get('queue_id'))
        
        print(f"[estadisticas_globales] Compiladas {len(all_matches)} partidas de {len(datos_jugadores)} jugadores")
        
        # Cachear los datos compilados
        global_stats_cache.set({
            'all_matches': all_matches,
            'all_champions': all_champions_for_filtering,
            'available_queue_ids': available_queue_ids
        })

    champion_list = sorted(list(all_champions_for_filtering))
    
    available_queues = [{'id': q_id, 'name': QUEUE_NAMES.get(q_id, f"Unknown ({q_id})")} 
                       for q_id in sorted(list(available_queue_ids))]

    # OPTIMIZACIÓN 2: Filtrar partidas por cola
    all_matches = filter_matches_by_queue(all_matches, current_queue)

    # OPTIMIZACIÓN 3: Calcular estadísticas de forma eficiente
    stats_por_jugador = []
    player_stats_dict = {}
    
    for player_name, match in all_matches:
        if player_name not in player_stats_dict:
            player_stats_dict[player_name] = {'wins': 0, 'losses': 0}
        
        if match.get('win'):
            player_stats_dict[player_name]['wins'] += 1
        else:
            player_stats_dict[player_name]['losses'] += 1

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

    # OPTIMIZACIÓN 4: Calcular estadísticas globales
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

    # OPTIMIZACIÓN 5: Extraer records
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
        ddragon_version=DDRAGON_VERSION, 
        champion_list=champion_list, 
        selected_champion=selected_champion, 
        current_queue=current_queue,
        available_queues=available_queues
    )
