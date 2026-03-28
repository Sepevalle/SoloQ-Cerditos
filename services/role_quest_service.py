"""
Utilidades para normalizar y exponer la informacion de Role Quest.

Riot no documenta de forma estable los campos de progreso/completado en match-v5,
asi que aqui conservamos cualquier senal cruda (`missions`, `playerScore*`, etc.)
y la empaquetamos en una estructura consistente para el resto de la app.
"""

from config.settings import SEASON_START_TIMESTAMP

ROLE_QUEST_ENABLED_QUEUE_IDS = {420, 440}
ROLE_QUEST_RELEASE_TIMESTAMP_MS = SEASON_START_TIMESTAMP * 1000
PLAYER_SCORE_KEYS = [f"playerScore{i}" for i in range(12)]
ROLE_QUEST_COMPLETION_SECONDS_KEYS = [
    "role_quest_completion_seconds",
    "quest_completion_seconds",
    "completion_seconds",
    "completion_time_seconds",
    "roleQuestCompletionSeconds",
    "questCompletionSeconds",
]
ROLE_QUEST_COMPLETION_MS_KEYS = [
    "role_quest_completion_ms",
    "quest_completion_ms",
    "completion_ms",
    "completion_time_ms",
    "roleQuestCompletionMs",
    "questCompletionMs",
]
ROLE_QUEST_COMPLETION_TIMESTAMP_KEYS = [
    "role_quest_completion_timestamp_ms",
    "quest_completion_timestamp_ms",
    "completion_timestamp_ms",
    "roleQuestCompletionTimestampMs",
    "questCompletionTimestampMs",
]

ROLE_LABELS = {
    "TOP": "Top",
    "JUNGLE": "Jungla",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "UTILITY": "Support",
    "UNKNOWN": "Desconocido",
}

ROLE_ALIASES = {
    "MID": "MIDDLE",
    "BOT": "BOTTOM",
    "ADC": "BOTTOM",
    "SUP": "UTILITY",
    "SUPPORT": "UTILITY",
    "NONE": "UNKNOWN",
    "INVALID": "UNKNOWN",
    "": "UNKNOWN",
}

ROLE_QUEST_ICON_SLUGS = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "mid",
    "BOTTOM": "bot",
    "UTILITY": "support",
}

BOOT_ITEM_SUFFIXES = (
    "1001",
    "2422",
    "3005",
    "3006",
    "3009",
    "3020",
    "3047",
    "3111",
    "3117",
    "3158",
)

ROLE_QUEST_PRESENTATION = {
    "TOP": {
        "quest_title": "Mision de la calle superior",
        "reward_items": [
            "Nivel maximo aumentado a 20.",
            "+600 de experiencia y +12.5% de experiencia futura.",
            "Teleport gratis o Teleport mejorado con escudo al aterrizar.",
        ],
    },
    "JUNGLE": {
        "quest_title": "Mision de la jungla",
        "reward_items": [
            "Smite mejorado al completar la quest.",
            "Mas oro y experiencia al limpiar campamentos grandes.",
            "Mejor movilidad dentro de la jungla y el rio.",
        ],
    },
    "MIDDLE": {
        "quest_title": "Mision de la calle central",
        "reward_items": [
            "Mejora de las botas al nivel 3.",
            "Retirada mejorada.",
        ],
    },
    "BOTTOM": {
        "quest_title": "Mision de la calle inferior",
        "reward_items": [
            "+300 de oro al completar la quest.",
            "Oro extra por subditos y por asesinatos o asistencias.",
            "Las botas pasan al slot de Role Quest.",
        ],
    },
    "UTILITY": {
        "quest_title": "Mision de support",
        "reward_items": [
            "Objeto de support mejorado.",
            "Control wards mas baratas.",
            "Hasta 2 control wards en el slot de Role Quest.",
        ],
    },
}

STATUS_META = {
    "captured": {
        "label": "Campos Riot",
        "variant": "success",
        "note": "La API devolvio senales crudas de Role Quest en esta partida.",
    },
    "refresh_needed": {
        "label": "Recapturar",
        "variant": "warning",
        "note": "La partida ya pertenece a la era de Role Quest, pero este historial no conserva aun los campos crudos. Conviene refrescar el historial.",
    },
    "position_only": {
        "label": "Solo rol",
        "variant": "secondary",
        "note": "Solo hay informacion de posicion; no hay progreso crudo de Role Quest guardado.",
    },
    "missing": {
        "label": "Sin datos",
        "variant": "dark",
        "note": "No hay suficientes campos para reconstruir Role Quest en este match.",
    },
}


def normalize_role_name(value):
    """Normaliza nombres de rol entre variantes de Riot y aliases comunes."""
    normalized = str(value or "").strip().upper()
    normalized = ROLE_ALIASES.get(normalized, normalized)
    return normalized if normalized in ROLE_LABELS else "UNKNOWN"


def get_role_label(value):
    """Retorna la etiqueta legible del rol."""
    return ROLE_LABELS.get(normalize_role_name(value), ROLE_LABELS["UNKNOWN"])


def _is_simple_value(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _clean_simple_mapping(payload):
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if _is_simple_value(value)
    }


def _extract_missions(source, existing_role_quest):
    missions = source.get("missions")
    if not isinstance(missions, dict):
        missions = existing_role_quest.get("missions")
    return _clean_simple_mapping(missions)


def _extract_player_scores(source, existing_role_quest, missions):
    scores = {}
    role_scores = existing_role_quest.get("player_scores")
    role_scores = role_scores if isinstance(role_scores, dict) else {}

    top_level_scores = source.get("player_scores")
    top_level_scores = top_level_scores if isinstance(top_level_scores, dict) else {}

    for key in PLAYER_SCORE_KEYS:
        value = None
        if key in source and _is_simple_value(source.get(key)):
            value = source.get(key)
        elif key in top_level_scores and _is_simple_value(top_level_scores.get(key)):
            value = top_level_scores.get(key)
        elif key in missions and _is_simple_value(missions.get(key)):
            value = missions.get(key)
        elif key in role_scores and _is_simple_value(role_scores.get(key)):
            value = role_scores.get(key)

        if value is not None:
            scores[key] = value

    return scores


def _extract_candidate_keys(source, existing_role_quest):
    keys = set()
    for key in source.keys():
        key_lower = str(key).lower()
        if key in PLAYER_SCORE_KEYS or "quest" in key_lower or "mission" in key_lower:
            keys.add(str(key))

    for key in existing_role_quest.get("candidate_keys", []):
        keys.add(str(key))

    if existing_role_quest.get("missions"):
        keys.add("missions")
    if existing_role_quest.get("player_scores"):
        keys.add("player_scores")

    return sorted(keys)


def _build_items_list(mapping):
    return [
        {"key": key, "value": value}
        for key, value in mapping.items()
    ]


def _build_non_zero_scores(scores):
    non_zero_values = []
    for key in PLAYER_SCORE_KEYS:
        if key not in scores:
            continue

        value = scores[key]
        if value in (None, False, 0, 0.0, ""):
            continue

        non_zero_values.append({"key": key, "value": value})

    return non_zero_values


def _build_tooltip(assigned_role_label, played_role_label, status_label, missions, scores):
    tooltip_parts = [f"Estado: {status_label}"]
    if assigned_role_label != ROLE_LABELS["UNKNOWN"]:
        tooltip_parts.append(f"Rol asignado: {assigned_role_label}")
    if played_role_label != ROLE_LABELS["UNKNOWN"]:
        tooltip_parts.append(f"Rol jugado: {played_role_label}")
    if scores:
        tooltip_parts.append(f"playerScore*: {len(scores)}")
    if missions:
        tooltip_parts.append(f"missions: {len(missions)}")
    return " | ".join(tooltip_parts)


def _default_display_payload():
    return {
        "showable": False,
        "icon_slug": None,
        "uses_boots_icon": False,
        "boots_item_id": None,
        "completed": False,
        "label": "Role Quest",
        "subtitle": "Sin datos",
        "asset_alt": "Role Quest",
        "quest_title": "Role Quest",
        "reward_items": [],
    }


def _extract_item_ids(source, existing_role_quest):
    for payload in (source, existing_role_quest):
        if not isinstance(payload, dict):
            continue

        for key in ("player_items", "items"):
            items = payload.get(key)
            if not isinstance(items, list):
                continue

            normalized = []
            for item in items:
                try:
                    normalized.append(int(item or 0))
                except (TypeError, ValueError):
                    normalized.append(0)
            return normalized

    return []


def _extract_boots_item_id(items):
    fallback_boots = None

    for item_id in items:
        if not item_id:
            continue

        item_text = str(item_id)
        for suffix in BOOT_ITEM_SUFFIXES:
            if not item_text.endswith(suffix):
                continue

            normalized_boots = int(suffix)
            if normalized_boots != 1001:
                return normalized_boots

            if fallback_boots is None:
                fallback_boots = normalized_boots
            break

    return fallback_boots


def _build_role_quest_display(
    assigned_role,
    status,
    status_label,
    completion_seconds,
    completion_time_label,
    source,
    existing_role_quest,
):
    icon_slug = ROLE_QUEST_ICON_SLUGS.get(assigned_role)
    if not icon_slug:
        return _default_display_payload()

    items = _extract_item_ids(source, existing_role_quest)
    boots_item_id = _extract_boots_item_id(items) if assigned_role == "BOTTOM" else None
    completed = completion_seconds is not None

    if completed:
        subtitle = completion_time_label or "Completada"
    elif status == "refresh_needed":
        subtitle = "Sin capturar"
    elif status in {"captured", "position_only"}:
        subtitle = "Pendiente"
    else:
        subtitle = status_label

    role_label = get_role_label(assigned_role)
    presentation = ROLE_QUEST_PRESENTATION.get(assigned_role, {})
    return {
        "showable": True,
        "icon_slug": icon_slug,
        "uses_boots_icon": assigned_role == "BOTTOM" and boots_item_id is not None,
        "boots_item_id": boots_item_id,
        "completed": completed,
        "label": role_label,
        "subtitle": subtitle,
        "asset_alt": f"Role Quest {role_label}",
        "quest_title": presentation.get("quest_title", f"Role Quest {role_label}"),
        "reward_items": presentation.get("reward_items", []),
    }


def _coerce_positive_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _find_numeric_value(payloads, keys):
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in keys:
            if key in payload:
                numeric = _coerce_positive_number(payload.get(key))
                if numeric is not None:
                    return numeric, key
    return None, None


def _format_duration_label(seconds):
    if not seconds:
        return None
    total_seconds = int(round(seconds))
    minutes = total_seconds // 60
    remaining_seconds = total_seconds % 60
    return f"{minutes}m {remaining_seconds}s"


def _extract_completion_seconds(source, existing_role_quest, missions, game_end_timestamp):
    payloads = [source, existing_role_quest, missions]

    seconds_value, seconds_key = _find_numeric_value(payloads, ROLE_QUEST_COMPLETION_SECONDS_KEYS)
    if seconds_value is not None:
        return round(seconds_value, 2), seconds_key

    ms_value, ms_key = _find_numeric_value(payloads, ROLE_QUEST_COMPLETION_MS_KEYS)
    if ms_value is not None:
        return round(ms_value / 1000.0, 2), ms_key

    timestamp_value, timestamp_key = _find_numeric_value(payloads, ROLE_QUEST_COMPLETION_TIMESTAMP_KEYS)
    if timestamp_value is not None and game_end_timestamp:
        game_duration = _coerce_positive_number(source.get("game_duration") or existing_role_quest.get("game_duration"))
        if game_duration:
            game_start_timestamp = game_end_timestamp - (game_duration * 1000.0)
            if game_start_timestamp <= timestamp_value <= game_end_timestamp:
                return round((timestamp_value - game_start_timestamp) / 1000.0, 2), timestamp_key

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key, raw_value in payload.items():
            key_lower = str(key).lower()
            if "completion" not in key_lower and not ("quest" in key_lower and "time" in key_lower):
                continue

            numeric = _coerce_positive_number(raw_value)
            if numeric is None:
                continue

            if "timestamp" in key_lower and game_end_timestamp:
                game_duration = _coerce_positive_number(source.get("game_duration") or existing_role_quest.get("game_duration"))
                if not game_duration:
                    continue
                game_start_timestamp = game_end_timestamp - (game_duration * 1000.0)
                if game_start_timestamp <= numeric <= game_end_timestamp:
                    return round((numeric - game_start_timestamp) / 1000.0, 2), str(key)
                continue

            if key_lower.endswith("_ms") or "milli" in key_lower:
                return round(numeric / 1000.0, 2), str(key)

            return round(numeric, 2), str(key)

    return None, None


def build_role_quest_payload(source, queue_id=None, game_end_timestamp=None):
    """
    Construye una vista normalizada de Role Quest a partir de cualquier payload.

    Acepta tanto el participant crudo de Riot como la version ya guardada en JSON.
    """
    if not isinstance(source, dict):
        return {
            "assigned_role": "UNKNOWN",
            "assigned_role_label": ROLE_LABELS["UNKNOWN"],
            "played_role": "UNKNOWN",
            "played_role_label": ROLE_LABELS["UNKNOWN"],
            "status": "missing",
            "status_label": STATUS_META["missing"]["label"],
            "status_variant": STATUS_META["missing"]["variant"],
            "note": STATUS_META["missing"]["note"],
            "tooltip": f"Estado: {STATUS_META['missing']['label']}",
            "feature_expected": False,
            "captured_from_api": False,
            "position_mismatch": False,
            "missions_present": False,
            "missions": {},
            "missions_items": [],
            "player_scores_present": False,
            "player_scores": {},
            "player_scores_count": 0,
            "non_zero_scores": [],
            "candidate_keys": [],
            "display": _default_display_payload(),
        }

    existing_role_quest = source.get("role_quest")
    existing_role_quest = existing_role_quest if isinstance(existing_role_quest, dict) else {}

    effective_queue_id = queue_id if queue_id is not None else source.get("queue_id")
    effective_timestamp = (
        game_end_timestamp
        if game_end_timestamp is not None
        else source.get("game_end_timestamp", 0)
    ) or 0

    raw_assigned_role = (
        source.get("team_position")
        or source.get("teamPosition")
        or existing_role_quest.get("assigned_role")
    )
    raw_played_role = (
        source.get("individual_position")
        or source.get("individualPosition")
        or existing_role_quest.get("played_role")
    )

    assigned_role = normalize_role_name(raw_assigned_role or raw_played_role)
    played_role = normalize_role_name(raw_played_role)

    assigned_role_label = get_role_label(assigned_role)
    played_role_label = get_role_label(played_role)

    missions = _extract_missions(source, existing_role_quest)
    player_scores = _extract_player_scores(source, existing_role_quest, missions)
    non_zero_scores = _build_non_zero_scores(player_scores)
    candidate_keys = _extract_candidate_keys(source, existing_role_quest)
    completion_seconds, completion_source_key = _extract_completion_seconds(
        source,
        existing_role_quest,
        missions,
        effective_timestamp,
    )

    feature_expected = (
        effective_queue_id in ROLE_QUEST_ENABLED_QUEUE_IDS
        and effective_timestamp >= ROLE_QUEST_RELEASE_TIMESTAMP_MS
    )
    has_raw_tracking = bool(missions or player_scores)
    has_position_info = assigned_role != "UNKNOWN" or played_role != "UNKNOWN"
    position_mismatch = (
        assigned_role != "UNKNOWN"
        and played_role != "UNKNOWN"
        and assigned_role != played_role
    )

    if has_raw_tracking:
        status = "captured"
    elif feature_expected and has_position_info:
        status = "refresh_needed"
    elif has_position_info:
        status = "position_only"
    else:
        status = "missing"

    status_meta = STATUS_META[status]
    display = _build_role_quest_display(
        assigned_role,
        status,
        status_meta["label"],
        completion_seconds,
        _format_duration_label(completion_seconds),
        source,
        existing_role_quest,
    )

    return {
        "assigned_role": assigned_role,
        "assigned_role_label": assigned_role_label,
        "assigned_role_inferred": not raw_assigned_role and assigned_role != "UNKNOWN",
        "played_role": played_role,
        "played_role_label": played_role_label,
        "status": status,
        "status_label": status_meta["label"],
        "status_variant": status_meta["variant"],
        "note": status_meta["note"],
        "tooltip": _build_tooltip(
            assigned_role_label,
            played_role_label,
            status_meta["label"],
            missions,
            player_scores,
        ),
        "feature_expected": feature_expected,
        "captured_from_api": has_raw_tracking,
        "completion_available": completion_seconds is not None,
        "completion_seconds": completion_seconds,
        "completion_time_label": _format_duration_label(completion_seconds),
        "completion_source_key": completion_source_key,
        "position_mismatch": position_mismatch,
        "missions_present": bool(missions),
        "missions": missions,
        "missions_items": _build_items_list(missions),
        "player_scores_present": bool(player_scores),
        "player_scores": player_scores,
        "player_scores_count": len(player_scores),
        "non_zero_scores": non_zero_scores,
        "candidate_keys": candidate_keys,
        "display": display,
    }


def enrich_participant_role_quest(participant, queue_id=None, game_end_timestamp=None):
    """Anade estructura normalizada de Role Quest a un participante."""
    if not isinstance(participant, dict):
        return participant

    role_quest = build_role_quest_payload(
        participant,
        queue_id=queue_id,
        game_end_timestamp=game_end_timestamp,
    )

    participant["team_position"] = participant.get("team_position") or participant.get("teamPosition") or (
        "" if role_quest["assigned_role"] == "UNKNOWN" else role_quest["assigned_role"]
    )
    participant["individual_position"] = participant.get("individual_position") or participant.get("individualPosition") or (
        "" if role_quest["played_role"] == "UNKNOWN" else role_quest["played_role"]
    )
    participant["missions"] = role_quest["missions"]
    participant["player_scores"] = role_quest["player_scores"]
    participant["role_quest_completion_seconds"] = role_quest["completion_seconds"]
    participant["role_quest"] = role_quest
    return participant


def enrich_match_role_quest_data(match):
    """Normaliza y completa datos de Role Quest para una partida completa."""
    if not isinstance(match, dict):
        return match

    queue_id = match.get("queue_id")
    game_end_timestamp = match.get("game_end_timestamp", 0)

    role_quest = build_role_quest_payload(
        match,
        queue_id=queue_id,
        game_end_timestamp=game_end_timestamp,
    )

    match["team_position"] = match.get("team_position") or match.get("teamPosition") or (
        "" if role_quest["assigned_role"] == "UNKNOWN" else role_quest["assigned_role"]
    )
    match["individual_position"] = match.get("individual_position") or match.get("individualPosition") or (
        "" if role_quest["played_role"] == "UNKNOWN" else role_quest["played_role"]
    )
    match["missions"] = role_quest["missions"]
    match["player_scores"] = role_quest["player_scores"]
    match["role_quest_completion_seconds"] = role_quest["completion_seconds"]
    match["role_quest"] = role_quest

    participants = match.get("all_participants") or []
    if isinstance(participants, list):
        match["all_participants"] = [
            enrich_participant_role_quest(
                participant if isinstance(participant, dict) else participant,
                queue_id=queue_id,
                game_end_timestamp=game_end_timestamp,
            )
            for participant in participants
        ]

    return match


def normalize_match_history_role_quest(historial_data):
    """Aplica normalizacion de Role Quest a todo un historial de partidas."""
    if not isinstance(historial_data, dict):
        return historial_data

    matches = historial_data.get("matches")
    if not isinstance(matches, list):
        return historial_data

    normalized = dict(historial_data)
    normalized["matches"] = [
        enrich_match_role_quest_data(match if isinstance(match, dict) else match)
        for match in matches
    ]
    return normalized
