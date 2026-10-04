"""Synthetic FD dataset generator for threshold tuning (Step 8).

Plants FDs while varying row count, cardinality, column count and noise,
then writes CSV datasets + a manifest with ground truth. The Step 8
tuning harness consumes these to draw precision/recall curves.

Stdlib only (offline-safe). Fixed seeds -> reproducible.

Usage:
  python scripts/testdb/generate_synthetic.py --out scripts/testdb/synthetic
  python scripts/testdb/generate_synthetic.py --out scripts/testdb/synthetic --sweep
"""

import argparse
import csv
import json
import random
from pathlib import Path

# (rows, determinant size, dependent cardinality class, noise, cols)
SWEEP = [
    (100, 1, "low", 0.00, 10),
    (100, 1, "low", 0.01, 10),
    (100, 1, "high", 0.00, 10),
    (300, 1, "low", 0.00, 12),
    (300, 1, "low", 0.02, 12),
    (300, 2, "low", 0.00, 12),
    (300, 2, "low", 0.03, 12),
    (1000, 2, "high", 0.00, 15),
    (1000, 2, "high", 0.01, 15),
    (1000, 3, "low", 0.00, 15),
    (5000, 2, "low", 0.01, 15),
    (5000, 3, "low", 0.00, 15),
]

CARD = {"low": 8, "high": 60}


def gen_dataset(rows, det_size, card_class, noise, n_cols, seed):
    rng = random.Random(seed)
    det_cards = [rng.choice([6, 10, 12, 20]) for _ in range(det_size)]
    dep_card = CARD[card_class]
    header = (
        [f"k{i}" for i in range(det_size)]
        + ["dep", "decoy"]
        + [f"f{i}" for i in range(n_cols - det_size - 2)]
    )
    data = []
    for _ in range(rows):
        det = tuple(rng.randrange(c) for c in det_cards)
        dep = (sum(det) * 7 + 3) % dep_card
        if rng.random() < noise:
            dep = rng.randrange(dep_card)
        decoy = rng.randrange(50)
        fillers = [rng.randrange(97) for _ in range(n_cols - det_size - 2)]
        data.append(
            [f"K{i}_{v}" for i, v in enumerate(det)]
            + [f"D_{dep}", f"X_{decoy}"]
            + [f"F{i}_{v}" for i, v in enumerate(fillers)]
        )
    truth = {
        "rows": rows,
        "det_size": det_size,
        "dep_cardinality": dep_card,
        "noise": noise,
        "cols": n_cols,
        "seed": seed,
        "planted_fd": {
            "determinant": [f"k{i}" for i in range(det_size)],
            "dependent": "dep",
        },
    }
    return header, data, truth


def write_dataset(out_dir, name, rows, det_size, card_class, noise, n_cols, seed):
    header, data, truth = gen_dataset(rows, det_size, card_class, noise, n_cols, seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(data)
    with open(out_dir / f"{name}.json", "w") as f:
        json.dump(truth, f, indent=2)
    return name, truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="scripts/testdb/synthetic")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--seed", type=int, default=20261004)
    args = ap.parse_args()

    out = Path(args.out)
    manifest = {}
    if args.sweep:
        for idx, (rows, det, card, noise, cols) in enumerate(SWEEP):
            name = f"syn_{idx:02d}_r{rows}_d{det}_{card}_n{int(noise*100):02d}"
            _, truth = write_dataset(
                out, name, rows, det, card, noise, cols, args.seed + idx
            )
            manifest[name] = truth
    else:
        _, truth = write_dataset(out, "syn_demo", 300, 1, "low", 0.01, 12, args.seed)
        manifest["syn_demo"] = truth
    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {len(manifest)} dataset(s) to {out}")


if __name__ == "__main__":
    main()
