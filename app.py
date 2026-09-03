import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from config import config
from models import db
from routes.api import api

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app(config_name=None):
    """Application factory"""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')
    
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])
    
    # Initialize extensions
    db.init_app(app)
    CORS(app, origins=app.config.get('CORS_ORIGINS', ['*']))
    
    # Register blueprints
    app.register_blueprint(api)
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"Internal server error: {str(error)}")
        return jsonify({'error': 'Internal server error'}), 500
    
    # Create tables
    with app.app_context():
        db.create_all()
        logger.info("Database tables created/verified")
    
    # Root endpoint
    @app.route('/', methods=['GET'])
    def index():
        return jsonify({
            'name': 'AI Stock Analyzer',
            'version': '1.0.0',
            'description': 'Advanced stock analysis and forecasting platform',
            'endpoints': {
                'health': '/api/health',
                'stocks': '/api/stocks/<symbol>/price',
                'forecast': '/api/stocks/<symbol>/forecast',
                'analytics': '/api/stocks/<symbol>/analytics',
                'compare': '/api/stocks/compare'
            }
        }), 200
    
    logger.info(f"Application created with config: {config_name}")
    return app

if __name__ == '__main__':
    app = create_app()
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(host=host, port=port, debug=debug)
