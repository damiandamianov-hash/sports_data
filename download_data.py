"""
download_data.py — one-off historical backfill from football-data.co.uk
=======================================================================

Downloads 7 seasons (2020-21 .. 2026-27) for the football predictor leagues,
validates each file against the known data quirks, and stores everything
in the repo-compatible layout:

    <root>/data/<RAW_DIR>/<PREFIX>-<season>.csv     (raw bytes, exactly as served)
    <root>/data/notes.txt                           (column documentation)
    <root>/data/download_report.csv                 (per-file validation summary)

Default <root> is C:\\Users\\DamianDamianov\\Documents\\football_predictor_damian
so the folder can later serve directly as the repo root (load_league_matches
and scripts/update_data.py will find the files where they expect them).

Usage (run locally, needs internet):
    python download_data.py                       # everything
    python download_data.py --league greek        # one league
    python download_data.py --seasons 2018-2019 2019-2020   # extra history
    python download_data.py --force               # re-download existing files

Data source: https://www.football-data.co.uk — free for personal/research
use; keep the attribution in your README.
"""

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import requests

BASE_URL = 'https://www.football-data.co.uk/mmz4281'
NOTES_URL = 'https://www.football-data.co.uk/notes.txt'

DEFAULT_ROOT = r'C:\Users\DamianDamianov\Documents\football_predictor_damian'

# league key -> (division code, raw_dir, file_prefix)
# Folders under data/ (excluding combined/):
#   ARGENTINA, BELGIUM, BRAZIL, BUNDESLIGA, BUNDESLIGA-2, CHAMPIONSHIP,
#   EKSTRAKLASA, EREDIVISIE, GREEK, JAPAN, LA-LIGA, LE-CHAMPIONNAT,
#   LIGUE-2, PORTUGAL, PREMIER LEAGUE, ROMANIA, SCOTLAND, SEGUNDA,
#   SERIE-A, SERIE-B, TURKEY, USA
# Main leagues: one CSV per season at mmz4281/<yyee>/<code>.csv
LEAGUES = {
    'belgium':        ('B1',  'BELGIUM',        'BELGIUM'),
    'bundesliga':     ('D1',  'BUNDESLIGA',     'BUNDESLIGA'),
    'bundesliga_2':   ('D2',  'BUNDESLIGA-2',   'BUNDESLIGA-2'),
    'championship':   ('E1',  'CHAMPIONSHIP',   'CHAMPIONSHIP'),
    'eredivisie':     ('N1',  'EREDIVISIE',     'EREDIVISIE'),
    'greek':          ('G1',  'GREEK',          'GREEK'),
    'la_liga':        ('SP1', 'LA-LIGA',        'LA-LIGA'),
    'segunda':        ('SP2', 'SEGUNDA',        'SEGUNDA'),
    'ligue_1':        ('F1',  'LE-CHAMPIONNAT', 'LE-CHAMPIONNAT'),
    'ligue_2':        ('F2',  'LIGUE-2',        'LIGUE-2'),
    'portugal':       ('P1',  'PORTUGAL',       'PORTUGAL'),
    'premier_league': ('E0',  'PREMIER LEAGUE', 'PL'),
    'scotland':       ('SC0', 'SCOTLAND',       'SCOTLAND'),
    'serie_a':        ('I1',  'SERIE-A',        'SERIE-A'),
    'serie_b':        ('I2',  'SERIE-B',        'SERIE-B'),
    'turkey':         ('T1',  'TURKEY',         'TURKEY'),
}

# Extra leagues: football-data.co.uk publishes one all-seasons file at /new/<code>.csv
# (Poland Ekstraklasa, Romania Superliga). We split + remap into the same
# <PREFIX>-<season>.csv layout as the main leagues.
EXTRA_LEAGUES = {
    'romania':     ('ROU', 'ROMANIA',     'ROMANIA'),
    'ekstraklasa': ('POL', 'EKSTRAKLASA', 'EKSTRAKLASA'),
    'brazil':      ('BRA', 'BRAZIL',      'BRAZIL'),
    'argentina':   ('ARG', 'ARGENTINA',   'ARGENTINA'),
    'japan':       ('JPN', 'JAPAN',       'JAPAN'),
    'usa':         ('USA', 'USA',         'USA'),
}

# Keep Argentina to Liga Profesional (Apertura / first-division season)
EXTRA_LEAGUE_FILTERS = {
    'argentina': 'Liga Profesional',
}
# Source labels that should land in our YYYY-YYYY files
EXTRA_SEASON_ALIASES = {
    'argentina': {'2019-2020': '2020-2021'},
}

EXTRA_URL = 'https://www.football-data.co.uk/new/{code}.csv'

# Extra-file columns -> main-file names used by load_league_matches / validate()
EXTRA_COL_RENAME = {
    'Home': 'HomeTeam', 'Away': 'AwayTeam',
    'HG': 'FTHG', 'AG': 'FTAG', 'Res': 'FTR',
    'B365CH': 'B365H', 'B365CD': 'B365D', 'B365CA': 'B365A',
    'AvgCH': 'AvgH', 'AvgCD': 'AvgD', 'AvgCA': 'AvgA',
    'PSCH': 'PSH', 'PSCD': 'PSD', 'PSCA': 'PSA',
    'MaxCH': 'MaxH', 'MaxCD': 'MaxD', 'MaxCA': 'MaxA',
}

ALL_LEAGUES = {**LEAGUES, **EXTRA_LEAGUES}

DEFAULT_SEASONS = ['2020-2021', '2021-2022', '2022-2023',
                   '2023-2024', '2024-2025', '2025-2026', '2026-2027']

REQUIRED_COLS = ['Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'FTR']
STAT_COLS     = ['HS', 'AS', 'HST', 'AST', 'HC', 'AC']
# Odds preference order: Bet365 closing -> market average -> Pinnacle
ODDS_SETS = [('B365H', 'B365D', 'B365A'),
             ('AvgH',  'AvgD',  'AvgA'),
             ('PSH',   'PSD',   'PSA'),
             ('B365CH', 'B365CD', 'B365CA'),  # extra-league closing odds
             ('AvgCH',  'AvgCD',  'AvgCA'),
             ('PSCH',   'PSCD',   'PSCA'),
             ('BbAvH', 'BbAvD', 'BbAvA')]   # very old files

UA = {'User-Agent': 'Mozilla/5.0 (football-predictor backfill script)'}


def season_code(season: str) -> str:
    """'2025-2026' -> '2526', matching football-data.co.uk's URL scheme."""
    start, end = season.split('-')
    return start[-2:] + end[-2:]


def normalize_season(value: str) -> str:
    """Map source season labels onto our YYYY-YYYY keys.
    '2025/2026' / '2025-2026' stay '2025-2026'.
    Calendar-year extra leagues ('2026') become '2026-2027'."""
    v = value.strip().replace('/', '-')
    if len(v) == 4 and v.isdigit():
        y = int(v)
        return f'{y}-{y + 1}'
    return v


def split_extra_league(raw: bytes, code: str, seasons: list[str],
                       league_filter: str | None = None,
                       season_aliases: dict[str, str] | None = None) -> dict[str, bytes]:
    """Split an extra-league all-seasons CSV into per-season files with
    main-league column names (HomeTeam/FTHG/FTR/B365H)."""
    text = decode(raw)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    src_header = rows[0]
    header = [EXTRA_COL_RENAME.get(h, h) for h in src_header]
    if 'Div' not in header:
        header = ['Div'] + header
        add_div = True
    else:
        add_div = False
    try:
        season_idx = header.index('Season')
    except ValueError:
        return {}
    league_idx = header.index('League') if 'League' in header else None

    wanted = set(seasons)
    grouped: dict[str, list[list[str]]] = {s: [] for s in seasons}
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        if add_div:
            row = [code] + row
        if season_idx >= len(row):
            continue
        if (league_filter and league_idx is not None
                and league_idx < len(row)
                and row[league_idx].strip() != league_filter):
            continue
        key = normalize_season(row[season_idx])
        if season_aliases:
            key = season_aliases.get(key, key)
        if key in wanted:
            grouped[key].append(row)

    out: dict[str, bytes] = {}
    for season, data_rows in grouped.items():
        if not data_rows:
            continue
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator='\n')
        writer.writerow(header)
        writer.writerows(data_rows)
        out[season] = ('\ufeff' + buf.getvalue()).encode('utf-8')
    return out


def fetch(url: str, retries: int = 3) -> bytes | None:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=30, headers=UA)
            if resp.status_code == 200 and resp.content.strip():
                return resp.content
            print(f'    HTTP {resp.status_code} from {url}')
            return None
        except requests.RequestException as e:
            print(f'    attempt {attempt}/{retries} failed: {e}')
            time.sleep(2 * attempt)
    return None


def decode(raw: bytes) -> str:
    """Handle the site's mixed encodings (UTF-8 with BOM on new files,
    Windows-1252 on older ones)."""
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def parse_date(s: str) -> bool:
    """Accept both %d/%m/%Y (new) and %d/%m/%y (older seasons)."""
    from datetime import datetime
    for fmt in ('%d/%m/%Y', '%d/%m/%y'):
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def validate(raw: bytes, league: str, season: str) -> dict:
    """Parse + sanity-check one downloaded file. Returns a report dict;
    report['ok'] is False when the file should not be kept."""
    text = decode(raw)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader]
    header = rows[0] if rows else []
    # trailing blank / ragged comma-only rows are common — count, don't fail
    data_rows = [r for r in rows[1:] if any(cell.strip() for cell in r)]
    blank_rows = len(rows) - 1 - len(data_rows)

    report = {
        'league': league, 'season': season,
        'matches': len(data_rows), 'blank_rows_dropped': blank_rows,
        'ok': True, 'problems': [],
    }

    missing = [c for c in REQUIRED_COLS if c not in header]
    if missing:
        report['ok'] = False
        report['problems'].append(f'missing required columns: {missing}')
        return report

    idx = {c: header.index(c) for c in header if c}

    # odds coverage — first bookmaker set whose columns exist and have values
    def odds_set_usable(cols) -> bool:
        if not all(c in idx for c in cols):
            return False
        for r in data_rows:
            if all(idx[c] < len(r) and r[idx[c]].strip() for c in cols):
                return True
        return False

    odds_set = next((s for s in ODDS_SETS if odds_set_usable(s)), None)
    report['odds_columns'] = '/'.join(odds_set) if odds_set else 'NONE'
    if odds_set is None:
        report['problems'].append('no usable odds columns (market RPS impossible for this season)')

    stat_missing = [c for c in STAT_COLS if c not in idx]
    if stat_missing:
        report['problems'].append(f'shot/corner columns missing: {stat_missing} '
                                  f'(the base+shots feature variant will impute these)')

    bad_dates = 0
    odds_missing_rows = 0
    bad_results = 0
    for r in data_rows:
        if len(r) <= max(idx[c] for c in REQUIRED_COLS):
            bad_results += 1
            continue
        if not parse_date(r[idx['Date']].strip()):
            bad_dates += 1
        if r[idx['FTR']].strip() not in ('H', 'D', 'A'):
            bad_results += 1
        if odds_set and any(not r[idx[c]].strip() for c in odds_set if idx[c] < len(r)):
            odds_missing_rows += 1

    report['rows_with_unparseable_date'] = bad_dates
    report['rows_missing_odds'] = odds_missing_rows
    report['rows_bad_result'] = bad_results
    if bad_dates:
        report['problems'].append(f'{bad_dates} rows with unparseable dates')
    if bad_results:
        report['ok'] = bad_results < len(data_rows) * 0.05  # tolerate stray junk rows
        report['problems'].append(f'{bad_results} rows with malformed result/short row')
    if odds_missing_rows:
        report['problems'].append(f'{odds_missing_rows} rows missing odds '
                                  f'(expected on Greek files — market RPS n will shrink)')
    # a full league season is 306 (18 teams) or 380 (20 teams) matches; a
    # current season may legitimately be partial
    if len(data_rows) < 100:
        report['problems'].append(f'only {len(data_rows)} matches — partial or truncated file')
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default=DEFAULT_ROOT, help=f'destination root (default: {DEFAULT_ROOT})')
    ap.add_argument('--league', choices=sorted(ALL_LEAGUES), help='only this league (default: all)')
    ap.add_argument('--seasons', nargs='+', default=DEFAULT_SEASONS,
                    help="seasons like 2024-2025 (default: 2020-2021 .. 2026-2027)")
    ap.add_argument('--force', action='store_true', help='re-download files that already exist')
    args = ap.parse_args()

    root = Path(args.root)
    data_dir = root / 'data'
    leagues = [args.league] if args.league else sorted(ALL_LEAGUES)
    reports, failed = [], []
    extra_cache: dict[str, bytes | None] = {}

    # column documentation
    notes_dest = data_dir / 'notes.txt'
    if not notes_dest.exists() or args.force:
        notes = fetch(NOTES_URL)
        if notes:
            data_dir.mkdir(parents=True, exist_ok=True)
            notes_dest.write_bytes(notes)
            print(f'Saved column documentation -> {notes_dest}')

    for league in leagues:
        is_extra = league in EXTRA_LEAGUES
        code, raw_dir, prefix = ALL_LEAGUES[league]
        dest_dir = data_dir / raw_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        extra_by_season: dict[str, bytes] = {}
        if is_extra:
            need_fetch = args.force or any(
                not (dest_dir / f'{prefix}-{season}.csv').exists()
                for season in args.seasons
            )
            if need_fetch:
                url = EXTRA_URL.format(code=code)
                print(f'[{league}] downloading extra-league file {url}')
                extra_raw = extra_cache.get(code)
                if extra_raw is None and code not in extra_cache:
                    extra_raw = fetch(url)
                    extra_cache[code] = extra_raw
                    time.sleep(1)
                if extra_raw is None:
                    for season in args.seasons:
                        tag = f'[{league} {season}]'
                        print(f'{tag} FAILED to download extra-league source.')
                        failed.append((league, season, 'download failed'))
                    continue
                extra_by_season = split_extra_league(
                    extra_raw, code, args.seasons,
                    league_filter=EXTRA_LEAGUE_FILTERS.get(league),
                    season_aliases=EXTRA_SEASON_ALIASES.get(league),
                )

        for season in args.seasons:
            dest = dest_dir / f'{prefix}-{season}.csv'
            tag = f'[{league} {season}]'
            if dest.exists() and not args.force:
                print(f'{tag} exists, skipping ({dest.name}); use --force to re-download.')
                raw = dest.read_bytes()
            elif is_extra:
                raw = extra_by_season.get(season)
                if raw is None:
                    print(f'{tag} FAILED: season not present in extra-league file.')
                    failed.append((league, season, 'season missing from extra-league file'))
                    continue
            else:
                url = f'{BASE_URL}/{season_code(season)}/{code}.csv'
                print(f'{tag} downloading {url}')
                raw = fetch(url)
                if raw is None:
                    print(f'{tag} FAILED to download.')
                    failed.append((league, season, 'download failed'))
                    continue
                time.sleep(1)   # be polite to the source

            report = validate(raw, league, season)
            reports.append(report)
            if report['ok']:
                # main leagues: store raw bytes exactly as served.
                # extra leagues: store the remapped per-season split.
                dest.write_bytes(raw)
                probs = ('; '.join(report['problems'])) or 'clean'
                print(f"{tag} OK: {report['matches']} matches, odds={report['odds_columns']} — {probs}")
            else:
                failed.append((league, season, '; '.join(report['problems'])))
                print(f"{tag} REJECTED (not saved): {'; '.join(report['problems'])}")

    # summary report
    if reports:
        rep_path = data_dir / 'download_report.csv'
        keys = ['league', 'season', 'matches', 'odds_columns', 'rows_missing_odds',
                'rows_with_unparseable_date', 'rows_bad_result', 'blank_rows_dropped', 'ok', 'problems']
        with open(rep_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in reports:
                r = dict(r); r['problems'] = ' | '.join(r['problems'])
                w.writerow({k: r.get(k, '') for k in keys})
        print(f'\nWrote validation summary -> {rep_path}')

    print(f'\nDone: {len(reports) - len(failed)} files OK, {len(failed)} failed.')
    for league, season, why in failed:
        print(f'  FAILED {league} {season}: {why}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
