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


# ---------------------------------------------------------------------------
# ROSTER GENERATOR
# ---------------------------------------------------------------------------

BATTLE_TYPES = ['RoE', 'Clan Battle']
PRIORITY_OPTIONS = ['Rezonowanie', 'Class']
ONLINE_OPTIONS   = ['Only confirmed online', 'All players']
DIST_OPTIONS     = ['Max power', 'Even distribution']
ROE_BATTLES_OPTIONS = ['7', '10']


@main_bp.route('/roster', methods=['GET', 'POST'])
@login_required
def roster_select():
    """Step 1 — pick up to 3 files."""
    tr = t()
    if current_user.is_admin():
        all_uploads = Upload.query.order_by(Upload.uploaded_at.desc()).all()
    else:
        all_uploads = Upload.query.filter(
            (Upload.user_id == current_user.id) | (Upload.is_shared == True)
        ).order_by(Upload.uploaded_at.desc()).all()

    if request.method == 'POST':
        selected = request.form.getlist('upload_ids')
        if not selected:
            flash(tr['roster_error_no_files'], 'error')
            return redirect(request.url)
        if len(selected) > 3:
            flash(tr['roster_error_too_many'], 'error')
            return redirect(request.url)

        # Pass selected IDs to config step via query string
        ids_str = ','.join(selected[:3])
        return redirect(url_for('main.roster_config', ids=ids_str))

    return render_template('roster_select.html',
                           uploads=all_uploads,
                           battle_types=BATTLE_TYPES)



@main_bp.route('/roster/config', methods=['GET', 'POST'])
@login_required
def roster_config():
    tr = t()
    ids_str = request.args.get('ids', '') or request.form.get('ids', '')
    try:
        upload_ids = [int(i) for i in ids_str.split(',') if i.strip()]
    except ValueError:
        flash(tr['roster_error_no_files'], 'error')
        return redirect(url_for('main.roster_select'))

    uploads = [_get_accessible_upload(uid) for uid in upload_ids]
    uploads = [u for u in uploads if u]
    if not uploads:
        flash(tr['roster_error_no_files'], 'error')
        return redirect(url_for('main.roster_select'))

    if request.method == 'POST':
        battle_type = request.form.get('battle_type', '').strip()
        priority    = request.form.get('priority', '').strip()
        online_only = request.form.get('online_only', '').strip()
        distribution= request.form.get('distribution', '').strip()
        roe_battles = request.form.get('roe_battles', '10').strip()

        errors = []
        if battle_type not in BATTLE_TYPES:
            errors.append(tr['roster_error_no_battle_type'])
        if priority not in PRIORITY_OPTIONS:
            errors.append(tr['roster_error_no_priority'])
        if online_only not in ONLINE_OPTIONS:
            errors.append(tr['roster_error_no_online'])
        if distribution not in DIST_OPTIONS:
            errors.append(tr['roster_error_no_dist'])

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('roster_config.html',
                                   uploads=uploads, ids_str=ids_str,
                                   battle_types=BATTLE_TYPES,
                                   priority_options=PRIORITY_OPTIONS,
                                   online_options=ONLINE_OPTIONS,
                                   dist_options=DIST_OPTIONS,
                                   roe_battles_options=ROE_BATTLES_OPTIONS)

        num_battles = int(roe_battles) if battle_type == 'RoE' else 10

        session['roster_ids']        = upload_ids
        session['roster_battle_type']= battle_type
        session['roster_priority']   = priority
        session['roster_online_only']= online_only
        session['roster_distribution']= distribution
        session['roster_num_battles']= num_battles
        return redirect(url_for('main.roster_view'))

    return render_template('roster_config.html',
                           uploads=uploads, ids_str=ids_str,
                           battle_types=BATTLE_TYPES,
                           priority_options=PRIORITY_OPTIONS,
                           online_options=ONLINE_OPTIONS,
                           dist_options=DIST_OPTIONS,
                           roe_battles_options=ROE_BATTLES_OPTIONS)


# ---------------------------------------------------------------------------
# Grouping algorithm
# ---------------------------------------------------------------------------

def _build_groups(players, num_battles, priority, distribution):
    """
    players   : list of dicts, each has 'Rezonowanie' (numeric), 'Klasa'
    num_battles: int — number of groups of 8
    priority  : 'Rezonowanie' | 'Class'
    distribution: 'Max power' | 'Even distribution'
    Returns list of groups (each group = list of player dicts).
    """
    import math

    # Convert Rezonowanie to numeric, default 0
    for p in players:
        try:
            p['_reso'] = int(p.get('Rezonowanie', 0))
        except (ValueError, TypeError):
            p['_reso'] = 0

    group_size = 8
    total_slots = num_battles * group_size

    # Trim or pad to total_slots
    if priority == 'Rezonowanie':
        sorted_players = sorted(players, key=lambda x: x['_reso'], reverse=True)
    else:
        # Class priority: interleave classes so each group gets variety
        from collections import defaultdict
        by_class = defaultdict(list)
        for p in sorted(players, key=lambda x: x['_reso'], reverse=True):
            by_class[p.get('Klasa', '')].append(p)
        # Round-robin across classes
        sorted_players = []
        classes = sorted(by_class.keys(), key=lambda c: -len(by_class[c]))
        while any(by_class[c] for c in classes):
            for c in classes:
                if by_class[c]:
                    sorted_players.append(by_class[c].pop(0))

    pool = sorted_players[:total_slots]

    if distribution == 'Max power':
        # Simple: fill groups top to bottom
        groups = []
        for i in range(num_battles):
            groups.append(pool[i*group_size:(i+1)*group_size])
    else:
        # Even distribution: snake draft
        # Sort by reso desc, then snake across groups
        pool_sorted = sorted(pool, key=lambda x: x['_reso'], reverse=True)
        groups = [[] for _ in range(num_battles)]
        for i, player in enumerate(pool_sorted):
            round_num = i // num_battles
            pos_in_round = i % num_battles
            # Snake: even rounds left-to-right, odd rounds right-to-left
            if round_num % 2 == 0:
                group_idx = pos_in_round
            else:
                group_idx = num_battles - 1 - pos_in_round
            groups[group_idx].append(player)

    # Clean up helper key
    for g in groups:
        for p in g:
            p.pop('_reso', None)

    return groups


@main_bp.route('/roster/view')
@login_required
def roster_view():
    tr = t()
    upload_ids   = session.get('roster_ids', [])
    battle_type  = session.get('roster_battle_type', '')
    priority     = session.get('roster_priority', 'Rezonowanie')
    online_only  = session.get('roster_online_only', 'All players')
    distribution = session.get('roster_distribution', 'Max power')
    num_battles  = session.get('roster_num_battles', 10)

    if not upload_ids or not battle_type:
        flash(tr['roster_error_no_files'], 'error')
        return redirect(url_for('main.roster_select'))

    # Combine all files into one player list
    all_players = []
    source_files = []
    columns_used = None

    for uid in upload_ids:
        upload_rec = _get_accessible_upload(uid)
        if not upload_rec:
            continue
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_rec.filename)
        try:
            df = parse_txt_to_df(filepath)
        except Exception:
            continue

        attendance = Attendance.query.filter_by(upload_id=uid).first()
        confirmed_map = json.loads(attendance.confirmed_rows) if attendance else {}

        cols = list(df.columns)
        if columns_used is None:
            # Remove first and last column
            columns_used = cols[1:-1]

        rows = df.fillna('').to_dict(orient='records')
        for i, row in enumerate(rows):
            row['_confirmed'] = confirmed_map.get(str(i), False)
            row['_source']    = upload_rec.original_filename
            all_players.append(row)

        source_files.append(upload_rec.original_filename)

    if online_only == 'Only confirmed online':
        pool = [p for p in all_players if p.get('_confirmed')]
    else:
        pool = all_players

    # Remove duplicates by name (keep highest reso)
    seen = {}
    for p in sorted(pool, key=lambda x: int(x.get('Rezonowanie', 0) or 0), reverse=True):
        name = p.get('Nazwa', '')
        if name and name not in seen:
            seen[name] = p
    pool = list(seen.values())

    groups = _build_groups(pool, num_battles, priority, distribution)

    # Compute avg reso per group for display
    def avg_reso(group):
        vals = []
        for p in group:
            try:
                vals.append(int(p.get('Rezonowanie', 0) or 0))
            except Exception:
                pass
        return round(sum(vals)/len(vals)) if vals else 0

    group_stats = [{'avg_reso': avg_reso(g), 'count': len(g)} for g in groups]

    return render_template('roster_view.html',
                           groups=groups,
                           group_stats=group_stats,
                           columns=columns_used or [],
                           battle_type=battle_type,
                           priority=priority,
                           online_only=online_only,
                           distribution=distribution,
                           num_battles=num_battles,
                           source_files=source_files,
                           total_players=len(pool))
