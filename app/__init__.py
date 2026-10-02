import os

from dotenv import load_dotenv
from flask import Flask

from app.extensions import db
from app.models import User
from app.routes.auth import auth_bp
from app.routes.cashier import cashier_bp
from app.routes.dashboard import dashboard_bp
from app.routes.kitchen import kitchen_bp
from app.routes.menu import menu_bp
from app.routes.orders import orders_bp
from app.routes.store import store_bp
from app.routes.tables import tables_bp
from app.routes.users import users_bp

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _validate_production_config(
    app: Flask,
    database_url_is_configured: bool,
) -> None:
    secret_key = app.config["SECRET_KEY"]
    if not secret_key or secret_key in {
        "dev-only-change-me",
        "platter-dev-secret-change-later",
    } or len(secret_key) < 32:
        raise RuntimeError(
            "Production requires SECRET_KEY to be a unique random value "
            "of at least 32 characters."
        )
    if not database_url_is_configured or not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("Production requires DATABASE_URL to be configured.")


def _bootstrap_admin(app: Flask) -> None:
    admin = User.query.filter_by(username="admin").first()
    if app.config["ENVIRONMENT"] == "production":
        initial_password = os.environ.get("ADMIN_PASSWORD", "")
        if admin and admin.check_password("admin12345"):
            if len(initial_password) < 16:
                raise RuntimeError(
                    "The default admin password is still in use. Set "
                    "ADMIN_PASSWORD to a unique password of at least 16 "
                    "characters to rotate it before production startup."
                )
            admin.set_password(initial_password)
            db.session.commit()
            app.logger.warning(
                "Rotated the development admin password for production. "
                "Remove ADMIN_PASSWORD from the environment after startup."
            )
        elif not User.query.first():
            username = os.environ.get("ADMIN_USERNAME", "").strip().lower()
            full_name = os.environ.get("ADMIN_FULL_NAME", "").strip()
            if not username or not full_name or len(initial_password) < 16:
                raise RuntimeError(
                    "A new production database needs ADMIN_USERNAME, "
                    "ADMIN_FULL_NAME, and a unique ADMIN_PASSWORD of at "
                    "least 16 characters for the initial admin account."
                )
            admin = User(username=username, full_name=full_name, role="admin")
            admin.set_password(initial_password)
            db.session.add(admin)
            db.session.commit()
            app.logger.info("Created the initial production admin account.")
        return

    if not admin:
        admin = User(username="admin", full_name="System Admin", role="admin")
        admin.set_password("admin12345")
        db.session.add(admin)
        db.session.commit()
        app.logger.warning(
            "Created the development admin account (admin / admin12345). "
            "Change this password before production use."
        )


def create_app(test_config: dict | None = None) -> Flask:
    environment = os.environ.get("PLATTER_ENV", "development").strip().lower()
    if environment not in {"development", "production"}:
        raise ValueError("PLATTER_ENV must be 'development' or 'production'.")

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_mapping(
        ENVIRONMENT=environment,
        DEBUG=environment == "development",
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///platter.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool(
            "SESSION_COOKIE_SECURE",
            default=environment == "production",
        ),
        HOST=os.environ.get(
            "PLATTER_HOST",
            "0.0.0.0" if environment == "production" else "127.0.0.1",
        ),
    )
    if test_config:
        app.config.update(test_config)

    if app.config["ENVIRONMENT"] == "production":
        database_url_is_configured = bool(os.environ.get("DATABASE_URL")) or bool(
            test_config and test_config.get("SQLALCHEMY_DATABASE_URI")
        )
        _validate_production_config(app, database_url_is_configured)

    db.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(menu_bp)
    app.register_blueprint(tables_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(kitchen_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(cashier_bp)
    app.register_blueprint(store_bp)

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    return app


app = create_app()
