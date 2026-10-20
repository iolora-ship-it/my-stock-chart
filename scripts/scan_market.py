# -*- coding: utf-8 -*-
"""
市場スキャンバッチ処理（GitHub Actionsで毎晩自動実行）

東証に上場している全銘柄（ETF・REIT・PRO Marketなどを除く約3,700銘柄）を対象に、
「まだ誰も注目していなさそうな低位株」を見つけるための3つのシグナルを一括でチェックし、
該当した銘柄だけを market_scan_results.csv に書き出す。

このスクリプトはapp.py（Streamlitアプリ本体）とは完全に独立して動く。
app.pyはページを開くたびにその場でyfinanceからデータを取りに行く作りだが、
約3,700銘柄をその場でスキャンするのは重すぎて非現実的（数分〜それ以上かかる）。
そのため、スキャンはGitHub Actionsの夜間バッチとして裏側で済ませておき、
アプリ側は結果ファイル（market_scan_results.csv）を読むだけにする。

3つのシグナル（それぞれ独立に判定。複数該当することもある）:
  1. quiet  : 値動きはほとんど変わらないのに出来高だけ普段より増えている
              （app.pyのメインダッシュボードにある「静かな出来高増加」バッジと同じロジック。
               ただしメインダッシュボードは時価総額500億円以上の大型株だけが対象だが、
               ここでは低位株も対象にしたいため、その時価総額フィルタは外している）
  2. spike  : 直近の出来高が、それ以前の平均出来高に対して急増している
              （値動きの大きさは問わない。出来高の変化だけを見る）
  3. newlisting : 取得できる価格データの期間が短い銘柄＝上場してまだ日が浅い可能性がある銘柄
              （yfinanceから正確な上場日を銘柄ごとに取得するのは数が多すぎて非現実的なため、
               「取得できたデータの開始日が直近約6か月以内」であることを代わりの目安にしている。
               データが取得しづらい銘柄でも同じ見え方になってしまうため、必ずしも正確なIPO日ではない点に注意）

対象は株価3,000円以下（低位株の目安。app.pyの低位株ページと同じ上限）。
"""
import datetime as dt
import io
import sys
import time

import pandas as pd
import requests
import yfinance as yf

JPX_LISTED_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"

PRICE_CAP = 3000.0  # 低位株の目安（app.pyの低位株ページのスライダー上限と合わせる）
MIN_AVG_VALUE = 1_000_000  # 平均売買代金がこれを下回る銘柄は、値が付きにくく判定が不安定なため除外(100万円/日)

QUIET_LOOKBACK_DAYS = 5
QUIET_BASELINE_DAYS = 21
QUIET_VOLUME_RATIO = 1.8
QUIET_PRICE_RANGE_PCT = 6.0

SPIKE_VOLUME_RATIO = 3.0

NEWLISTING_MAX_DAYS = 185  # 取得できた最古データが、これより新しければ「新規上場の可能性」とみなす

CHUNK_SIZE = 150
CHUNK_PAUSE_SEC = 2
MAX_RETRY = 3

OUTPUT_CSV = "market_scan_results.csv"


def fetch_jpx_universe() -> pd.DataFrame:
    """JPXの「東証上場銘柄一覧」Excelを取得し、ETF・REIT・PRO Marketなどを除いた
    普通株式（内国株式・外国株式）だけに絞り込む。"""
    resp = requests.get(
        JPX_LISTED_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; stock-chart-market-scan/1.0)"},
        timeout=60,
    )
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content))
    df = df.rename(
        columns={
            "コード": "code",
            "銘柄名": "name",
            "市場・商品区分": "market",
            "33業種区分": "sector33",
        }
    )
    mask = df["market"].astype(str).str.contains("内国株式|外国株式", na=False)
    universe = df.loc[mask, ["code", "name", "market", "sector33"]].copy()
    universe["code"] = universe["code"].astype(str).str.strip()
    universe["name"] = universe["name"].astype(str).str.strip()
    universe["sector33"] = universe["sector33"].astype(str).str.strip()
    return universe.reset_index(drop=True)


def _quiet_pattern(hist: pd.DataFrame):
    total_needed = QUIET_LOOKBACK_DAYS + QUIET_BASELINE_DAYS
    if len(hist) < total_needed:
        return False, None
    recent = hist.iloc[-QUIET_LOOKBACK_DAYS:]
    prior = hist.iloc[-total_needed:-QUIET_LOOKBACK_DAYS]
    prior_avg_vol = prior["Volume"].mean()
    if not prior_avg_vol or prior_avg_vol <= 0:
        return False, None
    recent_avg_vol = recent["Volume"].mean()
    vol_ratio = recent_avg_vol / prior_avg_vol
    recent_avg_price = recent["Close"].mean()
    if not recent_avg_price or recent_avg_price <= 0:
        return False, None
    price_range_pct = (recent["High"].max() - recent["Low"].min()) / recent_avg_price * 100
    if vol_ratio >= QUIET_VOLUME_RATIO and price_range_pct <= QUIET_PRICE_RANGE_PCT:
        reason = (
            f"直近{QUIET_LOOKBACK_DAYS}営業日の平均出来高が、その前の{QUIET_BASELINE_DAYS}営業日平均の"
            f"{vol_ratio:.1f}倍に増えている一方、値幅は{price_range_pct:.1f}%に収まっています"
        )
        return True, reason
    return False, None


def _spike_pattern(hist: pd.DataFrame):
    if len(hist) < 22:
        return False, None
    prior_volume = hist["Volume"].iloc[-21:-1]
    if prior_volume.empty or prior_volume.mean() <= 0:
        return False, None
    latest_volume = hist["Volume"].iloc[-1]
    ratio = latest_volume / prior_volume.mean()
    if ratio >= SPIKE_VOLUME_RATIO:
        return True, f"直近の出来高が、それ以前20営業日平均の{ratio:.1f}倍に急増"
    return False, None


def _newlisting_pattern(hist: pd.DataFrame, today: dt.date):
    if hist.empty:
        return False, None
    first_date = hist.index[0]
    if hasattr(first_date, "date"):
        first_date = first_date.date()
    days = (today - first_date).days
    if days <= NEWLISTING_MAX_DAYS:
        return True, f"取得できる価格データが{first_date}以降のみ（上場間もない可能性）"
    return False, None


def scan(universe: pd.DataFrame) -> pd.DataFrame:
    today = dt.date.today()
    start = today - dt.timedelta(days=270)  # 新規上場判定に必要な約9か月分を確保
    end = today

    codes = universe["code"].tolist()
    results = []

    for i in range(0, len(codes), CHUNK_SIZE):
        chunk = codes[i : i + CHUNK_SIZE]
        tickers = [f"{c}.T" for c in chunk]
        raw = None
        for attempt in range(MAX_RETRY):
            try:
                raw = yf.download(
                    tickers,
                    start=start,
                    end=end + dt.timedelta(days=1),
                    progress=False,
                    auto_adjust=False,
                    group_by="ticker",
                    threads=True,
                )
                break
            except Exception as e:
                print(f"[warn] chunk {i}: download failed (attempt {attempt+1}): {e}", file=sys.stderr)
                time.sleep(5)
        if raw is None or raw.empty:
            print(f"[warn] chunk {i}: skipped (no data)", file=sys.stderr)
            continue

        for code in chunk:
            ticker = f"{code}.T"
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if ticker not in raw.columns.get_level_values(0):
                        continue
                    hist = raw[ticker]
                else:
                    hist = raw
                hist = hist.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            except Exception:
                continue
            if hist.empty:
                continue
            hist = hist.sort_index()

            latest_close = float(hist["Close"].iloc[-1])
            if latest_close > PRICE_CAP:
                continue

            recent20 = hist.tail(20)
            avg_value = float((recent20["Volume"] * recent20["Close"]).mean())
            if not avg_value or avg_value < MIN_AVG_VALUE:
                continue

            quiet_flag, quiet_reason = _quiet_pattern(hist)
            spike_flag, spike_reason = _spike_pattern(hist)
            new_flag, new_reason = _newlisting_pattern(hist, today)

            if not (quiet_flag or spike_flag or new_flag):
                continue

            row = universe.loc[universe["code"] == code].iloc[0]
            results.append(
                {
                    "code": code,
                    "name": row["name"],
                    "sector33": row["sector33"],
                    "market": row["market"],
                    "price": round(latest_close, 1),
                    "avg_value": round(avg_value, 0),
                    "signal_quiet": quiet_flag,
                    "quiet_reason": quiet_reason or "",
                    "signal_spike": spike_flag,
                    "spike_reason": spike_reason or "",
                    "signal_newlisting": new_flag,
                    "newlisting_reason": new_reason or "",
                    "scan_date": today.isoformat(),
                }
            )

        print(f"[info] processed {min(i + CHUNK_SIZE, len(codes))}/{len(codes)} tickers, "
              f"{len(results)} hits so far", file=sys.stderr)
        time.sleep(CHUNK_PAUSE_SEC)

    return pd.DataFrame(results)


def main():
    print("[info] fetching JPX listed-company universe...", file=sys.stderr)
    universe = fetch_jpx_universe()
    print(f"[info] universe size: {len(universe)}", file=sys.stderr)

    results = scan(universe)
    print(f"[info] total hits: {len(results)}", file=sys.stderr)

    results.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"[info] wrote {OUTPUT_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()
