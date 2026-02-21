"""
Servicio de integración con Gemini AI para análisis de partidas.
"""

import json
import time
import re
from datetime import datetime

# Import opcional de Gemini
try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False
    print("[ai_service] Advertencia: Librería google-genai no disponible")

# Import opcional de pydantic
try:
    from pydantic import BaseModel
    PYDANTIC_AVAILABLE = True
except ImportError:
    BaseModel = None
    PYDANTIC_AVAILABLE = False
    print("[ai_service] Advertencia: Librería pydantic no disponible")

from config.settings import GOOGLE_API_KEY, GEMINI_MODEL, GEMINI_MODELS
from services.github_service import read_analysis, save_analysis, read_player_permission, save_player_permission


# Guardrails de tamaño de prompt para evitar excesos de tokens.
# Estimación rápida: ~4 caracteres por token.
MAX_PROMPT_CHARS_MATCH_DETAIL = 200_000
MAX_TIMELINE_CHARS_MATCH_DETAIL = 120_000


# Esquema para respuesta de Gemini (solo si pydantic está disponible)
if PYDANTIC_AVAILABLE:
    class AnalisisSoloQ(BaseModel):
        analisis_individual: str
        valoracion_companeros: str
        valoracion_rivales: str
        aspectos_mejora: str
        puntos_fuertes: str
        recomendaciones: str
        otros: str
else:
    AnalisisSoloQ = None


# Cliente de Gemini (inicializado lazy)
_gemini_client = None


def get_gemini_client():
    """Obtiene o inicializa el cliente de Gemini."""
    global _gemini_client
    if not GENAI_AVAILABLE:
        return None
    if _gemini_client is None and GOOGLE_API_KEY:
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


def _get_model_fallback_list():
    """
    Construye la lista de modelos en orden de prioridad.
    Si no hay lista explícita, usa GEMINI_MODEL como fallback único.
    """
    models = []
    try:
        for m in GEMINI_MODELS:
            if m and str(m).strip():
                models.append(str(m).strip())
    except Exception:
        pass

    if not models and GEMINI_MODEL:
        models = [GEMINI_MODEL]

    # Deduplicar preservando orden
    seen = set()
    unique = []
    for m in models:
        if m not in seen:
            unique.append(m)
            seen.add(m)
    return unique


def _generate_with_model_fallback(client, contents, config=None):
    """
    Ejecuta generate_content intentando modelos en cascada hasta uno exitoso.
    """
    models = _get_model_fallback_list()
    if not models:
        raise RuntimeError("No hay modelos Gemini configurados.")

    last_error = None
    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config
            )
            return response, model_name
        except Exception as e:
            last_error = e
            print(f"[ai_service] Modelo fallido: {model_name} -> {e}")
            continue

    raise last_error if last_error else RuntimeError("Fallaron todos los modelos configurados.")


def check_player_permission(puuid):
    """
    Verifica si un jugador tiene permiso para usar el análisis de IA.
    Si no existe el archivo, lo crea con permiso SI por defecto.
    Auto-rehabilita después de 24h.
    
    Returns:
        tuple: (tiene_permiso, sha, contenido_completo, segundos_restantes)
    """
    return read_player_permission(puuid)



def block_player_permission(puuid, sha=None, force_mode=False):
    """
    Bloquea el permiso de un jugador después de usar el análisis.
    Registra el timestamp para rehabilitación automática después de 24h.
    
    Args:
        puuid: ID del jugador
        sha: SHA del archivo existente
        force_mode: Si True, marca como modo forzado (permite saltarse el cooldown)
    """
    ahora = time.time()
    proxima_disponible = ahora + (24 * 3600)  # 24 horas en segundos
    
    content = {
        "permitir_llamada": "NO",
        "razon": "Llamada consumida. Disponible nuevamente en 24h." if not force_mode else "Análisis forzado manualmente.",
        "ultima_llamada": ahora,
        "proxima_llamada_disponible": proxima_disponible,
        "modo_forzado": force_mode,
        "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return save_player_permission(puuid, content, sha)


def get_time_until_next_analysis(puuid):
    """
    Obtiene el tiempo restante hasta el próximo análisis disponible.
    
    Returns:
        dict: Información sobre disponibilidad del análisis
    """
    tiene_permiso, _, content, segundos_restantes = read_player_permission(puuid)
    
    ahora = time.time()
    ultima_llamada = content.get("ultima_llamada", 0)
    proxima_disponible = content.get("proxima_llamada_disponible", 0)
    modo_forzado = content.get("modo_forzado", False)
    
    # Calcular tiempo restante
    if segundos_restantes <= 0:
        tiempo_restante_texto = "Disponible ahora"
        disponible = True
    else:
        horas = int(segundos_restantes // 3600)
        minutos = int((segundos_restantes % 3600) // 60)
        if horas > 0:
            tiempo_restante_texto = f"{horas}h {minutos}m"
        else:
            tiempo_restante_texto = f"{minutos}m"
        disponible = False
    
    return {
        "disponible": disponible or tiene_permiso,
        "segundos_restantes": int(segundos_restantes),
        "tiempo_restante_texto": tiempo_restante_texto,
        "ultima_llamada": ultima_llamada,
        "proxima_disponible": proxima_disponible,
        "modo_forzado": modo_forzado,
        "puede_forzar": segundos_restantes > 0 and not modo_forzado
    }


def force_enable_permission(puuid):
    """
    Fuerza el permiso a SI para saltarse el cooldown de 24h.
    Esto permite análisis manual sin esperar.
    
    Returns:
        bool: True si se habilitó correctamente
    """
    tiene_permiso, sha, content, _ = read_player_permission(puuid)
    
    content["permitir_llamada"] = "SI"
    content["razon"] = "Habilitado manualmente (forzado)"
    content["modo_forzado"] = True
    content["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    return save_player_permission(puuid, content, sha)



def get_cached_analysis(puuid):
    """
    Obtiene el análisis cacheado de un jugador si existe.
    
    Returns:
        tuple: (analisis_dict, sha) o (None, None)
    """
    return read_analysis(puuid)


def generate_match_signature(matches):
    """
    Genera una firma única basada en los IDs de partidas.
    """
    match_ids = sorted([str(m.get("match_id")) for m in matches if m.get("match_id")])
    return "-".join(match_ids)


def analyze_matches(puuid, matches, player_name=None):
    """
    Analiza las partidas de un jugador usando Gemini AI.
    
    Args:
        puuid: PUUID del jugador
        matches: Lista de partidas a analizar (deberían ser ~10 de SoloQ)
        player_name: Nombre del jugador para personalizar el análisis
    
    Returns:
        dict: Resultado del análisis con metadata
    """
    client = get_gemini_client()
    if not client:
        return {"error": "Gemini no configurado o librería no disponible"}, 500
    
    if not matches:
        return {"error": "No hay partidas para analizar"}, 404
    
    # Generar firma actual
    current_signature = generate_match_signature(matches)
    
    # Verificar análisis previo
    prev_analysis, sha = get_cached_analysis(puuid)
    if prev_analysis:
        prev_signature = prev_analysis.get("signature", "")
        if prev_signature == current_signature:
            # Mismo análisis, devolver cacheado
            return _add_metadata(prev_analysis["data"], prev_analysis.get("timestamp", 0))
    
    # Preparar datos para el prompt (simplificados)
    partidas_para_ia = []
    for m in matches[:5]:  # Máximo 10 partidas
        partidas_para_ia.append({
            "champion": m.get("champion_name"),
            "win": m.get("win"),
            "kills": m.get("kills"),
            "deaths": m.get("deaths"),
            "assists": m.get("assists"),
            "kda": round((m.get("kills", 0) + m.get("assists", 0)) / max(1, m.get("deaths", 0)), 2),
            "damage_dealt": m.get("total_damage_dealt_to_champions"),
            "gold_earned": m.get("gold_earned"),
            "vision_score": m.get("vision_score"),
            "cs": m.get("total_minions_killed", 0) + m.get("neutral_minions_killed", 0),
            "queue_id": m.get("queue_id")
        })
    
    # Construir prompt
    prompt = (
        f"Analiza profundamente estas {len(partidas_para_ia)} partidas de League of Legends "
        f"para el jugador {player_name or puuid}. "
        "Evalúa su desempeño considerando: "
        "1) Consistencia en KDA y daño, "
        "2) Impacto en el juego (visión, objetivos), "
        "3) Evolución entre partidas recientes, "
        "4) Áreas de mejora específicas, "
        "5) Puntos fuertes destacables. "
        "Sé objetivo y constructivo en el análisis, pero con un toque critico."
        f"Datos de partidas: {json.dumps(partidas_para_ia)}"
    )
    
    try:
        # Configurar respuesta con schema si pydantic está disponible
        config = {'response_mime_type': 'application/json'}
        if PYDANTIC_AVAILABLE and AnalisisSoloQ:
            config['response_schema'] = AnalisisSoloQ
        
        response, model_used = _generate_with_model_fallback(
            client=client,
            contents=prompt,
            config=config
        )
        
        # Parsear respuesta
        if hasattr(response, 'parsed') and response.parsed:
            if PYDANTIC_AVAILABLE and hasattr(response.parsed, 'dict'):
                result = response.parsed.dict()
            else:
                result = response.parsed
        else:
            # Fallback robusto para respuestas con texto extra o múltiples bloques JSON
            result = _parse_json_response(response.text)
        
        # Limpiar caracteres URL-encoded si existen en los valores
        result = _clean_url_encoded_strings(result)
        
        # Guardar análisis
        timestamp = time.time()
        analysis_doc = {
            "timestamp": timestamp,
            "signature": current_signature,
            "data": result
        }
        save_analysis(puuid, analysis_doc, sha)
        
        final = _add_metadata(result, timestamp)
        final["_metadata"]["model_used"] = model_used
        return final
        
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            return {"error": "Cuota de Gemini agotada. Espera unas horas."}, 429
        raise


def analyze_match_detail(match, timeline_data=None, player_puuid=None, player_name=None):
    """
    Analiza una partida concreta en detalle usando Gemini.

    Args:
        match: Dict con los datos de la partida
        player_puuid: PUUID del jugador principal (opcional)
        player_name: Riot ID o nombre visible del jugador (opcional)

    Returns:
        dict | tuple: dict con análisis o (dict_error, status_code)
    """
    client = get_gemini_client()
    if not client:
        return {"error": "Gemini no configurado o librería no disponible"}, 500

    if not match:
        return {"error": "No hay datos de partida para analizar"}, 404

    participants = match.get("all_participants", []) or []

    def _normalize_name(name):
        if not name:
            return ""
        return str(name).split("#")[0].strip().lower()

    player_data = None
    target_name = _normalize_name(player_name)

    for p in participants:
        if player_puuid and p.get("puuid") == player_puuid:
            player_data = p
            break

    if not player_data and target_name:
        for p in participants:
            if _normalize_name(p.get("summoner_name")) == target_name:
                player_data = p
                break

    if not player_data and participants:
        player_data = participants[0]

    if not player_data:
        return {"error": "No se pudo identificar al jugador en la partida"}, 400

    team_id = player_data.get("team_id")

    def _compact_participant(p):
        return {
            "summoner_name": p.get("summoner_name"),
            "champion": p.get("champion_name"),
            "role": p.get("team_position") or p.get("individual_position"),
            "win": p.get("win"),
            "kills": p.get("kills", 0),
            "deaths": p.get("deaths", 0),
            "assists": p.get("assists", 0),
            "kda": round((p.get("kills", 0) + p.get("assists", 0)) / max(1, p.get("deaths", 0)), 2),
            "damage_to_champions": p.get("total_damage_dealt_to_champions", 0),
            "gold_earned": p.get("gold_earned", 0),
            "vision_score": p.get("vision_score", 0),
            "cs": (p.get("total_minions_killed", 0) or 0) + (p.get("neutral_minions_killed", 0) or 0),
        }

    allies = [_compact_participant(p) for p in participants if p.get("team_id") == team_id and p is not player_data]
    enemies = [_compact_participant(p) for p in participants if p.get("team_id") != team_id]

    player_summary = _compact_participant(player_data)
    player_summary["participant_id"] = player_data.get("participant_id")

    match_summary = {
        "match_id": match.get("match_id"),
        "queue_id": match.get("queue_id"),
        "game_duration_seconds": match.get("game_duration"),
        "game_end_timestamp": match.get("game_end_timestamp"),
        "player": player_summary,
        "allies": allies,
        "enemies": enemies,
    }

    timeline_compact = _build_compact_timeline_payload(timeline_data, max_chars=MAX_TIMELINE_CHARS_MATCH_DETAIL)

    prompt = (
        "Eres un coach experto de League of Legends. "
        "Analiza esta partida concreta con enfoque técnico y accionable. "
        "Debes devolver JSON válido con estas claves exactas: "
        "resumen_partida, lectura_de_linea, impacto_mid_game, impacto_late_game, "
        "errores_clave, aciertos_clave, plan_de_mejora, veredicto_final, "
        "lectura_de_linea_score, impacto_mid_game_score, impacto_late_game_score, veredicto_final_score. "
        "Los campos *_score deben ser numéricos entre 0 y 10 (pueden incluir decimales). "
        "El contenido debe estar en español, directo y útil para mejorar en SoloQ. "
        "Incluye ejemplos concretos basados en estadísticas y eventos de esta partida, sin inventar datos. "
        "Debes priorizar el timeline para reconstruir decisiones, tempo, peleas y objetivos minuto a minuto. "
        f"Resumen de partida: {json.dumps(match_summary, ensure_ascii=False)}\n"
        f"Timeline compacto: {json.dumps(timeline_compact, ensure_ascii=False)}"
    )

    prompt = _truncate_text_to_chars(prompt, MAX_PROMPT_CHARS_MATCH_DETAIL)

    try:
        response, model_used = _generate_with_model_fallback(
            client=client,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        result = _parse_json_response(response.text)
        result = _clean_url_encoded_strings(result)
        result = normalize_match_detail_output(result)

        return {
            "data": result,
            "_metadata": {
                "generated_at": datetime.now().strftime('%d/%m/%Y %H:%M'),
                "source": "gemini_match_detail",
                "model_used": model_used,
                "match_id": match.get("match_id"),
                "timeline_compact_chars": len(json.dumps(timeline_compact, ensure_ascii=False)),
                "prompt_estimated_tokens": _estimate_tokens(prompt),
            },
        }
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            return {"error": "Cuota de Gemini agotada. Espera unas horas."}, 429
        raise


def _parse_json_response(text):
    """
    Parsea respuestas de modelo que deberían ser JSON, tolerando ruido alrededor.
    Soporta:
    - JSON puro
    - Bloques ```json ... ```
    - Texto con JSON + texto adicional
    - Múltiples objetos JSON seguidos (usa el primero válido)
    """
    if not text:
        raise json.JSONDecodeError("Empty response", "", 0)

    expected_keys = {
        "resumen_partida",
        "lectura_de_linea",
        "impacto_mid_game",
        "impacto_late_game",
        "errores_clave",
        "aciertos_clave",
        "plan_de_mejora",
        "veredicto_final",
    }

    def score_candidate(obj):
        if not isinstance(obj, dict):
            return -1
        return len(expected_keys.intersection(set(obj.keys())))

    best_candidate = None
    best_score = -1

    # 1) Intento directo
    try:
        direct = json.loads(text)
        direct_score = score_candidate(direct)
        if direct_score >= best_score:
            best_candidate = direct
            best_score = direct_score
        if best_score >= 3:
            return best_candidate
    except json.JSONDecodeError:
        pass

    # 2) Intentar bloque markdown ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            obj = json.loads(candidate)
            obj_score = score_candidate(obj)
            if obj_score >= best_score:
                best_candidate = obj
                best_score = obj_score
            if best_score >= 3:
                return best_candidate
        except json.JSONDecodeError:
            pass

    # 3) Intentar encontrar primer objeto/array JSON válido en medio del texto
    decoder = json.JSONDecoder()
    start_positions = [i for i, ch in enumerate(text) if ch in "{["]
    for pos in start_positions:
        try:
            obj, _ = decoder.raw_decode(text[pos:])
            obj_score = score_candidate(obj)
            if obj_score >= best_score:
                best_candidate = obj
                best_score = obj_score
        except json.JSONDecodeError:
            continue

    if best_candidate is not None:
        return best_candidate

    # Si no se pudo parsear nada, lanzar error con fragmento para diagnóstico
    snippet = text[:300].replace("\n", "\\n")
    raise json.JSONDecodeError(f"Could not parse model JSON. Snippet: {snippet}", text, 0)


def normalize_match_detail_output(data):
    """
    Normaliza la salida del análisis detallado para que siempre tenga las claves
    esperadas por frontend, tolerando variantes de nombre o estructura.
    """
    expected = {
        "resumen_partida": "",
        "lectura_de_linea": "",
        "impacto_mid_game": "",
        "impacto_late_game": "",
        "errores_clave": "",
        "aciertos_clave": "",
        "plan_de_mejora": "",
        "veredicto_final": "",
        "lectura_de_linea_score": None,
        "impacto_mid_game_score": None,
        "impacto_late_game_score": None,
        "veredicto_final_score": None,
    }

    if not isinstance(data, dict):
        data = {"resumen_partida": str(data)}

    # Si viene anidado (ej. {"data": {...}}), usar el interno
    nested = data.get("data")
    if isinstance(nested, dict):
        data = nested

    alias_map = {
        "resumen_partida": [
            "resumen_partida", "resumen", "summary", "resumenGeneral", "overview"
        ],
        "lectura_de_linea": [
            "lectura_de_linea", "lectura_linea", "fase_de_linea", "laning", "lane_phase"
        ],
        "impacto_mid_game": [
            "impacto_mid_game", "mid_game", "impacto_medio_juego", "midgame_impact"
        ],
        "impacto_late_game": [
            "impacto_late_game", "late_game", "impacto_juego_tardio", "lategame_impact"
        ],
        "errores_clave": [
            "errores_clave", "errores", "mistakes", "puntos_de_mejora"
        ],
        "aciertos_clave": [
            "aciertos_clave", "aciertos", "fortalezas", "highlights", "puntos_fuertes"
        ],
        "plan_de_mejora": [
            "plan_de_mejora", "plan_mejora", "improvement_plan", "recomendaciones"
        ],
        "veredicto_final": [
            "veredicto_final", "veredicto", "conclusion", "final_verdict"
        ],
        "lectura_de_linea_score": [
            "lectura_de_linea_score", "lectura_linea_score", "lane_score", "laning_score"
        ],
        "impacto_mid_game_score": [
            "impacto_mid_game_score", "mid_game_score", "midgame_score"
        ],
        "impacto_late_game_score": [
            "impacto_late_game_score", "late_game_score", "lategame_score"
        ],
        "veredicto_final_score": [
            "veredicto_final_score", "veredicto_score", "final_score"
        ],
    }

    normalized = {}
    lowered = {str(k).lower(): v for k, v in data.items()}

    for target_key, aliases in alias_map.items():
        value = ""
        for key in aliases:
            if key in data:
                value = data.get(key)
                break
            key_l = key.lower()
            if key_l in lowered:
                value = lowered.get(key_l)
                break
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if target_key.endswith("_score"):
            normalized[target_key] = _normalize_score(value)
        else:
            normalized[target_key] = _prettify_numbered_points(str(value).strip() if value is not None else "")

    # fallback extra: si no hay contenido util, usar campos largos comunes
    if not normalized["resumen_partida"]:
        raw_text = data.get("texto") or data.get("analysis") or data.get("analisis")
        if raw_text:
            normalized["resumen_partida"] = str(raw_text).strip()

    # Fallback: extraer score desde el propio texto si no vino en campo separado
    score_text_fallbacks = {
        "lectura_de_linea_score": normalized.get("lectura_de_linea", ""),
        "impacto_mid_game_score": normalized.get("impacto_mid_game", ""),
        "impacto_late_game_score": normalized.get("impacto_late_game", ""),
        "veredicto_final_score": normalized.get("veredicto_final", ""),
    }
    for score_key, text in score_text_fallbacks.items():
        if normalized.get(score_key) is None:
            normalized[score_key] = _extract_score_from_text(text)

    # asegurar todas las claves
    for key, default_val in expected.items():
        if key not in normalized:
            normalized[key] = default_val

    return normalized


def _prettify_numbered_points(text):
    """
    Separa listas numeradas en líneas distintas para que se vean legibles en frontend.
    """
    if not text:
        return ""
    # Inserta salto de línea antes de patrones tipo "2. " cuando están pegados
    pretty = re.sub(r"(?<!\n)\s+(\d+\.\s+)", r"\n\1", text)
    return pretty.strip()


def _normalize_score(value):
    """
    Normaliza una puntuación a float en rango [0, 10] o None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(10.0, float(value)))

    text = str(value).strip()
    return _extract_score_from_text(text)


def _extract_score_from_text(text):
    """
    Extrae una puntuación 0-10 desde texto libre (ej. '7.5/10', '8', 'nota 6').
    """
    if not text:
        return None

    candidates = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:/10)?", text)
    for cand in candidates:
        try:
            num = float(cand.replace(",", "."))
            if 0.0 <= num <= 10.0:
                return num
        except ValueError:
            continue
    return None


def _estimate_tokens(text):
    """Estimación simple de tokens: ~4 chars por token."""
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def _truncate_text_to_chars(text, max_chars):
    """Recorta texto a un máximo de caracteres."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[TRUNCATED_FOR_TOKEN_LIMIT]"


def _build_compact_timeline_payload(timeline_data, max_chars=120_000):
    """
    Compacta el timeline para reducir consumo:
    - Solo eventos clave
    - Resumen de oro por frame
    - Mantiene metadata útil para análisis
    """
    if not isinstance(timeline_data, dict):
        return {}

    info = timeline_data.get("info", {}) or {}
    frames = info.get("frames", []) or []
    participants = info.get("participants", []) or []

    key_event_types = {
        "CHAMPION_KILL",
        "ELITE_MONSTER_KILL",
        "BUILDING_KILL",
        "WARD_PLACED",
        "WARD_KILL",
    }

    compact_frames = []
    compact_events = []

    for frame in frames:
        timestamp = frame.get("timestamp", 0)
        participant_frames = frame.get("participantFrames", {}) or {}

        team_gold = {"100": 0, "200": 0}
        for pf in participant_frames.values():
            pid = pf.get("participantId")
            gold = pf.get("totalGold", 0) or 0
            if 1 <= int(pid or 0) <= 5:
                team_gold["100"] += gold
            elif 6 <= int(pid or 0) <= 10:
                team_gold["200"] += gold

        compact_frames.append({
            "timestamp": timestamp,
            "minute": int(timestamp / 60000) if timestamp else 0,
            "team_gold": team_gold,
            "gold_diff": team_gold["100"] - team_gold["200"],
        })

        events = frame.get("events", []) or []
        for event in events:
            etype = event.get("type")
            if etype not in key_event_types:
                continue

            compact_event = {
                "timestamp": event.get("timestamp"),
                "minute": int((event.get("timestamp", 0) or 0) / 60000),
                "type": etype,
                "killerId": event.get("killerId"),
                "victimId": event.get("victimId"),
                "assistingParticipantIds": event.get("assistingParticipantIds", []),
                "teamId": event.get("teamId"),
                "position": event.get("position"),
                "monsterType": event.get("monsterType"),
                "monsterSubType": event.get("monsterSubType"),
                "buildingType": event.get("buildingType"),
                "towerType": event.get("towerType"),
                "wardType": event.get("wardType"),
                "killType": event.get("killType"),
            }
            compact_events.append(compact_event)

    compact = {
        "metadata": {
            "match_id": timeline_data.get("metadata", {}).get("matchId"),
            "data_version": timeline_data.get("metadata", {}).get("dataVersion"),
            "participants_count": len(participants),
            "frames_count": len(frames),
            "events_key_count": len(compact_events),
        },
        "participants": [
            {
                "participantId": p.get("participantId"),
                "puuid": p.get("puuid"),
            } for p in participants
        ],
        "frame_gold_summary": compact_frames,
        "key_events": compact_events,
    }

    # Ajuste de tamaño progresivo si excede límite
    while len(json.dumps(compact, ensure_ascii=False)) > max_chars:
        if len(compact["key_events"]) > 50:
            compact["key_events"] = compact["key_events"][: int(len(compact["key_events"]) * 0.75)]
            continue
        if len(compact["frame_gold_summary"]) > 20:
            compact["frame_gold_summary"] = compact["frame_gold_summary"][::2]
            continue
        break

    return compact


def _clean_url_encoded_strings(obj):
    """
    Limpia caracteres URL-encoded de un diccionario o lista.
    Convierte secuencias como %f3 a sus caracteres UTF-8 correspondientes.
    
    Args:
        obj: Diccionario o lista a limpiar
        
    Returns:
        Diccionario o lista limpia
    """
    import urllib.parse
    
    if isinstance(obj, dict):
        return {k: _clean_url_encoded_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_url_encoded_strings(item) for item in obj]
    elif isinstance(obj, str):
        # Decodificar URL-encoded strings
        try:
            # Intentar decodificar si contiene caracteres URL-encoded
            return urllib.parse.unquote(obj)
        except Exception:
            return obj
    else:
        return obj


def _add_metadata(result, timestamp, permiso_info=None):
    """
    Añade metadata al resultado del análisis.
    
    Args:
        result: Resultado del análisis
        timestamp: Timestamp de generación
        permiso_info: Información de permisos (opcional)
    """
    hours_old = (time.time() - timestamp) / 3600
    days_old = hours_old / 24
    fecha = datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H:%M')
    
    metadata = {
        "generated_at": fecha,
        "timestamp": timestamp,
        "is_outdated": hours_old > 24,
        "hours_old": round(hours_old, 1),
        "days_old": round(days_old, 1),
        "button_label": f"Análisis antiguo ({fecha})" if hours_old > 24 else f"Generado: {fecha}"
    }
    
    # Añadir info de permisos si está disponible
    if permiso_info:
        metadata["tiempo_restante"] = permiso_info.get("tiempo_restante_texto", "Desponible")
        metadata["proximo_analisis_disponible"] = permiso_info.get("disponible", True)
        metadata["modo_forzado"] = permiso_info.get("modo_forzado", False)
        metadata["segundos_restantes"] = permiso_info.get("segundos_restantes", 0)
    
    return {
        **result,
        "_metadata": metadata
    }
