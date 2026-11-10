#!/usr/bin/env python3
"""모의데이터 생성:  python tools/make_mock_data.py --days 30 --out output/mock.csv"""
import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radon.mockdata import generate, save_csv  # noqa: E402
from radon.sim import TRAINING_SITES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "mock_radon.csv"))
    a = ap.parse_args()
    data = generate(TRAINING_SITES, datetime.strptime(a.start, "%Y-%m-%d"), a.days, seed=a.seed)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    save_csv(data, a.out)
    print("저장:", a.out, {k: len(v) for k, v in data.items()})


if __name__ == "__main__":
    main()
