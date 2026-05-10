from flask import Blueprint, render_template

import config.settings as settings
from services.team_service import build_team_dashboard


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
