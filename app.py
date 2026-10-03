import logging
import os

from flask import Flask, jsonify, redirect
from flask_cors import CORS

from config import config
from models import db
from routes.api import api

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(config_name=None):
    """Application factory"""
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    CORS(app, origins=app.config.get("CORS_ORIGINS", ["*"]))

    # Register blueprints
    app.register_blueprint(api)

    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {str(error)}")
        return jsonify({"error": "Internal server error"}), 500

    # Create tables (log and continue if the database is temporarily unreachable)
    with app.app_context():
        try:
            db.create_all()
            logger.info("Database tables created/verified")
        except Exception as exc:
            logger.error("Could not create database tables: %s", exc)

    # Mount the Dash dashboard on the same WSGI app
    dashboard_enabled = app.config.get("ENABLE_DASHBOARD", True)
    if dashboard_enabled:
        try:
            from dashboard import DASH_BASE_PATH, init_dashboard

            init_dashboard(app)
            app.config["DASHBOARD_URL"] = DASH_BASE_PATH
        except Exception as exc:
            dashboard_enabled = False
            logger.error("Dashboard could not be mounted: %s", exc)

    # Root endpoint
    @app.route("/", methods=["GET"])
    def index():
        if dashboard_enabled:
            return redirect(app.config["DASHBOARD_URL"])
        return (
            jsonify(
                {
                    "name": "AI Stock Analyzer",
                    "version": "1.0.0",
                    "description": "Advanced stock analysis and forecasting platform",
                    "endpoints": {
                        "health": "/api/health",
                        "stocks": "/api/stocks/<symbol>/price",
                        "forecast": "/api/stocks/<symbol>/forecast",
                        "analytics": "/api/stocks/<symbol>/analytics",
                        "compare": "/api/stocks/compare",
                    },
                }
            ),
            200,
        )

    logger.info(f"Application created with config: {config_name}")
    return app


if __name__ == "__main__":
    app = create_app()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", 7860))
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"

    app.run(host=host, port=port, debug=debug)
