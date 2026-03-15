from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_required, current_user
from extensions import db
from models import User
from translations import get_translations
from functools import wraps

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        t = get_translations(session.get('lang', 'en'))
        if not current_user.is_authenticated or not current_user.is_admin():
            flash(t['flash_admin_required'], 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@login_required
@admin_required
def index():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    t = get_translations(session.get('lang', 'en'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')

        if User.query.filter_by(username=username).first():
            flash(t['flash_username_taken'], 'error')
        elif User.query.filter_by(email=email).first():
            flash(t['flash_email_taken'], 'error')
        elif len(password) < 6:
            flash(t['flash_password_short'], 'error')
        else:
            user = User(username=username, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'{t["flash_user_created"]} "{username}"', 'success')
            return redirect(url_for('admin.index'))

    return render_template('admin/user_form.html', user=None)


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    t = get_translations(session.get('lang', 'en'))
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.email = request.form.get('email', user.email).strip()
        user.role = request.form.get('role', user.role)
        user.is_active = 'is_active' in request.form
        new_password = request.form.get('password', '').strip()
        if new_password:
            if len(new_password) < 6:
                flash(t['flash_password_short'], 'error')
                return render_template('admin/user_form.html', user=user)
            user.set_password(new_password)
        db.session.commit()
        flash(f'{t["flash_user_updated"]} "{user.username}"', 'success')
        return redirect(url_for('admin.index'))
    return render_template('admin/user_form.html', user=user)


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    t = get_translations(session.get('lang', 'en'))
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash(t['flash_self_delete'], 'error')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'{t["flash_user_deleted"]} "{user.username}"', 'success')
    return redirect(url_for('admin.index'))
