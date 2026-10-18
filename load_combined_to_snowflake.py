"""
Load data/combined/*.csv into PROD.SBX_BI.DD_HISTORY_LEAGUE_DATA.

Uses the same SSO connection as the other SBX_BI scripts
(externalbrowser, FR__DI, WH_TABLEAU_EXT_CORE).
"""

import re
from pathlib import Path

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

DEFAULT_ROOT = Path(r'C:\Users\DamianDamianov\Documents\football_predictor_damian')
TARGET_DATABASE = 'PROD'
TARGET_SCHEMA = 'SBX_BI'
TARGET_TABLE = 'DD_HISTORY_LEAGUE_DATA'

STRING_COLS = {
    'LEAGUE', 'SEASON', 'HOMETEAM', 'AWAYTEAM', 'FTR', 'ODDSSOURCE',
    'DIV', 'KICKOFF_TIME', 'HTR', 'REFEREE', 'COUNTRY', 'MATCH_DATE',
}

# Snowflake reserved words that appear as football-data column names
RESERVED_ALIASES = {
    'AS': 'AWAY_SHOTS',
    'DATE': 'MATCH_DATE',
    'TIME': 'KICKOFF_TIME',
}
SNOWFLAKE_RESERVED = {
    'AS', 'FROM', 'TO', 'BY', 'IS', 'OR', 'ON', 'NOT', 'ALL', 'ANY',
    'LIKE', 'TABLE', 'USER', 'ROLE', 'DATE', 'TIME', 'ORDER', 'GROUP',
    'HAVING', 'WHERE', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'VALUES',
    'CHECK', 'DEFAULT', 'GRANT', 'UNION', 'JOIN', 'CASE', 'WHEN', 'THEN',
    'ELSE', 'END', 'CAST', 'OVER', 'ROW', 'ROWS',
}


def snowflake_col(name: str) -> str:
    n = name.strip()
    n = n.replace('>2.5', '_OVER_2_5').replace('<2.5', '_UNDER_2_5')
    n = n.replace('>', '_GT_').replace('<', '_LT_')
    n = re.sub(r'[^A-Za-z0-9_]', '_', n)
    if n[0].isdigit():
        n = 'C_' + n
    n = n.upper()
    n = RESERVED_ALIASES.get(n, n)
    if n in SNOWFLAKE_RESERVED:
        n = n + '_COL'
    return n


def load_combined(root: Path) -> pd.DataFrame:
    files = sorted((root / 'data' / 'combined').glob('*.csv'))
    if not files:
        raise FileNotFoundError(f'No combined CSVs under {root / "data" / "combined"}')
    frames = [pd.read_csv(f, dtype=str, keep_default_na=False) for f in files]
    df = pd.concat(frames, ignore_index=True, sort=False)
    df.columns = [snowflake_col(c) for c in df.columns]
    # drop accidental duplicate names after sanitising
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.replace('', pd.NA)

    df['MATCH_DATE'] = pd.to_datetime(df['MATCH_DATE'], errors='coerce').dt.date
    for col in df.columns:
        if col in STRING_COLS:
            continue
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def main() -> None:
    df = load_combined(DEFAULT_ROOT)
    print(f'Prepared {len(df):,} rows x {len(df.columns)} columns')

    conn = snowflake.connector.connect(
        user='DAMIAN.DAMIANOV@SUPER.XYZ',
        authenticator='externalbrowser',
        account='WT74883-SB_PROD',
        role='FR__DI',
        warehouse='WH_TABLEAU_EXT_CORE',
        database=TARGET_DATABASE,
        schema=TARGET_SCHEMA,
    )
    cur = conn.cursor()
    try:
        cur.execute(f'USE DATABASE {TARGET_DATABASE}')
        cur.execute(f'USE SCHEMA {TARGET_SCHEMA}')
        success, _, nrows, _ = write_pandas(
            conn,
            df,
            table_name=TARGET_TABLE,
            database=TARGET_DATABASE,
            schema=TARGET_SCHEMA,
            overwrite=True,
            auto_create_table=True,
            quote_identifiers=False,
        )
        if not success:
            raise RuntimeError(
                f'Failed to load {TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}'
            )
        cur.execute(
            f'SELECT COUNT(*), MIN(MATCH_DATE), MAX(MATCH_DATE), '
            f'COUNT(DISTINCT LEAGUE) '
            f'FROM {TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}'
        )
        n, dmin, dmax, n_leagues = cur.fetchone()
        print(
            f'Loaded {nrows:,} rows into '
            f'{TARGET_DATABASE}.{TARGET_SCHEMA}.{TARGET_TABLE}'
        )
        print(f'Snowflake check: {n:,} rows, {n_leagues} leagues, {dmin} .. {dmax}')
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
