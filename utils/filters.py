"""
Filtros personalizados de Jinja2 para las plantillas.
Extraídos desde app.py para mantener la separación de responsabilidades.
"""

from datetime import datetime, timezone
from config.settings import TARGET_TIMEZONE, QUEUE_NAMES, TIER_ORDER, RANK_ORDER


def get_queue_type_filter(queue_id):
    """Filtro para obtener el nombre de una cola por su ID."""
    return QUEUE_NAMES.get(int(queue_id), "Desconocido")


def format_timestamp_filter(timestamp):
    """Filtro para formatear timestamps UTC a la zona horaria local (UTC+2)."""
    if timestamp is None or timestamp == 0:
        return "N/A"
    # El timestamp de la API viene en milisegundos (UTC)
    timestamp_sec = timestamp / 1000
    # Crear un objeto datetime consciente de la zona horaria (aware) en UTC
    dt_utc = datetime.fromtimestamp(timestamp_sec, tz=timezone.utc)
    # Convertir a la zona horaria de visualización deseada
    dt_target = dt_utc.astimezone(TARGET_TIMEZONE)
    return dt_target.strftime("%d/%m/%Y %H:%M")


def format_peak_elo_filter(valor):
    """Filtro para formatear el valor de ELO pico en formato legible."""
    if valor is None:
        return "N/A"
    try:
        valor = int(valor)
    except (ValueError, TypeError):
        return "N/A"

    if valor >= 2800:
        lps = valor - 2800
        if valor >= 3200:
            return f"CHALLENGER ({lps} LPs)"
        elif valor >= 3000:
            return f"GRANDMASTER ({lps} LPs)"
        else:
            return f"MASTER ({lps} LPs)"

    tier_map = {
        6: "DIAMOND", 5: "EMERALD", 4: "PLATINUM", 3: "GOLD",
        2: "SILVER", 1: "BRONZE", 0: "IRON"
    }
    rank_map = {3: "I", 2: "II", 1: "III", 0: "IV"}

    league_points = valor % 100
    valor_without_lps = valor - league_points
    rank_value = (valor_without_lps // 100) % 4
    tier_value = (valor_without_lps // 100) // 4

    tier_name = tier_map.get(tier_value, "UNKNOWN")
    rank_name = rank_map.get(rank_value, "")
    return f"{tier_name} {rank_name} ({league_points} LPs)"


def thousands_separator_filter(value):
    """
    Filtro de Jinja2 para formatear números con separador de miles (punto para locale español).
    No añade decimales para enteros.
    """
    try:
        if isinstance(value, (int, float)):
            # Manual formatting for thousands separator (dot) and decimal separator (comma)
            if isinstance(value, int):
                manual_formatted = "{:,.0f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")
                return manual_formatted
            else:
                manual_formatted = "{:,.2f}".format(value).replace(",", "X").replace(".", ",").replace("X", ".")
                return manual_formatted
        return value  # Retorna el valor original si no es un número
    except Exception as e:
        return str(value)  # Retorna como string si hay error


def format_number_filter(value):
    """
    Filtro de Jinja2 para formatear números con separador de miles.
    """
    try:
        return "{:,.0f}".format(int(value)).replace(",", ".")
    except (ValueError, TypeError):
        return value


def register_filters(app):
    """Registra todos los filtros personalizados en la aplicación Flask."""
    app.template_filter('get_queue_type')(get_queue_type_filter)
    app.template_filter('format_timestamp')(format_timestamp_filter)
    app.template_filter('format_peak_elo')(format_peak_elo_filter)
    app.template_filter('thousands_separator')(thousands_separator_filter)
    app.template_filter('format_number')(format_number_filter)
