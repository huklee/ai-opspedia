"""dummy 지표 시계열 생성 (결정적, seed 고정). 출력: dummy/metrics/series.json (Prometheus query_range 형태).

시나리오: 09-29 랭킹 실패로 피드 미갱신 · products_v42 문서 수 30 % 감소 · 자동완성 DAG 일시정지(09-13 이후).
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
END = datetime(2026, 9, 29, 9, 0, tzinfo=KST)
DAYS = 14
rnd = random.Random(20260929)


def days():
    return [END - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]


def noise(base, spread):
    return base + rnd.uniform(-spread, spread)


def series():
    out = {}
    ts = days()
    # 추천 CTR: 4.1 % 근처, INC 없는 기간 안정, 오늘 피드 미갱신으로 하락
    out["reco_ctr"] = [noise(4.12, 0.08) for _ in ts[:-1]] + [3.62]
    out["reco_feed_freshness"] = [noise(24.3, 0.4) for _ in ts[:-1]] + [26.7]
    out["reco_coverage"] = [noise(41.5, 1.2) for _ in ts[:-1]] + [39.8]
    out["dag_success_rate"] = [100.0] * 5 + [91.7] + [100.0] * 7 + [75.0]  # 09-21 모델 등록 재시도, 오늘 2건 실패 + 1 upstream
    out["feature_store_finish"] = [noise(2.68, 0.06) for _ in ts[:-1]] + [2.69]
    out["feed_publish_finish"] = [noise(6.3, 0.08) for _ in ts[:-1]] + [None]
    base = [noise(119.8, 0.6) for _ in ts[:-1]]
    out["products_doc_count"] = base + [120.4]
    out["products_newest_doc_ratio"] = [noise(100.5, 0.8) for _ in ts[:-1]] + [70.3]
    # 자동완성: 09-13 빌드 이후 정지 → 매일 +1
    out["suggest_freshness"] = [(t - datetime(2026, 9, 13, 2, 20, tzinfo=KST)).days for t in ts]
    out["search_zero_rate"] = [noise(3.05, 0.12) for _ in ts[:-3]] + [3.21, 3.34, 3.41]  # 자동완성 정체 영향으로 완만 상승
    out["search_ctr"] = [noise(32.4, 0.6) for _ in ts[:-1]] + [31.9]
    out["search_p95_ms"] = [noise(182, 12) for _ in ts[:-1]] + [197]
    return ts, out


def main():
    ts, s = series()
    result = []
    for mid, vals in s.items():
        result.append({"metric": {"__name__": "opspedia_service_metric", "id": mid},
                       "values": [[int(t.timestamp()), None if v is None else f"{v:.4f}"] for t, v in zip(ts, vals)]})
    doc = {"status": "success", "fetched_at": END.isoformat(),
           "data": {"resultType": "matrix", "result": result}}
    p = Path(__file__).resolve().parents[1] / "dummy/metrics/series.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {p} ({len(result)} series × {len(ts)} points)")


if __name__ == "__main__":
    main()
