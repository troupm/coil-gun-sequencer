"""Optimization page – skill results viewer."""

from flask import Blueprint, render_template

opt_bp = Blueprint("optimization", __name__)


@opt_bp.route("/optimization")
def optimization_page():
    return render_template("optimization.html")
