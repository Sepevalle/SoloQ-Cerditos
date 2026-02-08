"""
Constantes globales y estructuras de datos inmutables del proyecto.
"""

# ============================================================================
# ICONOS PARA RÉCORDS
# ============================================================================

RECORD_ICONS = {
    'longest_game': 'fas fa-clock',
    'most_kills': 'fas fa-skull-crossbones',
    'most_deaths': 'fas fa-skull',
    'most_assists': 'fas fa-hands-helping',
    'highest_kda': 'fas fa-star',
    'most_cs': 'fas fa-tractor',
    'most_damage_dealt': 'fas fa-fire',
    'most_gold_earned': 'fas fa-coins',
    'most_vision_score': 'fas fa-eye',
    'largest_killing_spree': 'fas fa-fire-alt',
    'largest_multikill': 'fas fa-bolt',
    'most_time_spent_dead': 'fas fa-bed',
    'most_wards_placed': 'fas fa-map-marker-alt',
    'most_wards_killed': 'fas fa-eye-slash',
    'most_turret_kills': 'fas fa-chess-rook',
    'most_inhibitor_kills': 'fas fa-chess-queen',
    'most_baron_kills': 'fas fa-dragon',
    'most_dragon_kills': 'fas fa-dragon',
    'most_damage_taken': 'fas fa-shield-alt',
    'most_total_heal': 'fas fa-heart',
    'most_damage_shielded_on_teammates': 'fas fa-shield-virus',
    'most_time_ccing_others': 'fas fa-hand-paper',
    'most_objectives_stolen': 'fas fa-hand-rock',
    'highest_kill_participation': 'fas fa-users',
    'most_double_kills': 'fas fa-angle-double-up',
    'most_triple_kills': 'fas fa-angle-double-up',
    'most_quadra_kills': 'fas fa-angle-double-up',
    'most_penta_kills': 'fas fa-trophy',
    'longest_win_streak': 'fas fa-trophy',
    'longest_loss_streak': 'fas fa-arrow-down',
}

# ============================================================================
# NOMBRES DE RÉCORDS PARA DISPLAY
# ============================================================================

RECORD_DISPLAY_NAMES = {
    'longest_game': 'Partida Más Larga',
    'most_kills': 'Más Asesinatos',
    'most_deaths': 'Más Muertes',
    'most_assists': 'Más Asistencias',
    'highest_kda': 'Mejor KDA',
    'most_cs': 'Más CS',
    'most_damage_dealt': 'Más Daño Infligido',
    'most_gold_earned': 'Más Oro Ganado',
    'most_vision_score': 'Mayor Puntuación de Visión',
    'largest_killing_spree': 'Mayor Racha de Asesinatos',
    'largest_multikill': 'Mayor Multikill',
    'most_time_spent_dead': 'Más Tiempo Muerto',
    'most_wards_placed': 'Más Guardianes Colocados',
    'most_wards_killed': 'Más Guardianes Destruidos',
    'most_turret_kills': 'Más Torres Destruidas',
    'most_inhibitor_kills': 'Más Inhibidores Destruidos',
    'most_baron_kills': 'Más Barones',
    'most_dragon_kills': 'Más Dragones',
    'most_damage_taken': 'Más Daño Recibido',
    'most_total_heal': 'Más Curación',
    'most_damage_shielded_on_teammates': 'Más Escudos a Aliados',
    'most_time_ccing_others': 'Más Tiempo CC',
    'most_objectives_stolen': 'Más Objetivos Robados',
    'highest_kill_participation': 'Mayor Participación',
    'most_double_kills': 'Más Doble Kills',
    'most_triple_kills': 'Más Triple Kills',
    'most_quadra_kills': 'Más Quadra Kills',
    'most_penta_kills': 'Más Penta Kills',
    'longest_win_streak': 'Mayor Racha de Victorias',
    'longest_loss_streak': 'Mayor Racha de Derrotas',
}

# ============================================================================
# KEYS DE RÉCORDS PERSONALES
# ============================================================================

PERSONAL_RECORD_KEYS = [
    'longest_game',
    'most_kills',
    'most_deaths',
    'most_assists',
    'highest_kda',
    'most_cs',
    'most_damage_dealt',
    'most_gold_earned',
    'most_vision_score',
    'largest_killing_spree',
    'largest_multikill',
    'most_time_spent_dead',
    'most_wards_placed',
    'most_wards_killed',
    'most_turret_kills',
    'most_inhibitor_kills',
    'most_baron_kills',
    'most_dragon_kills',
    'most_damage_taken',
    'most_total_heal',
    'most_damage_shielded_on_teammates',
    'most_time_ccing_others',
    'most_objectives_stolen',
    'highest_kill_participation',
    'most_double_kills',
    'most_triple_kills',
    'most_quadra_kills',
    'most_penta_kills',
    'longest_win_streak',
    'longest_loss_streak',
]

# ============================================================================
# RÉCORDS DONDE 0 DEBE MOSTRARSE COMO N/A
# ============================================================================

RECORDS_NA_IF_ZERO = [
    'largest_killing_spree',
    'largest_multikill',
    'most_turret_kills',
    'most_inhibitor_kills',
    'most_baron_kills',
    'most_dragon_kills',
    'most_objectives_stolen',
    'most_double_kills',
    'most_triple_kills',
    'most_quadra_kills',
    'most_penta_kills',
]
