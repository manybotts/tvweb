
# tv_app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager  # Import LoginManager
import os

db = SQLAlchemy()
login_manager = LoginManager()  # Initialize LoginManager


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    login_manager.init_app(app)  # Initialize LoginManager with the app
    login_manager.login_view = 'login'  # Set the login view

    # Import models here to avoid circular imports (but before blueprints)
    from .models import User, Show, Episodes  # Import User

    # Import and register blueprints
    from .routes import bp as main_blueprint
    app.register_blueprint(main_blueprint)
    
    @app.context_processor
    def inject_user():
        from flask_login import current_user
        return dict(current_user=current_user)


    return app
