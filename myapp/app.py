from flask import Flask, session, redirect, url_for, request
from extensions import db, login_manager
from auth import auth_bp
from admin import admin_bp
from main import main_bp
from translations import get_translations
import os
import json as _json


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(main_bp)

    # Language toggle route
    @app.route('/set_lang/<lang>')
    def set_lang(lang):
        if lang in ('en', 'pl'):
            session['lang'] = lang
        return redirect(request.referrer or url_for('main.dashboard'))

    # Inject translation helper + current lang into every template
    @app.context_processor
    def inject_translations():
        lang = session.get('lang', 'en')
        return dict(t=get_translations(lang), lang=lang)

    app.jinja_env.filters['from_json'] = _json.loads

    with app.app_context():
        from models import SavedRoster  # ensure table is created
        db.create_all()
        seed_admin()

    return app


def seed_admin():
    from models import User
    if not User.query.filter_by(username='Emir').first():
        admin = User(username='Emir', email='admin@example.com', role='admin')
        admin.set_password('Emir666')
        db.session.add(admin)
        db.session.commit()
        print("✓ Default admin created: Emir / Emir666")


app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
