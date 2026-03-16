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

BATTLE_TYPES        = ['RoE', 'Clan Battle']
CLAN_BATTLE_MODES   = ['Standard', '8 4 2 1']
PRIORITY_OPTIONS    = ['Rezonowanie', 'Class']
ONLINE_OPTIONS      = ['Only confirmed online', 'Prioritize Online', 'All players']
DIST_OPTIONS        = ['Max power', 'Even distribution']
ROE_BATTLES_OPTIONS = ['7', '10']

ALL_CLASSES = [
    'Nekromanta', 'Barbarzyńca', 'Łowca demonów', 'Rycerz krwi',
    'Czarownik', 'Mnich', 'Krzyżowiec', 'Sztormiciel', 'Druid'
]


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

def _reso(p):
    try:
        return int(p.get('Rezonowanie', 0) or 0)
    except Exception:
        return 0


def _sort_by_priority(players, priority):
    """Return players sorted according to priority setting."""
    if priority == 'Rezonowanie':
        return sorted(players, key=_reso, reverse=True)
    else:
        # Class round-robin (sorted by reso within class)
        from collections import defaultdict
        by_class = defaultdict(list)
        for p in sorted(players, key=_reso, reverse=True):
            by_class[p.get('Klasa', '')].append(p)
        result = []
        classes = sorted(by_class.keys(), key=lambda c: -len(by_class[c]))
        while any(by_class[c] for c in classes):
            for c in classes:
                if by_class[c]:
                    result.append(by_class[c].pop(0))
        return result


def _distribute(pool, num_groups, group_size, distribution):
    """Split pool into num_groups of group_size using chosen distribution."""
    if distribution == 'Max power':
        groups = []
        for i in range(num_groups):
            groups.append(pool[i*group_size:(i+1)*group_size])
        return groups
    else:
        # Snake draft for even reso averages
        by_reso = sorted(pool, key=_reso, reverse=True)
        groups = [[] for _ in range(num_groups)]
        for i, player in enumerate(by_reso):
            rnd = i // num_groups
            pos = i % num_groups
            idx = pos if rnd % 2 == 0 else num_groups - 1 - pos
            groups[idx].append(player)
        return groups


def _build_groups(players, num_battles, priority, distribution,
                  clan_mode='Standard'):
    """
    Main grouping entry point.
    clan_mode: 'Standard' | '8 4 2 1'
    """
    group_size = 8

    # Enrich with numeric reso
    for p in players:
        p['_reso'] = _reso(p)

    sorted_pool = _sort_by_priority(players, priority)

    if clan_mode == '8 4 2 1':
        # Tier sizes: 3 groups of 8 = 24 players per tier label
        # Label tiers: first 3 groups → "8", next 3 → "4", next 3 → "2", last 3 → "1"
        # Total: 12 groups of 8 = 96 slots
        tier_labels = ['8']*3 + ['4']*3 + ['2']*3 + ['1']*3
        total_slots = 12 * group_size
        pool = sorted_pool[:total_slots]

        tier_groups = []
        for tier_start in range(0, 12, 3):
            tier_pool = pool[tier_start*group_size:(tier_start+3)*group_size]
            tier_g = _distribute(tier_pool, 3, group_size, distribution)
            for g in tier_g:
                tier_groups.append(g)

        for p in players:
            p.pop('_reso', None)
        return tier_groups, tier_labels

    else:
        total_slots = num_battles * group_size
        pool = sorted_pool[:total_slots]
        groups = _distribute(pool, num_battles, group_size, distribution)
        for p in players:
            p.pop('_reso', None)
        return groups, None


def _apply_online_filter(all_players, online_mode, num_slots):
    """
    Returns a pool respecting the online mode.
    'Only confirmed online'  → confirmed players only
    'All players'            → everyone
    'Prioritize Online'      → confirmed first, then fill with non-confirmed
    """
    if online_mode == 'Only confirmed online':
        return [p for p in all_players if p.get('_confirmed')]
    elif online_mode == 'Prioritize Online':
        confirmed  = [p for p in all_players if p.get('_confirmed')]
        remaining  = [p for p in all_players if not p.get('_confirmed')]
        # Fill up to num_slots
        return (confirmed + remaining)[:num_slots]
    else:
        return all_players


def _avg_reso(group):
    vals = [_reso(p) for p in group]
    return round(sum(vals)/len(vals)) if vals else 0


# ---------------------------------------------------------------------------
# Roster routes
# ---------------------------------------------------------------------------

@main_bp.route('/roster', methods=['GET', 'POST'])
@login_required
def roster_select():
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
        ids_str = ','.join(selected[:3])
        return redirect(url_for('main.roster_config', ids=ids_str))

    return render_template('roster_select.html', uploads=all_uploads)


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

    def _render_config(**kw):
        return render_template('roster_config.html',
                               uploads=uploads, ids_str=ids_str,
                               battle_types=BATTLE_TYPES,
                               clan_battle_modes=CLAN_BATTLE_MODES,
                               priority_options=PRIORITY_OPTIONS,
                               online_options=ONLINE_OPTIONS,
                               dist_options=DIST_OPTIONS,
                               roe_battles_options=ROE_BATTLES_OPTIONS,
                               all_classes=ALL_CLASSES,
                               **kw)

    if request.method == 'POST':
        battle_type   = request.form.get('battle_type', '').strip()
        clan_mode     = request.form.get('clan_mode', 'Standard').strip()
        priority      = request.form.get('priority', '').strip()
        online_only   = request.form.get('online_only', '').strip()
        distribution  = request.form.get('distribution', '').strip()
        roe_battles   = request.form.get('roe_battles', '10').strip()
        active_classes= request.form.getlist('active_classes')

        errors = []
        if battle_type not in BATTLE_TYPES:
            errors.append(tr['roster_error_no_battle_type'])
        if priority not in PRIORITY_OPTIONS:
            errors.append(tr['roster_error_no_priority'])
        if online_only not in ONLINE_OPTIONS:
            errors.append(tr['roster_error_no_online'])
        if distribution not in DIST_OPTIONS:
            errors.append(tr['roster_error_no_dist'])
        if not active_classes:
            errors.append(tr['roster_error_no_classes'])

        if errors:
            for e in errors:
                flash(e, 'error')
            return _render_config()

        if battle_type == 'RoE':
            num_battles = int(roe_battles) if roe_battles in ROE_BATTLES_OPTIONS else 10
        elif clan_mode == '8 4 2 1':
            num_battles = 12
        else:
            num_battles = 12

        session['roster_ids']         = upload_ids
        session['roster_battle_type'] = battle_type
        session['roster_clan_mode']   = clan_mode
        session['roster_priority']    = priority
        session['roster_online_only'] = online_only
        session['roster_distribution']= distribution
        session['roster_num_battles'] = num_battles
        session['roster_active_classes'] = active_classes
        return redirect(url_for('main.roster_view'))

    return _render_config()


@main_bp.route('/roster/view')
@login_required
def roster_view():
    tr = t()
    upload_ids     = session.get('roster_ids', [])
    battle_type    = session.get('roster_battle_type', '')
    clan_mode      = session.get('roster_clan_mode', 'Standard')
    priority       = session.get('roster_priority', 'Rezonowanie')
    online_only    = session.get('roster_online_only', 'All players')
    distribution   = session.get('roster_distribution', 'Max power')
    num_battles    = session.get('roster_num_battles', 12)
    active_classes = session.get('roster_active_classes', ALL_CLASSES)

    if not upload_ids or not battle_type:
        flash(tr['roster_error_no_files'], 'error')
        return redirect(url_for('main.roster_select'))

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
            columns_used = cols[1:-1]

        rows = df.fillna('').to_dict(orient='records')
        for i, row in enumerate(rows):
            row['_confirmed'] = confirmed_map.get(str(i), False)
            row['_source']    = upload_rec.original_filename
            all_players.append(row)
        source_files.append(upload_rec.original_filename)

    # Deduplicate by name
    seen = {}
    for p in sorted(all_players, key=_reso, reverse=True):
        name = p.get('Nazwa', '')
        if name and name not in seen:
            seen[name] = p
    all_players = list(seen.values())

    # Class filter
    all_players = [p for p in all_players
                   if p.get('Klasa', '') in active_classes]

    # Online filter
    total_slots = num_battles * 8
    pool = _apply_online_filter(all_players, online_only, total_slots)

    # Build groups
    groups, tier_labels = _build_groups(
        pool, num_battles, priority, distribution,
        clan_mode=clan_mode if battle_type == 'Clan Battle' else 'Standard'
    )

    group_stats = [{'avg_reso': _avg_reso(g), 'count': len(g)} for g in groups]

    # Strip internal _ keys from groups and pool before passing to template
    def _clean(p):
        return {k: v for k, v in p.items() if not k.startswith('_')}

    clean_groups = [[_clean(p) for p in g] for g in groups]
    full_pool_clean = [_clean(p) for p in all_players]

    # Store roster data server-side in a temp file (session too small for large rosters)
    import tempfile, pickle
    roster_tmp = {
        'groups':       clean_groups,
        'columns':      columns_used or [],
        'player_pool':  full_pool_clean,
        'tier_labels':  tier_labels,
        'source_files': source_files,
    }
    tmp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'_roster_tmp_{current_user.id}.pkl')
    with open(tmp_path, 'wb') as f:
        pickle.dump(roster_tmp, f)

    return render_template('roster_view.html',
                           groups=clean_groups,
                           group_stats=group_stats,
                           tier_labels=tier_labels,
                           columns=columns_used or [],
                           battle_type=battle_type,
                           clan_mode=clan_mode,
                           priority=priority,
                           online_only=online_only,
                           distribution=distribution,
                           num_battles=num_battles,
                           active_classes=active_classes,
                           source_files=source_files,
                           total_players=len(pool),
                           full_pool=full_pool_clean)


@main_bp.route('/roster/save', methods=['POST'])
@login_required
def roster_save():
    from models import SavedRoster
    import traceback
    tr = t()

    import pickle
    tmp_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'_roster_tmp_{current_user.id}.pkl')
    if not os.path.exists(tmp_path):
        flash('Session expired — please regenerate the roster first.', 'error')
        return redirect(url_for('main.roster_select'))
    with open(tmp_path, 'rb') as f:
        save_data = pickle.load(f)

    battle_type = session.get('roster_battle_type', '')
    clan_mode   = session.get('roster_clan_mode', 'Standard')

    config = {
        'battle_type':    battle_type,
        'clan_mode':      clan_mode,
        'priority':       session.get('roster_priority'),
        'online_only':    session.get('roster_online_only'),
        'distribution':   session.get('roster_distribution'),
        'num_battles':    session.get('roster_num_battles'),
        'active_classes': session.get('roster_active_classes'),
        'source_files':   save_data.get('source_files', []),
        'tier_labels':    save_data.get('tier_labels'),
    }

    today = date.today().strftime('%Y-%m-%d')
    name  = f"{current_user.username}_{today}"

    try:
        sr = SavedRoster(
            name=name,
            created_by=current_user.id,
            battle_type=battle_type,
            config=json.dumps(config),
            groups_data=json.dumps(save_data.get('groups', [])),
            columns_data=json.dumps(save_data.get('columns', [])),
            player_pool=json.dumps(save_data.get('player_pool', [])),
            overrides='{}',
        )
        db.session.add(sr)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'roster_save error: {traceback.format_exc()}')
        flash(f'Could not save roster: {e}', 'error')
        return redirect(url_for('main.roster_view'))

    flash(f'{tr["roster_saved"]} "{name}"', 'success')
    return redirect(url_for('main.saved_roster_view', roster_id=sr.id))


# ---------------------------------------------------------------------------
# Saved Rosters module
# ---------------------------------------------------------------------------

@main_bp.route('/saved-rosters')
@login_required
def saved_rosters_list():
    from models import SavedRoster
    if current_user.is_admin():
        rosters = SavedRoster.query.order_by(SavedRoster.created_at.desc()).all()
    else:
        rosters = SavedRoster.query.filter_by(created_by=current_user.id)\
                             .order_by(SavedRoster.created_at.desc()).all()
    return render_template('saved_rosters_list.html', rosters=rosters)


@main_bp.route('/saved-rosters/<int:roster_id>')
@login_required
def saved_roster_view(roster_id):
    from models import SavedRoster
    sr = SavedRoster.query.get_or_404(roster_id)
    if sr.created_by != current_user.id and not current_user.is_admin():
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.saved_rosters_list'))

    groups      = json.loads(sr.groups_data)
    columns     = json.loads(sr.columns_data)
    overrides   = json.loads(sr.overrides)
    player_pool = json.loads(sr.player_pool)
    config      = json.loads(sr.config)
    tier_labels = config.get('tier_labels')

    # Apply overrides to groups for display
    display_groups = []
    for gi, group in enumerate(groups):
        display_group = []
        for si, player in enumerate(group):
            key = f'{gi}:{si}'
            display_group.append(overrides.get(key, player))
        display_groups.append(display_group)

    group_stats = [{'avg_reso': _avg_reso(g), 'count': len(g)}
                   for g in display_groups]

    return render_template('saved_roster_view.html',
                           sr=sr,
                           groups=display_groups,
                           group_stats=group_stats,
                           tier_labels=tier_labels,
                           columns=columns,
                           config=config,
                           player_pool=player_pool)


@main_bp.route('/saved-rosters/<int:roster_id>/override', methods=['POST'])
@login_required
def saved_roster_override(roster_id):
    from models import SavedRoster
    sr = SavedRoster.query.get_or_404(roster_id)
    if sr.created_by != current_user.id and not current_user.is_admin():
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.saved_rosters_list'))

    overrides = json.loads(sr.overrides)
    player_pool = json.loads(sr.player_pool)

    # Each override field: name="override_GI_SI" value=player_name
    for key, val in request.form.items():
        if key.startswith('override_'):
            parts = key.split('_', 2)
            if len(parts) == 3:
                gi, si = parts[1], parts[2]
                slot_key = f'{gi}:{si}'
                # Find player in pool by name
                chosen = next((p for p in player_pool
                               if p.get('Nazwa', '') == val), None)
                if chosen:
                    overrides[slot_key] = chosen
                elif slot_key in overrides:
                    del overrides[slot_key]

    sr.overrides = json.dumps(overrides)
    db.session.commit()
    flash(t()['roster_overrides_saved'], 'success')
    return redirect(url_for('main.saved_roster_view', roster_id=roster_id))


@main_bp.route('/saved-rosters/<int:roster_id>/delete', methods=['POST'])
@login_required
def saved_roster_delete(roster_id):
    from models import SavedRoster
    sr = SavedRoster.query.get_or_404(roster_id)
    if sr.created_by != current_user.id and not current_user.is_admin():
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.saved_rosters_list'))
    db.session.delete(sr)
    db.session.commit()
    flash(t()['roster_deleted'], 'success')
    return redirect(url_for('main.saved_rosters_list'))



@main_bp.route('/saved-rosters/<int:roster_id>/export')
@login_required
def saved_roster_export(roster_id):
    import traceback
    from models import SavedRoster
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from io import BytesIO
    from flask import send_file

    # --- Load roster ---
    sr = SavedRoster.query.get_or_404(roster_id)
    if sr.created_by != current_user.id and not current_user.is_admin():
        flash(t()['flash_access_denied'], 'error')
        return redirect(url_for('main.saved_rosters_list'))

    try:
        groups    = json.loads(sr.groups_data  or '[]')
        columns   = json.loads(sr.columns_data or '[]')
        overrides = json.loads(sr.overrides    or '{}')
        config    = json.loads(sr.config       or '{}')
    except Exception as e:
        flash(f'Could not read roster data: {e}', 'error')
        return redirect(url_for('main.saved_roster_view', roster_id=roster_id))

    if not groups or not columns:
        flash('Roster has no data — please regenerate and save it again.', 'error')
        return redirect(url_for('main.saved_roster_view', roster_id=roster_id))

    tier_labels = config.get('tier_labels')

    # Apply overrides
    display_groups = []
    for gi, group in enumerate(groups):
        dg = []
        for si, player in enumerate(group):
            key = f'{gi}:{si}'
            dg.append(overrides.get(key, player))
        display_groups.append(dg)

    # --- Build workbook ---
    try:
        accent_hex  = 'C8F07A'
        dark_hex    = '171717'
        surface_hex = '1E1E1E'
        border_hex  = '2A2A2A'
        muted_hex   = '888888'

        tier_colours = {
            '8': ('2E1A0F', 'F0A060'),
            '4': ('1A2E1A', '70C070'),
            '2': ('0F1E2E', '60A0F0'),
            '1': ('2E0F0F', 'F07070'),
        }
        class_colours = {
            'Nekromanta':    ('1A1A2E', 'A0A0F0'),
            'Barbarzyca':    ('2E1A0F', 'F0A060'),
            'Barbarzyńca':   ('2E1A0F', 'F0A060'),
            'Łowca demonów': ('1A2E1A', '70C070'),
            'Rycerz krwi':   ('2E0F0F', 'F07070'),
            'Czarownik':     ('2A1A2E', 'C070E0'),
            'Mnich':         ('1A2A2E', '60C0D0'),
            'Krzyżowiec':    ('2E2A0F', 'E0D060'),
            'Sztormiciel':   ('0F1E2E', '60A0F0'),
            'Druid':         ('1A2E20', '60D090'),
        }

        def sol(hex_col):
            return PatternFill('solid', start_color=hex_col, fgColor=hex_col)

        def bdr():
            s = Side(style='thin', color=border_hex)
            return Border(left=s, right=s, top=s, bottom=s)

        wb = Workbook()
        wb.remove(wb.active)

        # Summary sheet
        ws = wb.create_sheet('Summary')
        ws.sheet_view.showGridLines = False
        ws.column_dimensions['A'].width = 24
        ws.column_dimensions['B'].width = 34

        ws['A1'] = sr.name
        ws['A1'].font = Font(name='Arial', bold=True, size=13, color=accent_hex)
        ws['A1'].fill = sol(dark_hex)
        ws.merge_cells('A1:B1')
        ws.row_dimensions[1].height = 26

        meta_rows = [
            ('Battle type',  sr.battle_type or ''),
            ('Clan mode',    config.get('clan_mode', '')),
            ('Priority',     config.get('priority', '')),
            ('Player pool',  config.get('online_only', '')),
            ('Distribution', config.get('distribution', '')),
            ('Battles',      str(config.get('num_battles', ''))),
            ('Created by',   sr.creator.username),
            ('Date',         sr.created_at.strftime('%d %b %Y %H:%M')),
        ]
        for ri, (k, v) in enumerate(meta_rows, start=2):
            ws.cell(ri, 1, k).font = Font(name='Arial', size=9, color=muted_hex)
            ws.cell(ri, 1).fill    = sol(surface_hex)
            ws.cell(ri, 2, str(v)).font = Font(name='Arial', size=9, color='E8E8E8')
            ws.cell(ri, 2).fill   = sol(surface_hex)

        sf = config.get('source_files', [])
        if isinstance(sf, str):
            try: sf = json.loads(sf)
            except: sf = []
        r = len(meta_rows) + 2
        ws.cell(r, 1, 'Source files').font = Font(name='Arial', size=9,
                                                   color=muted_hex, bold=True)
        ws.cell(r, 1).fill = sol(surface_hex)
        ws.merge_cells(f'A{r}:B{r}')
        for fname in sf:
            r += 1
            ws.cell(r, 1, str(fname)).font = Font(name='Arial', size=9,
                                                   color=accent_hex)
            ws.cell(r, 1).fill = sol(surface_hex)
            ws.merge_cells(f'A{r}:B{r}')

        # One sheet per battle
        for gi, group in enumerate(display_groups):
            tier = (tier_labels[gi]
                    if tier_labels and gi < len(tier_labels) else None)
            sname = f'Battle {gi+1}' + (f' [{tier}]' if tier else '')
            ws = wb.create_sheet(sname[:31])
            ws.sheet_view.showGridLines = False

            hdr_cols  = ['#'] + columns
            col_widths = [4] + [max(13, len(c) + 2) for c in columns]

            hdr_bg = tier_colours.get(tier, (surface_hex, accent_hex))[0] if tier else surface_hex
            hdr_fg = tier_colours.get(tier, (surface_hex, accent_hex))[1] if tier else accent_hex

            ws.row_dimensions[1].height = 20
            for ci, (hdr, w) in enumerate(zip(hdr_cols, col_widths), 1):
                c = ws.cell(1, ci, hdr)
                c.font      = Font(name='Arial', bold=True, size=8, color=hdr_fg)
                c.fill      = sol(hdr_bg)
                c.alignment = Alignment(horizontal='center', vertical='center')
                c.border    = bdr()
                ws.column_dimensions[get_column_letter(ci)].width = w

            for ri, player in enumerate(group, 2):
                ws.row_dimensions[ri].height = 16
                rbg = dark_hex if ri % 2 == 0 else surface_hex

                c = ws.cell(ri, 1, ri - 1)
                c.font = Font(name='Arial', size=8, color=muted_hex)
                c.fill = sol(rbg); c.border = bdr()
                c.alignment = Alignment(horizontal='center', vertical='center')

                for ci, col in enumerate(columns, 2):
                    val = str(player.get(col, '') or '')
                    c = ws.cell(ri, ci, val)
                    c.fill = sol(rbg); c.border = bdr()
                    c.alignment = Alignment(horizontal='left', vertical='center')
                    c.font = Font(name='Arial', size=8, color='E8E8E8')

                    if col == 'Nazwa':
                        c.font = Font(name='Arial', size=8, bold=True, color='E8E8E8')
                    elif col == 'Klasa':
                        bg, fg = class_colours.get(val, (surface_hex, muted_hex))
                        c.fill = sol(bg)
                        c.font = Font(name='Arial', size=8, bold=True, color=fg)
                        c.alignment = Alignment(horizontal='center', vertical='center')
                    elif col in ('Rezonowanie', 'Ranking udziału', 'Poziom'):
                        c.alignment = Alignment(horizontal='center', vertical='center')
                        if val in ('Poza rankingiem', ''):
                            c.font = Font(name='Arial', size=8, color=muted_hex, italic=True)

            # Avg reso footer
            if group:
                try:
                    avg = round(sum(int(p.get('Rezonowanie', 0) or 0)
                                    for p in group) / len(group))
                except Exception:
                    avg = 0
                fr = len(group) + 2
                c = ws.cell(fr, 1, f'{len(group)} players  ·  avg reso {avg}')
                c.font = Font(name='Arial', size=7, color=accent_hex, italic=True)
                c.fill = sol(dark_hex)
                ws.merge_cells(start_row=fr, start_column=1,
                               end_row=fr, end_column=len(hdr_cols))

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)

        safe_name = (sr.name or 'roster').replace(' ', '_')
        return send_file(
            buf,
            as_attachment=True,
            download_name=f'{safe_name}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        current_app.logger.error(f'Export error: {traceback.format_exc()}')
        flash(f'Export failed: {e}', 'error')
        return redirect(url_for('main.saved_roster_view', roster_id=roster_id))
