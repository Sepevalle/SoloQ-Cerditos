def _get_player_profile_data(game_name):
    """
    Función auxiliar que encapsula la lógica para obtener y procesar
    todos los datos de un perfil de jugador.
    Devuelve el diccionario 'perfil' o None si no se encuentra el jugador.
    """
    print(f"[_get_player_profile_data] Obteniendo datos de perfil para: {game_name}")
    todos_los_datos, _ = obtener_datos_jugadores()
    datos_del_jugador = [j for j in todos_los_datos if j.get('game_name') == game_name]
    
    if not datos_del_jugador:
        print(f"[_get_player_profile_data] No se encontraron datos para el jugador {game_name} en la caché.")
        return None
    
    primer_perfil = datos_del_jugador[0]
    puuid = primer_perfil.get('puuid')

    historial_partidas_completo = {}
    if puuid:
        historial_partidas_completo = leer_historial_jugador_github(puuid)
        for match in historial_partidas_completo.get('matches', []):
            if 'lp_change_this_game' not in match:
                match['lp_change_this_game'] = None
                print(f"[_get_player_profile_data] Inicializando 'lp_change_this_game' a None para la partida {match.get('match_id')} del jugador {puuid}.")

    perfil = {
        'nombre': primer_perfil.get('jugador', 'N/A'),
        'game_name': game_name,
        'perfil_icon_url': primer_perfil.get('perfil_icon_url', ''),
        'historial_partidas': historial_partidas_completo.get('matches', [])
    }

    for item in datos_del_jugador:
        if item.get('queue_type') == 'RANKED_SOLO_5x5':
            perfil['soloq'] = item
        elif item.get('queue_type') == 'RANKED_FLEX_SR':
            perfil['flexq'] = item

    historial_total = perfil.get('historial_partidas', [])
    
    # --- START of new ELO History Logic ---
    perfil['elo_history_soloq'] = []
    perfil['elo_history_flexq'] = []    

    # --- Lógica de Gráfico de Evolución (Línea Suave) ---
    # Filtra partidas que tienen el dato de ELO post-partida y las ordena cronológicamente.
    partidas_con_elo_soloq = sorted(
        [p for p in historial_total if p.get('queue_id') == 420 and p.get('post_game_valor_clasificacion') is not None],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    perfil['elo_history_soloq'] = [
        {'timestamp': p['game_end_timestamp'], 'elo': p['post_game_valor_clasificacion']}
        for p in partidas_con_elo_soloq
    ]

    partidas_con_elo_flexq = sorted(
        [p for p in historial_total if p.get('queue_id') == 440 and p.get('post_game_valor_clasificacion') is not None],
        key=lambda x: x.get('game_end_timestamp', 0)
    )
    perfil['elo_history_flexq'] = [
        {'timestamp': p['game_end_timestamp'], 'elo': p['post_game_valor_clasificacion']}
        for p in partidas_con_elo_flexq
    ]

    # --- END of new ELO History Logic ---


    if 'soloq' in perfil:
        partidas_soloq = [p for p in historial_total if p.get('queue_id') == 420]
        rachas_soloq = calcular_rachas(partidas_soloq)
        perfil['soloq'].update(rachas_soloq)
        print(f"[_get_player_profile_data] Rachas SoloQ calculadas para {game_name}.")

    if 'flexq' in perfil:
        partidas_flexq = [p for p in historial_total if p.get('queue_id') == 440]
        rachas_flexq = calcular_rachas(partidas_flexq)
        perfil['flexq'].update(rachas_flexq)
        print(f"[_get_player_profile_data] Rachas FlexQ calculadas para {game_name}.")

    # --- Champion Specific Stats ---
    champion_stats = {}
    for match in historial_total:
        champion_name = match.get('champion_name')
        if champion_name and champion_name != "Desconocido":
            if champion_name not in champion_stats:
                champion_stats[champion_name] = {
                    'games_played': 0,
                    'wins': 0,
                    'losses': 0,
                    'kills': 0,
                    'deaths': 0,
                    'assists': 0
                }
            
            stats = champion_stats[champion_name]
            stats['games_played'] += 1
            if match.get('win'):
                stats['wins'] += 1
            else:
                stats['losses'] += 1
            
            stats['kills'] += match.get('kills', 0)
            stats['deaths'] += match.get('deaths', 0)
            stats['assists'] += match.get('assists', 0)

    for champ, stats in champion_stats.items():
        stats['win_rate'] = (stats['wins'] / stats['games_played'] * 100) if stats['games_played'] > 0 else 0
        stats['kda'] = (stats['kills'] + stats['assists']) / max(1, stats['deaths'])

    perfil['champion_stats'] = sorted(champion_stats.items(), key=lambda x: x[1]['games_played'], reverse=True)

    perfil['historial_partidas'].sort(key=lambda x: x.get('game_end_timestamp', 0), reverse=True)
    print(f"[_get_player_profile_data] Perfil de {game_name} preparado.")
    return perfil