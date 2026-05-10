from flask import Blueprint, flash, redirect, render_template, request, url_for

import config.settings as settings
from services.team_service import build_team_dashboard, save_team_logo


team_bp = Blueprint("team", __name__)


@team_bp.route("/")
def equipo():
    """Pestana de seguimiento del rendimiento colectivo del equipo."""
    dashboard = build_team_dashboard()
    return render_template(
        "equipo.html",
        dashboard=dashboard,
        ddragon_version=settings.DDRAGON_VERSION,
        has_player_data=True,
    )


@team_bp.route("/logo", methods=["POST"])
def subir_logo():
    """Sube el PNG del logo del equipo."""
    ok, message = save_team_logo(request.files.get("team_logo"))
    flash(message, "success" if ok else "danger")
    return redirect(url_for("team.equipo"))
