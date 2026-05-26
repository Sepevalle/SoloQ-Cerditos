from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for

import config.settings as settings
from services.github_service import read_team_report, save_team_report
from services.team_service import build_team_dashboard, get_team_config, save_team_logo


team_bp = Blueprint("team", __name__)


def _empty_team_dashboard():
    config = get_team_config()
    complete_roster = [p for p in config.get("players", []) if p.get("puuid")]
    return {
        "config": config,
        "summary": {
            "total_matches": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "current_streak": {"type": "none", "count": 0, "label": "Sin racha"},
            "recent_wins": 0,
            "recent_losses": 0,
            "recent_win_rate": 0,
            "lp_change": 0,
            "avg_duration": "0:00",
            "avg_kda": 0,
            "avg_damage": 0,
            "avg_vision": 0,
            "avg_dragons": 0,
            "avg_barons": 0,
            "avg_turrets": 0,
            "avg_deaths": 0,
            "avg_kills": 0,
            "avg_cs": 0,
            "last_match": None,
        },
        "aggregate_summary": {},
        "team_matches": [],
        "queue_stats": [],
        "recent_form": [],
        "champion_compositions": [],
        "player_stats": [],
        "duration_buckets": [],
        "objective_profile": {},
        "recent_trends": {
            "recent": {"matches": 0, "win_rate": 0, "avg_kda": 0, "avg_deaths": 0, "avg_objectives": 0},
            "previous": {"matches": 0, "win_rate": 0, "avg_kda": 0, "avg_deaths": 0, "avg_objectives": 0},
            "win_rate_delta": 0,
            "kda_delta": 0,
            "deaths_delta": 0,
            "objectives_delta": 0,
        },
        "insights": [],
        "missing_roster": len(complete_roster) != 5,
    }


def _format_wait(seconds):
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _get_time_until_next_team_generation(snapshot=None):
    if not snapshot:
        success, snapshot = read_team_report()
        if not success:
            snapshot = {}

    calculated_at = snapshot.get("calculated_at_iso")
    if not calculated_at:
        return True, 0, "0s"

    try:
        last_calc = datetime.fromisoformat(str(calculated_at).replace("Z", "+00:00"))
        if last_calc.tzinfo is None:
            last_calc = last_calc.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_calc).total_seconds()
        interval = settings.GLOBAL_STATS_UPDATE_INTERVAL
        if elapsed >= interval:
            return True, 0, "0s"
        remaining = int(interval - elapsed)
        return False, remaining, _format_wait(remaining)
    except Exception as e:
        print(f"[_get_time_until_next_team_generation] Error parseando fecha: {e}")
        return True, 0, "0s"


@team_bp.route("/")
def equipo():
    """Pestana de seguimiento del rendimiento colectivo del equipo desde snapshot."""
    success, snapshot = read_team_report()
    if not success:
        snapshot = {}

    dashboard = snapshot.get("dashboard") or _empty_team_dashboard()
    if dashboard:
        dashboard["config"] = get_team_config()
    can_generate, seconds_remaining, time_remaining = _get_time_until_next_team_generation(snapshot)
    return render_template(
        "equipo.html",
        dashboard=dashboard,
        needs_update=not bool(snapshot),
        can_generate=can_generate,
        seconds_remaining=seconds_remaining,
        time_remaining=time_remaining,
        generated_at=snapshot.get("generated_at", "N/A"),
        ddragon_version=settings.DDRAGON_VERSION,
        has_player_data=True,
    )


@team_bp.route("/actualizar", methods=["POST"])
def actualizar_equipo():
    """Genera y guarda el dashboard de equipo, como maximo cada 24h."""
    success, snapshot = read_team_report()
    can_generate, _seconds_remaining, time_remaining = _get_time_until_next_team_generation(snapshot if success else {})
    if not can_generate:
        flash(f"El dashboard de equipo se genero recientemente. Espera {time_remaining}.", "warning")
        return redirect(url_for("team.equipo"))

    try:
        dashboard = build_team_dashboard()
        report_data = {
            "dashboard": dashboard,
            "generated_at": datetime.now(settings.TARGET_TIMEZONE).strftime("%d/%m/%Y %H:%M"),
            "calculated_at_iso": datetime.now(timezone.utc).isoformat(),
        }
        if save_team_report(report_data):
            flash("Dashboard de equipo actualizado correctamente.", "success")
        else:
            flash("No se pudo guardar el dashboard de equipo. Revisa GITHUB_TOKEN o permisos.", "danger")
    except Exception as e:
        print(f"[actualizar_equipo] Error: {e}")
        import traceback
        traceback.print_exc()
        flash(f"Error al generar el dashboard de equipo: {e}", "danger")

    return redirect(url_for("team.equipo"))


@team_bp.route("/logo", methods=["POST"])
def subir_logo():
    """Sube el PNG del logo del equipo."""
    ok, message = save_team_logo(request.files.get("team_logo"))
    flash(message, "success" if ok else "danger")
    return redirect(url_for("team.equipo"))
