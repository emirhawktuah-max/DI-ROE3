from extensions import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'admin' or 'user'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    uploads = db.relationship('Upload', backref='owner', lazy=True, foreign_keys='Upload.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Upload(db.Model):
    __tablename__ = 'uploads'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_shared = db.Column(db.Boolean, default=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    row_count = db.Column(db.Integer, default=0)
    columns = db.Column(db.Text, default='')  # JSON list of column names

    results = db.relationship('Result', backref='upload', lazy=True)

    def __repr__(self):
        return f'<Upload {self.original_filename}>'


class Result(db.Model):
    __tablename__ = 'results'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('uploads.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    choices = db.Column(db.Text, default='{}')  # JSON of user choices
    output = db.Column(db.Text, default='{}')   # JSON of result data
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Result {self.id}>'


class Attendance(db.Model):
    __tablename__ = 'attendance'
    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey('uploads.id'), nullable=False)
    # JSON dict: { "1": true, "3": true, ... } — keys are row index strings
    confirmed_rows = db.Column(db.Text, default='{}')
    # JSON dict: { "1": true, ... } — rows marked as PowerPlayer
    power_rows = db.Column(db.Text, default='{}')
    saved_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Attendance upload={self.upload_id}>'


class SavedRoster(db.Model):
    __tablename__ = 'saved_rosters'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)        # username + date
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    battle_type = db.Column(db.String(64), default='')
    config = db.Column(db.Text, default='{}')               # JSON config snapshot
    # JSON: list of groups, each group = list of player dicts
    groups_data = db.Column(db.Text, default='[]')
    # JSON: list of column names used
    columns_data = db.Column(db.Text, default='[]')
    # JSON: manual overrides { "group_idx:slot_idx": player_dict }
    overrides = db.Column(db.Text, default='{}')
    # JSON: full combined player pool (for override dropdowns)
    player_pool = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('User', foreign_keys=[created_by])

    def __repr__(self):
        return f'<SavedRoster {self.name}>'
