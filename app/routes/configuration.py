"""Configuration page – parameter editing, sequence management."""

from flask import Blueprint, render_template

cfg_bp = Blueprint("configuration", __name__)


@cfg_bp.route("/config")
def config_page():
    return render_template("configuration.html")
