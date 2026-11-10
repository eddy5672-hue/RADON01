#!/usr/bin/env python3
"""예측 모델 재학습.

    python tools/train.py                              # 동봉 모의데이터로 학습
    python tools/train.py --csv 내측정데이터.csv        # 실측 CSV로 재학습
결과는 output/model.json 에 저장되며 run.py 가 우선 사용한다. (입력 CSV는 수정하지 않음)
CSV 형식은 radon/mockdata.py 상단 설명 참조.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radon.mockdata import load_csv  # noqa: E402
from radon.predictor import RadonPredictor  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "mock_radon_30days.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "model.json"))
    a = ap.parse_args()
    data = load_csv(a.csv)
    print("현장별 데이터 수:", {k: len(v) for k, v in data.items()})
    p = RadonPredictor()
    p.train(data, source=os.path.basename(a.csv))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    p.save(a.out)
    print(json.dumps(p.metrics, ensure_ascii=False, indent=1))
    print("저장:", a.out)


if __name__ == "__main__":
    main()
