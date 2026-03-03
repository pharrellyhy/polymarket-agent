#!/usr/bin/env python3
"""Compare paper-trading test runs across Phase 1-3 config profiles.

Usage:
    python scripts/compare_test_runs.py [--db-dir DIR]

Reads SQLite DBs named baseline.db, phase1.db, phase2.db, phase3.db from
the specified directory (default: data/) and prints a side-by-side metrics table.
"""

import argparse
import math
import sqlite3
import sys
from pathlib import Path

PROFILES = ["baseline", "phase1", "phase2", "phase3"]


def query_metrics(db_path: Path) -> dict[str, object]:
    """Extract key metrics from a test run database."""
    if not db_path.exists():
        return {"error": f"DB not found: {db_path}"}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Signal metrics (generated only, to avoid double-counting executed/rejected log rows)
        signals = conn.execute(
            "SELECT COUNT(*) as cnt FROM signal_log WHERE status = 'generated'"
        ).fetchone()
        signal_count = signals["cnt"] if signals else 0

        conf_stats = conn.execute(
            "SELECT AVG(confidence) as mean, AVG(confidence * confidence) as mean_sq "
            "FROM signal_log WHERE status = 'generated'"
        ).fetchone()
        mean = float(conf_stats["mean"]) if conf_stats and conf_stats["mean"] is not None else 0.0
        mean_sq = float(conf_stats["mean_sq"]) if conf_stats and conf_stats["mean_sq"] is not None else 0.0
        variance = max(mean_sq - (mean * mean), 0.0)
        mean_conf = round(mean, 4)
        std_conf = round(math.sqrt(variance), 4)

        # Trade metrics
        trades = conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()
        trade_count = trades["cnt"] if trades else 0

        avg_size = conn.execute("SELECT AVG(size) as avg FROM trades").fetchone()
        mean_size = round(avg_size["avg"], 2) if avg_size and avg_size["avg"] else 0.0

        unique_markets = conn.execute("SELECT COUNT(DISTINCT market_id) as cnt FROM trades").fetchone()
        market_count = unique_markets["cnt"] if unique_markets else 0

        # Exit manager metrics
        trailing = conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE reason LIKE '%trailing_stop%'"
        ).fetchone()
        trailing_count = trailing["cnt"] if trailing else 0

        # TA signal count (generated only)
        regime_signals = conn.execute(
            "SELECT COUNT(*) as cnt FROM signal_log WHERE strategy = 'technical_analyst' AND status = 'generated'"
        ).fetchone()
        ta_signal_count = regime_signals["cnt"] if regime_signals else 0

        conn.close()
    except sqlite3.Error as exc:
        return {"error": f"Could not read {db_path}: {exc}"}

    return {
        "signals": signal_count,
        "mean_conf": mean_conf,
        "std_conf": std_conf,
        "trades": trade_count,
        "mean_size": mean_size,
        "markets": market_count,
        "trailing_exits": trailing_count,
        "ta_signals": ta_signal_count,
    }


def print_comparison(db_dir: Path) -> None:
    """Print side-by-side comparison table."""
    results = {}
    for profile in PROFILES:
        db_path = db_dir / f"{profile}.db"
        results[profile] = query_metrics(db_path)

    # Check for errors
    for profile, metrics in results.items():
        if "error" in metrics:
            print(f"WARNING: {metrics['error']}")

    # Print table
    header = f"{'Metric':<25}" + "".join(f"{p:>12}" for p in PROFILES)
    separator = "-" * len(header)

    print(separator)
    print(header)
    print(separator)

    rows = [
        ("Signals generated", "signals"),
        ("Mean confidence", "mean_conf"),
        ("Confidence std dev", "std_conf"),
        ("Trades executed", "trades"),
        ("Mean position size", "mean_size"),
        ("Unique markets", "markets"),
        ("Trailing stop exits", "trailing_exits"),
        ("TA signals", "ta_signals"),
    ]

    for label, key in rows:
        vals = []
        for profile in PROFILES:
            m = results[profile]
            if "error" in m:
                vals.append("N/A")
            else:
                v = m[key]
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
        print(f"{label:<25}" + "".join(f"{v:>12}" for v in vals))

    print(separator)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare paper-trading test runs.")
    parser.add_argument("--db-dir", type=Path, default=Path("data"), help="Directory containing test DBs")
    args = parser.parse_args()

    if not args.db_dir.exists():
        print(f"ERROR: DB directory not found: {args.db_dir}", file=sys.stderr)
        sys.exit(1)

    print_comparison(args.db_dir)


if __name__ == "__main__":
    main()
