"""Flask application factory."""

import logging
import uuid

from flask import Flask

from app.config import FlaskConfig, DEFAULTS
from app.models import db, ConfigSnapshot
from app.hardware import create_hardware
from app.sequencer import Sequencer, StatePublisher

log = logging.getLogger(__name__)

# Module-level singletons (initialised in create_app)
sequencer: Sequencer = None  # type: ignore[assignment]
publisher: StatePublisher = None  # type: ignore[assignment]


def create_app() -> Flask:
    global sequencer, publisher

    app = Flask(__name__)
    app.config.from_object(FlaskConfig)

    # Database
    db.init_app(app)
    with app.app_context():
        db.create_all()

    # Hardware
    hw = create_hardware()
    hw.setup()

    # State publisher + Sequencer
    publisher = StatePublisher()
    sequencer = Sequencer(hw, publisher)

    # Load most recent config (or use defaults)
    with app.app_context():
        _load_initial_config(sequencer)

    # Register blueprints
    from app.routes.api import api_bp
    from app.routes.touchscreen import ts_bp
    from app.routes.configuration import cfg_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(ts_bp)
    app.register_blueprint(cfg_bp)

    # Teardown
    @app.teardown_appcontext
    def _shutdown(exc):
        pass  # cleanup handled at process exit

    import atexit
    atexit.register(hw.cleanup)

    log.info("Coil-gun sequencer app ready")
    return app


def _load_initial_config(seq: Sequencer) -> None:
    """Load the most recent config snapshot, or seed with defaults."""
    latest = (
        ConfigSnapshot.query
        .order_by(ConfigSnapshot.id.desc())
        .first()
    )

    if latest:
        cfg = {k: getattr(latest, k) for k in ConfigSnapshot.PARAM_KEYS}
        seq.config = cfg
        seq.config_snapshot_id = latest.id
        seq.set_run_sequence(latest.run_sequence_id)
        log.info(f"Loaded config snapshot #{latest.id}, sequence {latest.run_sequence_id}")
    else:
        seq.config = dict(DEFAULTS)
        # Create initial snapshot
        seq_id = str(uuid.uuid4())
        snap = ConfigSnapshot(run_sequence_id=seq_id, **DEFAULTS)
        db.session.add(snap)
        db.session.commit()
        seq.config_snapshot_id = snap.id
        seq.set_run_sequence(seq_id)
        log.info(f"Seeded default config, new sequence {seq_id}")
