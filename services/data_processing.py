from datetime import datetime, timedelta, timezone
import time

def calculate_lp_change_robust(match, all_matches_for_player, player_queue_lp_history):
    """
    Calcula el cambio de LP para una partida de forma robusta y consistente.
    Usa la lógica unificada de anclajes y propagación de ELO.

    Args:
        match (dict): La partida para la que calcular el cambio de LP.
        all_matches_for_player (list): Todas las partidas del jugador en la temporada actual.
        player_queue_lp_history (list): Snapshots de LP/ELO para la cola específica.

    Returns:
        tuple: (lp_change, elo_before, elo_after) donde cualquiera puede ser None si no se puede determinar.
    """
    game_end_ts = match.get('game_end_timestamp', 0)
    queue_id = match.get('queue_id')
    match_id = match.get('match_id')

    if not game_end_ts or queue_id not in [420, 440]:
        return None, None, None

    # Mapear cola
    queue_name = "RANKED_SOLO_5x5" if queue_id == 420 else "RANKED_FLEX_SR"
    mock_history = {queue_name: player_queue_lp_history or []}
    
    # Procesar la lista completa de partidas del jugador para obtener la cadena coherente
    processed = process_player_match_history([m for m in all_matches_for_player if m.get('queue_id') == queue_id], mock_history)
    for m in processed:
        if m.get('match_id') == match_id:
            return m.get('lp_change_this_game'), m.get('pre_game_valor_clasificacion'), m.get('post_game_valor_clasificacion')

    return None, None, None


def calculate_lp_change(match, all_matches_for_player, player_queue_lp_history):
    """
    Mantener compatibilidad hacia atrás con el nombre anterior.
    """
    return calculate_lp_change_robust(match, all_matches_for_player, player_queue_lp_history)


def _distribute_interval_lp(matches_in_interval, elo_start, elo_end):
    """
    Distribuye un cambio de ELO total (elo_end - elo_start) entre una lista de partidas
    cronológicamente ordenadas (de más antigua a más reciente), respetando victorias y derrotas.
    
    Garantiza:
    1. Las victorias tienen cambio de LP positivo (o 0 en casos extremos).
    2. Las derrotas tienen cambio de LP negativo (o 0 si escudo de descenso).
    3. Para cada partida j: match[j].post == match[j+1].pre.
    4. La primera partida empieza en elo_start y la última termina en elo_end.
    """
    m_count = len(matches_in_interval)
    if m_count == 0:
        return
    
    total_delta = elo_end - elo_start

    # Caso simple: 1 sola partida
    if m_count == 1:
        single_match = matches_in_interval[0]
        single_match['lp_change_this_game'] = int(round(total_delta))
        single_match['pre_game_valor_clasificacion'] = int(round(elo_start))
        single_match['post_game_valor_clasificacion'] = int(round(elo_end))
        return

    # Múltiples partidas: contar victorias y derrotas
    wins = [i for i, m in enumerate(matches_in_interval) if m.get('win', False)]
    losses = [i for i, m in enumerate(matches_in_interval) if not m.get('win', False)]
    w = len(wins)
    l = len(losses)

    lp_changes = [0] * m_count

    if l == 0:
        # Solo victorias
        base = total_delta // w if w > 0 else 20
        rem = total_delta % w if w > 0 else 0
        for idx in range(m_count):
            lp_changes[idx] = int(base + (1 if idx < rem else 0))
    elif w == 0:
        # Solo derrotas
        base = total_delta // l if l > 0 else -19
        rem = total_delta % l if l > 0 else 0
        for idx in range(m_count):
            lp_changes[idx] = int(base + (1 if idx < rem else 0))
    else:
        # Mezcla de victorias y derrotas
        # En League of Legends el promedio base de victoria es ~+20 y derrota ~-20
        # Ecuación: w * g - l * p = total_delta
        expected_at_base = 20 * (w - l)
        discrepancy = total_delta - expected_at_base
        adj_per_match = round(discrepancy / m_count)
        
        g = max(12, min(38, int(round(20 + adj_per_match))))
        p = max(12, min(38, int(round(20 - adj_per_match))))

        for i in wins:
            lp_changes[i] = g
        for i in losses:
            lp_changes[i] = -p

        # Ajustar diferencia para que la suma sea exactamente total_delta
        current_sum = sum(lp_changes)
        diff = total_delta - current_sum
        if diff != 0:
            # Distribuir la diferencia
            step = 1 if diff > 0 else -1
            cand_indices = wins if diff > 0 else losses
            if not cand_indices:
                cand_indices = list(range(m_count))
            idx_i = 0
            while diff != 0:
                target_idx = cand_indices[idx_i % len(cand_indices)]
                lp_changes[target_idx] += step
                diff -= step
                idx_i += 1

    # Asignar pre y post encadenados
    current_elo = elo_start
    for idx, match in enumerate(matches_in_interval):
        match['pre_game_valor_clasificacion'] = int(round(current_elo))
        match['lp_change_this_game'] = int(round(lp_changes[idx]))
        current_elo += lp_changes[idx]
        match['post_game_valor_clasificacion'] = int(round(current_elo))

    # Asegurar que el último post_game coincida exactamente con elo_end
    matches_in_interval[-1]['post_game_valor_clasificacion'] = int(round(elo_end))
    matches_in_interval[-1]['lp_change_this_game'] = int(round(
        matches_in_interval[-1]['post_game_valor_clasificacion'] - matches_in_interval[-1]['pre_game_valor_clasificacion']
    ))


def process_player_match_history(matches, player_lp_history, current_elo_dict=None):
    """
    Procesa el historial de partidas de un jugador para calcular cambios de LP y ELO
    utilizando puntos de anclaje (snapshots históricos y ELO actual) y propagación coherente.

    Args:
        matches (list): Lista de partidas del jugador.
        player_lp_history (dict): Diccionario de snapshots de LP indexado por nombre de cola.
        current_elo_dict (dict, optional): ELO actual por cola {'RANKED_SOLO_5x5': elo, 'RANKED_FLEX_SR': elo}.

    Returns:
        list: Lista de partidas con LP y ELO calculados, ordenada descendente por fecha.
    """
    if not matches:
        return []

    # Copia defensiva superficial de los objetos de partida
    matches_list = [dict(m) for m in matches]

    # Procesar colas clasificatorias de forma independiente
    queue_configs = [
        (420, "RANKED_SOLO_5x5"),
        (440, "RANKED_FLEX_SR")
    ]

    for queue_id, queue_name in queue_configs:
        # Filtrar partidas de esta cola
        q_matches = [m for m in matches_list if m.get('queue_id') == queue_id]
        if not q_matches:
            continue

        # Ordenar cronológicamente (más antigua primero)
        q_matches.sort(key=lambda x: x.get('game_end_timestamp', 0))

        # Obtener snapshots de esta cola
        raw_snapshots = player_lp_history.get(queue_name, []) if player_lp_history else []
        snapshots = sorted([s for s in raw_snapshots if s.get('elo', 0) > 0], key=lambda x: x.get('timestamp', 0))

        # Construir lista de anclajes [(timestamp, elo)]
        anchors = []
        for s in snapshots:
            ts = s.get('timestamp', 0)
            elo = s.get('elo', 0)
            if ts > 0 and elo > 0:
                anchors.append((ts, elo))

        # Si tenemos ELO actual para la cola, agregarlo como anclaje presente
        now_ts = int(time.time() * 1000)
        curr_elo = current_elo_dict.get(queue_name) if current_elo_dict else None
        if curr_elo and curr_elo > 0:
            anchors.append((now_ts, curr_elo))

        # Deduplicar y ordenar anclajes
        anchors.sort(key=lambda a: a[0])
        unique_anchors = []
        for a in anchors:
            if not unique_anchors or unique_anchors[-1][0] != a[0]:
                unique_anchors.append(a)
        anchors = unique_anchors

        # Si no hay anclajes externos, intentar usar alguna partida con ELO ya conocido
        if not anchors:
            for m in q_matches:
                p_elo = m.get('post_game_valor_clasificacion')
                ts = m.get('game_end_timestamp', 0)
                if p_elo and p_elo > 0 and ts > 0:
                    anchors.append((ts, p_elo))
            anchors.sort(key=lambda a: a[0])

        # Si aún no hay anclajes, establecer un anclaje base razonable (Plata/Oro: 1200 o 1400)
        if not anchors:
            first_ts = q_matches[0].get('game_end_timestamp', 0)
            anchors.append((first_ts, 1400))

        # Procesar partidas entre anclajes
        first_anchor = anchors[0]
        last_anchor = anchors[-1]

        # 1. Partidas previas al primer anclaje (encadenar hacia atrás)
        pre_anchor_matches = [m for m in q_matches if m.get('game_end_timestamp', 0) < first_anchor[0]]
        if pre_anchor_matches:
            next_elo = first_anchor[1]
            for m in reversed(pre_anchor_matches):
                is_win = m.get('win', False)
                delta = 20 if is_win else -19
                m['post_game_valor_clasificacion'] = int(round(next_elo))
                m['lp_change_this_game'] = int(round(delta))
                m['pre_game_valor_clasificacion'] = int(round(next_elo - delta))
                next_elo = m['pre_game_valor_clasificacion']

        # 2. Partidas posteriores al último anclaje (encadenar hacia adelante)
        post_anchor_matches = [m for m in q_matches if m.get('game_end_timestamp', 0) > last_anchor[0]]
        if post_anchor_matches:
            prev_elo = last_anchor[1]
            for m in post_anchor_matches:
                is_win = m.get('win', False)
                delta = 20 if is_win else -19
                m['pre_game_valor_clasificacion'] = int(round(prev_elo))
                m['lp_change_this_game'] = int(round(delta))
                m['post_game_valor_clasificacion'] = int(round(prev_elo + delta))
                prev_elo = m['post_game_valor_clasificacion']

        # 3. Partidas comprendidas entre anclajes
        for i in range(len(anchors) - 1):
            a_start = anchors[i]
            a_end = anchors[i + 1]
            
            interval_matches = [
                m for m in q_matches 
                if a_start[0] <= m.get('game_end_timestamp', 0) <= a_end[0]
            ]
            
            if interval_matches:
                _distribute_interval_lp(interval_matches, a_start[1], a_end[1])

        # Si alguna partida por alguna razón no cayó en los rangos anteriores, asignar coherencia
        for i, m in enumerate(q_matches):
            if m.get('lp_change_this_game') is None or m.get('post_game_valor_clasificacion') is None:
                # Usar partida anterior o posterior como referencia
                if i > 0 and q_matches[i - 1].get('post_game_valor_clasificacion'):
                    prev_post = q_matches[i - 1]['post_game_valor_clasificacion']
                    delta = 20 if m.get('win') else -19
                    m['pre_game_valor_clasificacion'] = prev_post
                    m['lp_change_this_game'] = delta
                    m['post_game_valor_clasificacion'] = prev_post + delta
                else:
                    delta = 20 if m.get('win') else -19
                    base = first_anchor[1]
                    m['pre_game_valor_clasificacion'] = base - delta
                    m['lp_change_this_game'] = delta
                    m['post_game_valor_clasificacion'] = base

    # Reintegrar las partidas ordenadas de más reciente a más antigua
    matches_list.sort(key=lambda x: x.get('game_end_timestamp', 0) if x.get('game_end_timestamp') else 0, reverse=True)
    return matches_list

# ===== FUNCIONES OPTIMIZADAS PARA ESTADÍSTICAS GLOBALES =====
import threading
import time

# Caché global para estadísticas compiladas
GLOBAL_STATS_CACHE = {
    'data': None,
    'timestamp': 0,
    'lock': threading.Lock(),
    'cache_timeout': 5 * 60  # 5 minutos
}

def invalidate_global_stats_cache():
    """Invalida el caché de estadísticas globales cuando se actualiza un jugador."""
    with GLOBAL_STATS_CACHE['lock']:
        GLOBAL_STATS_CACHE['data'] = None
        GLOBAL_STATS_CACHE['timestamp'] = 0
    print("[invalidate_global_stats_cache] Caché de estadísticas globales invalidado")


def get_cached_global_stats():
    """Obtiene estadísticas globales del caché si están disponibles y frescas."""
    with GLOBAL_STATS_CACHE['lock']:
        if (GLOBAL_STATS_CACHE['data'] is not None and 
            time.time() - GLOBAL_STATS_CACHE['timestamp'] < GLOBAL_STATS_CACHE['cache_timeout']):
            print("[get_cached_global_stats] Devolviendo estadísticas del caché")
            return GLOBAL_STATS_CACHE['data']
    return None


def cache_global_stats(stats_data):
    """Guarda estadísticas globales en caché."""
    with GLOBAL_STATS_CACHE['lock']:
        GLOBAL_STATS_CACHE['data'] = stats_data
        GLOBAL_STATS_CACHE['timestamp'] = time.time()
    print("[cache_global_stats] Estadísticas globales cacheadas")


def filter_matches_by_queue(matches, queue_id):
    """Filtra partidas por ID de cola."""
    if queue_id == 'all':
        return matches
    return [m for m in matches if str(m.get('queue_id')) == str(queue_id)]


def filter_matches_by_champion(matches, champion_name):
    """Filtra partidas por nombre de campeón."""
    if champion_name == 'all':
        return matches
    return [m for m in matches if m.get('champion_name') == champion_name]


def calculate_player_stats_from_matches(player_name, matches):
    """Calcula estadísticas básicas de un jugador desde sus partidas."""
    if not matches:
        return {'summonerName': player_name, 'total_partidas': 0, 'win_rate': 0}
    
    wins = sum(1 for m in matches if m.get('win'))
    total = len(matches)
    win_rate = (wins / total * 100) if total > 0 else 0
    
    return {
        'summonerName': player_name,
        'total_partidas': total,
        'win_rate': win_rate
    }


def get_top_champions(matches, limit=5):
    """Obtiene los campeones más jugados desde las partidas."""
    from collections import Counter
    if not matches:
        return []
    
    champion_counts = Counter([m.get('champion_name') for m in matches if m.get('champion_name')])
    return champion_counts.most_common(limit)


def extract_global_records(all_matches):
    """Extrae records globales de las partidas de forma eficiente."""
    records = {
        'Más Asesinatos': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull-crossbones'},
        'Más Muertes': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-skull'},
        'Más Asistencias': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-hands-helping'},
        'Mejor KDA': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-star'},
        'Más CS': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-tractor'},
        'Mayor Puntuación de Visión': {'value': 0, 'player': '', 'champion': '', 'icon': 'fas fa-eye'}
    }
    
    for player_name, match in all_matches:
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
    
    return records
