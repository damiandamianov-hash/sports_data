"""
combine_leagues.py — build ONE consolidated CSV per league
==========================================================

Run AFTER download_data.py has populated <root>/data/<RAW_DIR>/ with the
per-season files. For each league this stacks all seasons into a single
clean, analysis-ready CSV:

    <root>/data/combined/premier_league.csv
    <root>/data/combined/championship.csv
    <root>/data/combined/la_liga.csv
    <root>/data/combined/bundesliga.csv
    <root>/data/combined/bundesliga_2.csv
    <root>/data/combined/serie_a.csv
    <root>/data/combined/ligue_1.csv
    <root>/data/combined/belgium.csv
    <root>/data/combined/greek.csv
    <root>/data/combined/romania.csv
    <root>/data/combined/ekstraklasa.csv
    <root>/data/combined/eredivisie.csv
    <root>/data/combined/turkey.csv
    <root>/data/combined/portugal.csv
    <root>/data/combined/scotland.csv
    <root>/data/combined/segunda.csv
    <root>/data/combined/serie_b.csv
    <root>/data/combined/ligue_2.csv
    <root>/data/combined/brazil.csv
    <root>/data/combined/argentina.csv
    <root>/data/combined/japan.csv
    <root>/data/combined/usa.csv

Normalisations applied (the raw per-season files are left untouched):
  • encoding unified to UTF-8 (source mixes UTF-8-BOM and Windows-1252)
  • Date parsed from both %d/%m/%Y and %d/%m/%y, written as ISO YYYY-MM-DD
  • Season and League columns added
  • one odds set resolved per row into OddsH/OddsD/OddsA + OddsSource
    (preference: B365 -> Avg -> Pinnacle -> BbAv), original columns kept
  • blank/ragged trailer rows dropped
  • columns aligned across seasons (union of headers; missing -> empty)

Usage:
    python combine_leagues.py                 # all leagues
    python combine_leagues.py --league greek  # one league
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = r'C:\Users\DamianDamianov\Documents\football_predictor_damian'

# league key -> (raw_dir, file_prefix) — matches download_data.py / config/leagues/*.yaml
LEAGUES = {
    'premier_league': ('PREMIER LEAGUE', 'PL'),
    'championship':   ('CHAMPIONSHIP',   'CHAMPIONSHIP'),
    'la_liga':        ('LA-LIGA',        'LA-LIGA'),
    'bundesliga':     ('BUNDESLIGA',     'BUNDESLIGA'),
    'bundesliga_2':   ('BUNDESLIGA-2',   'BUNDESLIGA-2'),
    'serie_a':        ('SERIE-A',        'SERIE-A'),
    'ligue_1':        ('LE-CHAMPIONNAT', 'LE-CHAMPIONNAT'),
    'belgium':        ('BELGIUM',        'BELGIUM'),
    'greek':          ('GREEK',          'GREEK'),
    'romania':        ('ROMANIA',        'ROMANIA'),
    'ekstraklasa':    ('EKSTRAKLASA',    'EKSTRAKLASA'),
    'eredivisie':     ('EREDIVISIE',     'EREDIVISIE'),
    'turkey':         ('TURKEY',         'TURKEY'),
    'portugal':       ('PORTUGAL',       'PORTUGAL'),
    'scotland':       ('SCOTLAND',       'SCOTLAND'),
    'segunda':        ('SEGUNDA',        'SEGUNDA'),
    'serie_b':        ('SERIE-B',        'SERIE-B'),
    'ligue_2':        ('LIGUE-2',        'LIGUE-2'),
    'brazil':         ('BRAZIL',         'BRAZIL'),
    'argentina':      ('ARGENTINA',      'ARGENTINA'),
    'japan':          ('JAPAN',          'JAPAN'),
    'usa':            ('USA',            'USA'),
}

CORE_COLS = ['Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'FTR',
             'HS', 'AS', 'HST', 'AST', 'HC', 'AC']
ODDS_SETS = [('B365H', 'B365D', 'B365A', 'B365'),
             ('AvgH',  'AvgD',  'AvgA',  'Avg'),
             ('PSH',   'PSD',   'PSA',   'PS'),
             ('BbAvH', 'BbAvD', 'BbAvA', 'BbAv')]


def decode(raw: bytes) -> str:
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def iso_date(s: str) -> str | None:
    s = s.strip()
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def read_season(path: Path) -> tuple[list[str], list[dict]]:
    text = decode(path.read_bytes())
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    records = []
    for r in rows[1:]:
        if not any(cell.strip() for cell in r):
            continue                      # trailing blank rows
        rec = {header[i]: (r[i].strip() if i < len(r) else '') for i in range(len(header)) if header[i]}
        if not rec.get('HomeTeam') or not rec.get('Date'):
            continue                      # ragged junk rows
        records.append(rec)
    return header, records


def combine_league(root: Path, league: str) -> tuple[int, int] | None:
    raw_dir, prefix = LEAGUES[league]
    src_dir = root / 'data' / raw_dir
    files = sorted(src_dir.glob(f'{prefix}-*.csv'))
    if not files:
        print(f'[{league}] no season files in {src_dir} — run download_data.py first.')
        return None

    all_records, all_cols, bad_dates = [], [], 0
    seen_cols = set()
    for f in files:
        season = f.stem.removeprefix(f'{prefix}-')
        header, records = read_season(f)
        for c in header:
            if c and c not in seen_cols:
                seen_cols.add(c)
                all_cols.append(c)
        for rec in records:
            d = iso_date(rec.get('Date', ''))
            if d is None:
                bad_dates += 1
                continue
            rec['Date'] = d
            rec['League'] = league
            rec['Season'] = season
            # resolve one odds set per row
            rec['OddsH'] = rec['OddsD'] = rec['OddsA'] = rec['OddsSource'] = ''
            for h, dcol, a, tag in ODDS_SETS:
                if rec.get(h) and rec.get(dcol) and rec.get(a):
                    rec['OddsH'], rec['OddsD'], rec['OddsA'], rec['OddsSource'] = rec[h], rec[dcol], rec[a], tag
                    break
            all_records.append(rec)

    lead = ['League', 'Season'] + CORE_COLS + ['OddsH', 'OddsD', 'OddsA', 'OddsSource']
    rest = [c for c in all_cols if c not in lead]
    fieldnames = lead + rest

    out_dir = root / 'data' / 'combined'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{league}.csv'
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for rec in sorted(all_records, key=lambda r: (r['Season'], r['Date'])):
            w.writerow({k: rec.get(k, '') for k in fieldnames})

    n_odds = sum(1 for r in all_records if r['OddsSource'])
    print(f'[{league}] {out_path.name}: {len(all_records)} matches across {len(files)} seasons, '
          f'{n_odds} with odds'
          + (f', {bad_dates} rows dropped (unparseable date)' if bad_dates else ''))
    return len(all_records), len(files)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default=DEFAULT_ROOT, help=f'repo root (default: {DEFAULT_ROOT})')
    ap.add_argument('--league', choices=sorted(LEAGUES), help='only this league (default: all)')
    args = ap.parse_args()

    root = Path(args.root)
    leagues = [args.league] if args.league else sorted(LEAGUES)
    ok = True
    for league in leagues:
        ok = (combine_league(root, league) is not None) and ok
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
