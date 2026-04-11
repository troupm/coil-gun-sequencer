"""Analysis page – velocity trend charts and tabular log viewer."""

from flask import Blueprint, render_template

analysis_bp = Blueprint("analysis", __name__)


@analysis_bp.route("/analysis")
def analysis_page():
    return render_template("analysis.html")
