"""Flask application factory."""

import logging
import uuid

from flask import Flask
from flask_socketio import SocketIO, emit

from app.config import FlaskConfig, DEFAULTS
from app.models import db, ConfigSnapshot, SequenceNote
from app.hardware import create_hardware
from app.sequencer import Sequencer, StatePublisher

log = logging.getLogger(__name__)

# Module-level singletons (initialised in create_app)
sequencer: Sequencer = None  # type: ignore[assignment]
publisher: StatePublisher = None  # type: ignore[assignment]
socketio: SocketIO = SocketIO()


def create_app() -> Flask:
    global sequencer, publisher

    app = Flask(__name__)
    app.config.from_object(FlaskConfig)

    # Database
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrate_config_snapshots_schema()

    # SocketIO – threading mode is safe with our busy-wait timing threads.
    # simple-websocket provides native WebSocket transport without eventlet.
    socketio.init_app(app, async_mode="threading")

    # Hardware
    hw = create_hardware()
    hw.setup()

    # State publisher + Sequencer
    publisher = StatePublisher()
    publisher.init_socketio(socketio)
    sequencer = Sequencer(hw, publisher)

    # Load most recent config (or use defaults)
    with app.app_context():
        _load_initial_config(sequencer)

    # SocketIO connect handler — send initial state to newly connected client
    @socketio.on("connect")
    def _on_connect():
        emit("state_update", sequencer.snapshot())

    # Register blueprints
    from app.routes.api import api_bp
    from app.routes.touchscreen import ts_bp
    from app.routes.configuration import cfg_bp
    from app.routes.analysis import analysis_bp
    from app.routes.optimization import opt_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(ts_bp)
    app.register_blueprint(cfg_bp)
    app.register_blueprint(analysis_bp)
    app.register_blueprint(opt_bp)

    # Teardown
    @app.teardown_appcontext
    def _shutdown(exc):
        pass  # cleanup handled at process exit

    import atexit
    atexit.register(hw.cleanup)

    log.info("Coil-gun sequencer app ready")
    return app


def _migrate_config_snapshots_schema() -> None:
    """Apply lightweight schema + value migrations to config_snapshots.

    `db.create_all()` creates missing tables but never alters existing ones,
    so when a new column is added to ConfigSnapshot we need to ALTER TABLE
    on existing SQLite databases. Also handles one-time legacy-value fixups
    for columns that were added with now-obsolete defaults or semantics.

    Idempotent — safe to run on every startup. Rows that are already in the
    new shape produce zero-row UPDATEs.
    """
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    if "config_snapshots" not in insp.get_table_names():
        return  # Fresh database — create_all() already built the full schema.

    existing_cols = {c["name"] for c in insp.get_columns("config_snapshots")}

    with db.engine.begin() as conn:
        # --- capacitor_bank_size_uf ---
        if "capacitor_bank_size_uf" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE config_snapshots "
                "ADD COLUMN capacitor_bank_size_uf REAL NOT NULL DEFAULT 1000.0"
            ))
            log.info("Added column config_snapshots.capacitor_bank_size_uf")
        else:
            # Legacy fixup: the first migration used DEFAULT 0.0 for this
            # column. The current design treats 1000 µF (smallest module)
            # as the fallback for unset, so bump any remaining 0.0 rows.
            # Edge case: this also bumps rows where an operator explicitly
            # set capacitor = 0, but 0 µF is physically nonsensical so we
            # accept the loss.
            bumped = conn.execute(text(
                "UPDATE config_snapshots "
                "SET capacitor_bank_size_uf = 1000.0 "
                "WHERE capacitor_bank_size_uf = 0.0"
            )).rowcount
            if bumped:
                log.info(
                    "Bumped %d legacy capacitor_bank_size_uf=0 rows to 1000",
                    bumped,
                )

        # --- rail_source_active ---
        if "rail_source_active" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE config_snapshots "
                "ADD COLUMN rail_source_active REAL NOT NULL DEFAULT 0.0"
            ))
            log.info("Added column config_snapshots.rail_source_active")
        else:
            # Legacy fixup: the first migration stored this as INTEGER
            # (0 or 1) from a short-lived Boolean incarnation. The current
            # design stores v_coil_ceiling when on (and 0.0 when off) so
            # ML regressors see a meaningful magnitude. Rewrite any row
            # that still holds the literal value 1 to match its own
            # snapshot's v_coil_ceiling. SQLite's lax type affinity means
            # the column can already accept REAL values — no rebuild
            # needed, just reinterpret the data in place.
            bumped = conn.execute(text(
                "UPDATE config_snapshots "
                "SET rail_source_active = v_coil_ceiling "
                "WHERE rail_source_active = 1"
            )).rowcount
            if bumped:
                log.info(
                    "Rewrote %d legacy rail_source_active=1 rows to match "
                    "v_coil_ceiling",
                    bumped,
                )


        # --- coil resistance & inductance ratings ---
        _coil_rating_cols = {
            "coil_1_resistance_ohms": 1.3,
            "coil_1_inductance_uh": 476.0,
            "coil_2_resistance_ohms": 2.8,
            "coil_2_inductance_uh": 1900.0,
            "coil_3_resistance_ohms": 5.0,
            "coil_3_inductance_uh": 1000.0,
        }
        for col, default in _coil_rating_cols.items():
            if col not in existing_cols:
                conn.execute(text(
                    f"ALTER TABLE config_snapshots "
                    f"ADD COLUMN {col} REAL NOT NULL DEFAULT {default}"
                ))
                log.info("Added column config_snapshots.%s", col)

        # --- per-coil capacitor banks & projectile start offset ---
        _new_metadata_cols = {
            "coil_1_capacitor_uf": 4000.0,
            "coil_2_capacitor_uf": 4000.0,
            "coil_3_capacitor_uf": 4000.0,
            "projectile_start_offset_mm": 2.0,
        }
        for col, default in _new_metadata_cols.items():
            if col not in existing_cols:
                conn.execute(text(
                    f"ALTER TABLE config_snapshots "
                    f"ADD COLUMN {col} REAL NOT NULL DEFAULT {default}"
                ))
                log.info("Added column config_snapshots.%s", col)


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
