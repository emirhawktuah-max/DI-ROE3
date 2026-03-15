"""
processing.py — Logic for processing the uploaded ranking .txt files.

Columns expected:
  numeracja, Nazwa, Poziom, Klasa, Rezonowanie, Ranking udziału

'Ranking udziału' may contain 'Poza rankingiem' (outside ranking) for some rows.
"""

import pandas as pd


def process(df: pd.DataFrame, choices: dict) -> dict:
    df = df.copy()

    # Normalise 'Ranking udziału' — treat 'Poza rankingiem' as unranked
    rank_col = 'Ranking udziału'
    if rank_col in df.columns:
        df['_ranked'] = pd.to_numeric(df[rank_col], errors='coerce')
        df['_unranked'] = df['_ranked'].isna()

    # --- Filter mode ---
    filter_mode = choices.get('filter_mode', 'All rows')
    if filter_mode == 'Ranked only':
        df = df[~df['_unranked']]
    elif filter_mode == 'Unranked only':
        df = df[df['_unranked']]

    # --- Group by class ---
    group_by = choices.get('group_by', '(none)')

    # --- Sort ---
    sort_by = choices.get('sort_by', 'numeracja')
    sort_asc = choices.get('sort_order', 'Ascending') == 'Ascending'
    if sort_by in df.columns:
        numeric_sort = pd.to_numeric(df[sort_by], errors='coerce')
        if numeric_sort.notna().any():
            df['_sort_key'] = numeric_sort
            df = df.sort_values('_sort_key', ascending=sort_asc)
        else:
            df = df.sort_values(sort_by, ascending=sort_asc)

    # Drop helper columns before display
    display_df = df.drop(columns=[c for c in ['_ranked', '_unranked', '_sort_key'] if c in df.columns])

    # --- Stats ---
    stats = {}
    if choices.get('include_stats', True):
        if rank_col in df.columns:
            ranked_vals = df['_ranked'].dropna() if '_ranked' in df.columns else pd.to_numeric(df[rank_col], errors='coerce').dropna()
            stats['Ranking udziału (ranked only)'] = {
                'min': int(ranked_vals.min()) if len(ranked_vals) else 'n/a',
                'max': int(ranked_vals.max()) if len(ranked_vals) else 'n/a',
                'mean': round(ranked_vals.mean(), 1) if len(ranked_vals) else 'n/a',
            }
        if 'Rezonowanie' in df.columns:
            rez = pd.to_numeric(df['Rezonowanie'], errors='coerce').dropna()
            stats['Rezonowanie'] = {
                'min': int(rez.min()) if len(rez) else 'n/a',
                'max': int(rez.max()) if len(rez) else 'n/a',
                'mean': round(rez.mean(), 1) if len(rez) else 'n/a',
            }

    # --- Class breakdown ---
    class_breakdown = {}
    if group_by == 'Klasa' and 'Klasa' in df.columns:
        class_breakdown = df['Klasa'].value_counts().to_dict()

    total = len(df)
    unranked = int(df['_unranked'].sum()) if '_unranked' in df.columns else 0
    ranked = total - unranked

    summary = (
        f"Showing {total} players — {ranked} ranked, {unranked} outside ranking. "
        f"Filter: {filter_mode}. Sorted by: {sort_by} ({'asc' if sort_asc else 'desc'})."
    )

    return {
        'summary': summary,
        'table': display_df.fillna('').to_dict(orient='records'),
        'columns': list(display_df.columns),
        'stats': stats,
        'class_breakdown': class_breakdown,
    }


def get_choice_options(df: pd.DataFrame) -> list:
    columns = list(df.columns)

    return [
        {
            'name': 'filter_mode',
            'label': 'Show players',
            'type': 'select',
            'options': ['All rows', 'Ranked only', 'Unranked only'],
            'default': 'All rows',
        },
        {
            'name': 'sort_by',
            'label': 'Sort by',
            'type': 'select',
            'options': ['numeracja', 'Nazwa', 'Poziom', 'Klasa', 'Rezonowanie', 'Ranking udziału'],
            'default': 'numeracja',
        },
        {
            'name': 'sort_order',
            'label': 'Sort order',
            'type': 'select',
            'options': ['Ascending', 'Descending'],
            'default': 'Ascending',
        },
        {
            'name': 'group_by',
            'label': 'Show class breakdown',
            'type': 'select',
            'options': ['(none)', 'Klasa'],
            'default': '(none)',
        },
        {
            'name': 'include_stats',
            'label': 'Include numeric statistics',
            'type': 'checkbox',
            'options': [],
            'default': True,
        },
    ]
