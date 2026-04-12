"""Touchscreen page – Arm/Fire controls and run statistics."""

from flask import Blueprint, render_template

ts_bp = Blueprint("touchscreen", __name__)


@ts_bp.route("/")
def index():
    return render_template("touchscreen.html")
