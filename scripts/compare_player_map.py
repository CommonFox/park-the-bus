"""Compare ptb's derived player map against sacked-in-the-morning's.

sacked-in-the-morning's map_player_external is itself auto-derived by name
matching -- every row carries an "auto: ... name match" note -- so it is a
baseline to beat, not a source of truth. Coverage below it means the new
matcher is worse; conflicts are the rows worth reading by hand.

Coverage is computed over dim_player WHERE fpl_code IS NOT NULL. ptb's
dimension also holds Big-5 players who never appear in FPL, and counting those
would make the two percentages incomparable.

Usage:
    uv run python scripts/compare_player_map.py PTB_DB SITM_DB
"""
from __future__ import annotations

import sys

import duckdb

BASELINE = {"understat": 67.0, "fotmob": 77.0}


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    ptb_path, sitm_path = argv[1], argv[2]

    con = duckdb.connect(ptb_path, read_only=True)
    # ATTACH is DDL and does not accept a bound parameter for the path; the
    # literal is escaped by doubling embedded single quotes, DuckDB's own
    # string-literal escaping convention.
    con.execute("ATTACH '{}' AS sitm (READ_ONLY)".format(sitm_path.replace("'", "''")))

    spine = con.execute(
        "SELECT count(*) FROM dim_player WHERE fpl_code IS NOT NULL").fetchone()[0]
    if not spine:
        print("no FPL spine in the warehouse -- run `ptb rebuild` first")
        return 1

    print("FPL spine players: {}".format(spine))
    print("\nCOVERAGE (of the FPL spine)")
    for source, baseline in sorted(BASELINE.items()):
        mapped = con.execute(
            "SELECT count(DISTINCT m.player_id) FROM map_player_source m "
            "JOIN dim_player p ON p.player_id = m.player_id "
            "WHERE m.source = ? AND p.fpl_code IS NOT NULL", [source]
        ).fetchone()[0]
        pct = 100.0 * mapped / spine
        verdict = "PASS" if pct > baseline else "FAIL"
        print("  {:<10} {:>5} / {:<5} {:>6.1f}%   baseline {:.0f}%   {}".format(
            source, mapped, spine, pct, baseline, verdict))

    total = con.execute("SELECT count(*) FROM dim_player").fetchone()[0]
    print("\n  (whole dimension: {} players, incl. non-FPL)".format(total))

    print("\nAGREEMENT WITH THE BASELINE (understat)")
    rows = con.execute(
        "WITH ours AS ("
        "  SELECT p.fpl_code, m.source_player_id AS understat_id "
        "  FROM map_player_source m "
        "  JOIN dim_player p ON p.player_id = m.player_id "
        "  WHERE m.source = 'understat' AND p.fpl_code IS NOT NULL"
        "), theirs AS ("
        "  SELECT player_code AS fpl_code, understat_id "
        "  FROM sitm.map_player_external WHERE understat_id IS NOT NULL"
        ") "
        "SELECT "
        "  count(*) FILTER (WHERE o.understat_id = t.understat_id) AS agree, "
        "  count(*) FILTER (WHERE t.fpl_code IS NULL)              AS ours_only, "
        "  count(*) FILTER (WHERE o.fpl_code IS NULL)              AS theirs_only, "
        "  count(*) FILTER (WHERE o.understat_id IS NOT NULL "
        "                    AND t.understat_id IS NOT NULL "
        "                    AND o.understat_id <> t.understat_id) AS conflict "
        "FROM ours o FULL OUTER JOIN theirs t ON t.fpl_code = o.fpl_code"
    ).fetchone()
    for label, value in zip(("agree", "ours only", "theirs only", "CONFLICT"), rows):
        print("  {:<12} {}".format(label, value))

    if rows[3]:
        print("\nCONFLICTS -- read these by hand")
        for code, ours, theirs in con.execute(
            "WITH ours AS ("
            "  SELECT p.fpl_code, p.canonical_name, m.source_player_id AS understat_id "
            "  FROM map_player_source m "
            "  JOIN dim_player p ON p.player_id = m.player_id "
            "  WHERE m.source = 'understat' AND p.fpl_code IS NOT NULL"
            ") "
            "SELECT o.canonical_name, o.understat_id, t.understat_id "
            "FROM ours o JOIN sitm.map_player_external t ON t.player_code = o.fpl_code "
            "WHERE t.understat_id IS NOT NULL AND t.understat_id <> o.understat_id "
            "ORDER BY o.canonical_name LIMIT 50"
        ).fetchall():
            print("  {:<28} ours={:<10} theirs={}".format(code, ours, theirs))

    print("\nAUDIT SAMPLE -- 50 scored-tier matches, check by hand for precision")
    print("Bar: >= 98%, i.e. at most one wrong. Deterministic tiers are excluded;")
    print("they are not where wrong answers come from.\n")
    for name, source, sid, confidence in con.execute(
        # USING SAMPLE applies to the table scan before WHERE, not to the
        # filtered result -- sampling the join+filter directly starved the
        # sample down to population * filter-selectivity rows instead of 50.
        # Wrapping it in a subquery samples the filtered rows themselves.
        "SELECT canonical_name, source, source_player_id, confidence FROM ("
        "  SELECT p.canonical_name, m.source, m.source_player_id, m.confidence "
        "  FROM map_player_source m "
        "  JOIN dim_player p ON p.player_id = m.player_id "
        "  WHERE m.method = 'scored'"
        ") USING SAMPLE 50 ROWS (reservoir, 42)"
    ).fetchall():
        print("  {:<28} {:<10} {:<12} {:.3f}".format(name, source, sid, confidence))

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
