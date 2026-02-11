"""
Servicio de integración con Gemini AI para análisis de partidas.
"""

import json
import time
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

from config.settings import GOOGLE_API_KEY, GEMINI_MODEL
from services.github_service import read_analysis, save_analysis, read_player_permission, save_player_permission


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


def check_player_permission(puuid):
    """
    Verifica si un jugador tiene permiso para usar el análisis de IA.
    Si no existe el archivo, lo crea con permiso SI por defecto.
    
    Returns:
        tuple: (tiene_permiso, sha, contenido_completo)
    """
    return read_player_permission(puuid)


def block_player_permission(puuid, sha=None):
    """
    Bloquea el permiso de un jugador después de usar el análisis.
    """
    content = {
        "permitir_llamada": "NO",
        "razon": "Llamada consumida. Requiere rehabilitación manual.",
        "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
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
        
        response = client.models.generate_content(
            model=GEMINI_MODEL,
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
            # Fallback a JSON manual
            result = json.loads(response.text)
        
        # Guardar análisis
        timestamp = time.time()
        analysis_doc = {
            "timestamp": timestamp,
            "signature": current_signature,
            "data": result
        }
        save_analysis(puuid, analysis_doc, sha)
        
        return _add_metadata(result, timestamp)
        
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            return {"error": "Cuota de Gemini agotada. Espera unas horas."}, 429
        raise


def _add_metadata(result, timestamp):
    """Añade metadata al resultado del análisis."""
    hours_old = (time.time() - timestamp) / 3600
    days_old = hours_old / 24
    fecha = datetime.fromtimestamp(timestamp).strftime('%d/%m/%Y %H:%M')
    
    return {
        **result,
        "_metadata": {
            "generated_at": fecha,
            "timestamp": timestamp,
            "is_outdated": hours_old > 24,
            "hours_old": round(hours_old, 1),
            "days_old": round(days_old, 1),
            "button_label": f"Análisis antiguo ({fecha})" if hours_old > 24 else f"Generado: {fecha}"
        }
    }
