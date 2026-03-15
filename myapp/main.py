from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_required, current_user
from extensions import db
from models import Upload, Result, Attendance
from processing import process, get_choice_options
from translations import get_translations
import pandas as pd
import os, uuid, json
from datetime import date

CLAN_OPTIONS = ["AlterEgo", "Nirvana", "AE Hells"]

main_bp = Blueprint('main', __name__)


def parse_txt_to_df(filepath):
    for enc in ('utf-8', 'cp1250', 'latin-1'):
        try:
            df = pd.read_csv(filepath, encoding=enc, encoding_errors='replace')
            return df
        except Exception:
            continue
    raise ValueError("Could not parse file with any known encoding.")


def t():
    return get_translations(session.get('lang', 'en'))


@main_bp.route('/')
@login_required
def dashboard():
    my_uploads = Upload.query.filter_by(user_id=current_user.id).order_by(Upload.uploaded_at.desc()).all()
    shared_uploads = Upload.query.filter_by(is_shared=True).order_by(Upload.uploaded_at.desc()).all()
    my_results = Result.query.filter_by(user_id=current_user.id).order_by(Result.created_at.desc()).limit(5).all()
    return render_template('dashboard.html',
                           my_uploads=my_uploads,
                           shared_uploads=shared_uploads,
                           my_results=my_results)


@main_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    tr = t()
    if request.method == 'POST':
        file = request.files.get('csv_file')
        clan = request.form.get('clan', '').strip()

        if not file or file.filename == '':
            flash(tr['upload_error_no_file'], 'error')
            return redirect(request.url)
        if not file.filename.lower().endswith('.txt'):
            flash(tr['upload_error_wrong_type'], 'error')
            return redirect(request.url)
        if clan not in CLAN_OPTIONS:
            flash(tr['upload_error_no_clan'], 'error')
            return redirect(request.url)

        # Internal storage filename (uuid to avoid collisions)
        filename = f"{uuid.uuid4().hex}.txt"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        try:
            df = parse_txt_to_df(filepath)
            row_count = len(df)
            columns = json.dumps(list(df.columns))
        except Exception as e:
            os.remove(filepath)
            flash(f'Could not parse file: {e}', 'error')
            return redirect(request.url)

        # Display name: ClanName_YYYY-MM-DD.txt
        today = date.today().strftime('%Y-%m-%d')
        display_name = f"{clan}_{today}.txt"

        is_shared = 'is_shared' in request.form and current_user.is_admin()

        upload_rec = Upload(
            filename=filename,
            original_filename=display_name,
            user_id=current_user.id,
            is_shared=is_shared,
            row_count=row_count,
            columns=columns,
        )
        db.session.add(upload_rec)
        db.session.commit()
        flash(f'"{display_name}" {tr["upload_success"]} — {row_count} {tr["upload_rows_found"]}.', 'success')
        return redirect(url_for('main.view_upload', upload_id=upload_rec.id))

    return render_template('upload.html', clan_options=CLAN_OPTIONS)


@main_bp.route('/uploads')
@login_required
def manage_uploads():
    if current_user.is_admin():
        all_uploads = Upload.query.order_by(Upload.uploaded_at.desc()).all()
    else:
        all_uploads = Upload.query.filter(
            (Upload.user_id == current_user.id) | (Upload.is_shared == True)
        ).order_by(Upload.uploaded_at.desc()).all()
    return render_template('manage_uploads.html', uploads=all_uploads)


@main_bp.route('/uploads/<int:upload_id>/preview')
@login_required
def preview_upload(upload_id):
    tr = t()
    upload_rec = _get_accessible_upload(upload_id)
    if not upload_rec:
        flash(tr['flash_access_denied'], 'error')
        return redirect(url_for('main.manage_uploads'))
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
    try:
        df = parse_txt_to_df(filepath)
        columns = list(df.columns)
        rows = df.head(50).fillna('').to_dict(orient='records')
        total_rows = len(df)
        dtypes = {col: str(df[col].dtype) for col in columns}
        stats = {}
        for col in df.select_dtypes(include='number').columns:
            stats[col] = {
                'min': round(df[col].min(), 2),
                'max': round(df[col].max(), 2),
                'mean': round(df[col].mean(), 2),
                'nulls': int(df[col].isnull().sum()),
            }
    except Exception as e:
        flash(f'Could not read file: {e}', 'error')
        return redirect(url_for('main.manage_uploads'))
    return render_template('preview_upload.html',
                           upload=upload_rec,
                           columns=columns,
                           rows=rows,
                           total_rows=total_rows,
                           dtypes=dtypes,
                           stats=stats)


@main_bp.route('/choices/<int:upload_id>', methods=['GET', 'POST'])
@login_required
def choices(upload_id):
    tr = t()
    upload_rec = _get_accessible_upload(upload_id)
    if not upload_rec:
        flash(tr['choices_error'], 'error')
        return redirect(url_for('main.dashboard'))

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
    df = parse_txt_to_df(filepath)
    choice_defs = get_choice_options(df)

    if request.method == 'POST':
        user_choices = {}
        for c in choice_defs:
            if c['type'] == 'checkbox':
                user_choices[c['name']] = c['name'] in request.form
            else:
                user_choices[c['name']] = request.form.get(c['name'], c.get('default', ''))

        try:
            output = process(df, user_choices)
        except Exception as e:
            flash(f'{tr["choices_proc_error"]}: {e}', 'error')
            return redirect(request.url)

        result = Result(
            upload_id=upload_rec.id,
            user_id=current_user.id,
            choices=json.dumps(user_choices),
            output=json.dumps(output),
        )
        db.session.add(result)
        db.session.commit()
        return redirect(url_for('main.results', result_id=result.id))

    return render_template('choices.html', upload=upload_rec, choice_defs=choice_defs)


@main_bp.route('/results/<int:result_id>')
@login_required
def results(result_id):
    tr = t()
    result = Result.query.get_or_404(result_id)
    if result.user_id != current_user.id and not current_user.is_admin():
        flash(tr['flash_access_denied'], 'error')
        return redirect(url_for('main.dashboard'))
    output = json.loads(result.output)
    choices = json.loads(result.choices)
    return render_template('results.html', result=result, output=output, choices=choices)


@main_bp.route('/uploads/<int:upload_id>/delete', methods=['POST'])
@login_required
def delete_upload(upload_id):
    tr = t()
    upload_rec = Upload.query.get_or_404(upload_id)
    if upload_rec.user_id != current_user.id and not current_user.is_admin():
        flash(tr['flash_access_denied'], 'error')
        return redirect(url_for('main.manage_uploads'))
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(upload_rec)
    db.session.commit()
    flash(tr['flash_upload_deleted'], 'success')
    return redirect(url_for('main.manage_uploads'))


def _get_accessible_upload(upload_id):
    upload_rec = Upload.query.get(upload_id)
    if not upload_rec:
        return None
    if upload_rec.user_id == current_user.id:
        return upload_rec
    if upload_rec.is_shared:
        return upload_rec
    if current_user.is_admin():
        return upload_rec
    return None


@main_bp.route('/uploads/<int:upload_id>/view')
@login_required
def view_upload(upload_id):
    upload_rec = _get_accessible_upload(upload_id)
    if not upload_rec:
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.manage_uploads'))
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
    try:
        df = parse_txt_to_df(filepath)
        columns = list(df.columns)
        rows = df.fillna('').to_dict(orient='records')
    except Exception as e:
        flash(f'Could not read file: {e}', 'error')
        return redirect(url_for('main.manage_uploads'))

    # Load existing attendance for this upload
    attendance = Attendance.query.filter_by(upload_id=upload_id).first()
    confirmed = json.loads(attendance.confirmed_rows) if attendance else {}

    confirmed_count = sum(1 for v in confirmed.values() if v)

    return render_template('view_upload.html',
                           upload=upload_rec,
                           columns=columns,
                           rows=rows,
                           confirmed=confirmed,
                           confirmed_count=confirmed_count)


@main_bp.route('/uploads/<int:upload_id>/attendance', methods=['POST'])
@login_required
def save_attendance(upload_id):
    upload_rec = _get_accessible_upload(upload_id)
    if not upload_rec:
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.manage_uploads'))

    # Collect checked row indices from form
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
    df = parse_txt_to_df(filepath)
    confirmed = {}
    for i in range(len(df)):
        confirmed[str(i)] = f'row_{i}' in request.form

    attendance = Attendance.query.filter_by(upload_id=upload_id).first()
    if attendance:
        attendance.confirmed_rows = json.dumps(confirmed)
        attendance.saved_at = __import__('datetime').datetime.utcnow()
    else:
        attendance = Attendance(upload_id=upload_id, confirmed_rows=json.dumps(confirmed))
        db.session.add(attendance)
    db.session.commit()

    tr = t()
    flash(tr['attendance_saved'], 'success')
    return redirect(url_for('main.view_upload', upload_id=upload_id))
