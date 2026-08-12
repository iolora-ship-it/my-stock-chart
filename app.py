# -*- coding: utf-8 -*-
"""
自分専用 日本株チャートビューア
============================
使い方:
    streamlit run app.py

必要なライブラリ (初回のみ):
    pip install streamlit yfinance plotly pandas

機能:
  - stocks.csv に登録した銘柄をセクターごとに一覧・比較
  - 「前日比」ではなく、任意の期間を指定してトータルの騰落率(%)を表示
  - ローソク足チャートにカーソルを合わせる/クリックすると、その時点のOHLC株価を表示
  - 決算発表日をチャート上に縦線でマーキング
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="My Stock Chart", layout="wide")

STOCKS_CSV = "stocks.csv"

PERIOD_PRESETS = {
    "5日": 5,
    "10日": 10,
    "20日": 20,
    "60日": 60,
    "半年": 182,
    "1年": 365,
    "カスタム": None,
}


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def load_master(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    return df


@st.cache_data(ttl=600, show_spinner=False)
def fetch_history(code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """日本株のコードから .T ティッカーで日足OHLCを取得"""
    ticker = f"{code}.T"
    # 期間の前後に少し余裕を持たせて取得(休場日対策)
    df = yf.download(
        ticker,
        start=start - dt.timedelta(days=10),
        end=end + dt.timedelta(days=1),
        progress=False,
        auto_adjust=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(how="all")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_earnings_dates(code: str) -> list:
    """決算発表日の一覧を取得(取得できない銘柄もあるためtry/exceptで保護)"""
    ticker = f"{code}.T"
    try:
        t = yf.Ticker(ticker)
        edf = t.get_earnings_dates(limit=12)
        if edf is None or edf.empty:
            return []
        return [d.date() for d in edf.index.to_pydatetime()]
    except Exception:
        return []


def pct_change_over_period(df: pd.DataFrame, start: dt.date, end: dt.date):
    """指定期間の始値近辺の終値 → 直近終値のトータル騰落率(%)"""
    if df.empty:
        return None, None, None
    window = df[(df.index.date >= start) & (df.index.date <= end)]
    if window.empty:
        window = df
    base_price = float(window["Close"].iloc[0])
    latest_price = float(window["Close"].iloc[-1])
    if base_price == 0:
        return None, None, None
    pct = (latest_price - base_price) / base_price * 100
    return pct, base_price, latest_price


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------
st.sidebar.title("設定")

master = load_master(STOCKS_CSV)
sectors = ["すべて"] + sorted(master["sector"].unique().tolist())
selected_sector = st.sidebar.selectbox("セクター", sectors)

if selected_sector == "すべて":
    sector_df = master
else:
    sector_df = master[master["sector"] == selected_sector]

sector_df = sector_df.copy()
sector_df["label"] = sector_df["code"] + " " + sector_df["name"]

default_codes = sector_df["code"].tolist()
selected_labels = st.sidebar.multiselect(
    "表示銘柄(セクター内)",
    options=sector_df["label"].tolist(),
    default=sector_df["label"].tolist()[: min(8, len(sector_df))],
)
selected_codes = [lbl.split(" ")[0] for lbl in selected_labels]

st.sidebar.markdown("---")
st.sidebar.subheader("騰落率の期間")
period_choice = st.sidebar.radio("期間プリセット", list(PERIOD_PRESETS.keys()), index=2)

today = dt.date.today()
if period_choice == "カスタム":
    date_range = st.sidebar.date_input(
        "開始日〜終了日",
        value=(today - dt.timedelta(days=30), today),
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = today - dt.timedelta(days=30), today
else:
    days = PERIOD_PRESETS[period_choice]
    start_date = today - dt.timedelta(days=days)
    end_date = today

st.sidebar.caption(f"期間: {start_date} 〜 {end_date}")
st.sidebar.markdown("---")
st.sidebar.caption(
    "銘柄を追加したい場合は stocks.csv に "
    "`code,name,sector` の形式で行を追加してください。"
)

st.title("📈 自分専用 日本株チャートビューア")

# ---------------------------------------------------------------------------
# セクター内 騰落率比較
# ---------------------------------------------------------------------------
st.header(f"セクター別 騰落率比較（{selected_sector}／{period_choice}）")

if not selected_codes:
    st.info("左のサイドバーで表示したい銘柄を選択してください。")
else:
    rows = []
    with st.spinner("株価データを取得中..."):
        for code in selected_codes:
            name = master.loc[master["code"] == code, "name"].values[0]
            hist = fetch_history(code, start_date, end_date)
            pct, base_p, latest_p = pct_change_over_period(hist, start_date, end_date)
            rows.append(
                {
                    "コード": code,
                    "銘柄名": name,
                    "騰落率(%)": round(pct, 2) if pct is not None else None,
                    "起点株価": round(base_p, 1) if base_p is not None else None,
                    "直近株価": round(latest_p, 1) if latest_p is not None else None,
                }
            )

    result_df = pd.DataFrame(rows).sort_values("騰落率(%)", ascending=False)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.dataframe(
            result_df.style.format({"騰落率(%)": "{:+.2f}%"}, na_rep="—").applymap(
                lambda v: "color: red" if isinstance(v, (int, float)) and v > 0
                else ("color: blue" if isinstance(v, (int, float)) and v < 0 else ""),
                subset=["騰落率(%)"],
            ),
            use_container_width=True,
            hide_index=True,
        )
    with col2:
        bar = go.Figure(
            go.Bar(
                x=result_df["騰落率(%)"],
                y=result_df["銘柄名"],
                orientation="h",
                marker_color=[
                    "crimson" if v and v > 0 else "royalblue"
                    for v in result_df["騰落率(%)"]
                ],
            )
        )
        bar.update_layout(
            height=max(300, 30 * len(result_df)),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="騰落率(%)",
        )
        st.plotly_chart(bar, use_container_width=True)

    st.markdown("---")

    # -----------------------------------------------------------------
    # 個別銘柄のローソク足チャート
    # -----------------------------------------------------------------
    st.header("個別銘柄チャート")

    focus_label = st.selectbox(
        "詳細を見る銘柄",
        options=selected_labels,
    )
    focus_code = focus_label.split(" ")[0]
    focus_name = master.loc[master["code"] == focus_code, "name"].values[0]

    chart_days = st.slider("チャート表示期間(日数)", 30, 730, 180, step=10)
    chart_start = today - dt.timedelta(days=chart_days)
    hist = fetch_history(focus_code, chart_start, today)

    if hist.empty:
        st.warning("株価データを取得できませんでした。ティッカーコードや通信環境をご確認ください。")
    else:
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=hist.index,
                    open=hist["Open"],
                    high=hist["High"],
                    low=hist["Low"],
                    close=hist["Close"],
                    increasing_line_color="crimson",
                    decreasing_line_color="royalblue",
                    name=focus_name,
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>"
                        "始値: %{open:.1f}<br>"
                        "高値: %{high:.1f}<br>"
                        "安値: %{low:.1f}<br>"
                        "終値: %{close:.1f}<extra></extra>"
                    ),
                )
            ]
        )

        # 決算発表日を縦線でマーキング
        earnings_dates = fetch_earnings_dates(focus_code)
        visible_earnings = [
            d for d in earnings_dates if chart_start <= d <= today + dt.timedelta(days=365)
        ]
        for d in visible_earnings:
            fig.add_vline(
                x=pd.Timestamp(d),
                line_width=1,
                line_dash="dash",
                line_color="orange",
            )
        if visible_earnings:
            fig.add_annotation(
                x=pd.Timestamp(visible_earnings[0]),
                y=1,
                yref="paper",
                text="決算",
                showarrow=False,
                font=dict(color="orange", size=11),
                yshift=10,
            )

        fig.update_layout(
            title=f"{focus_code} {focus_name}",
            xaxis_title="日付",
            yaxis_title="株価(円)",
            xaxis_rangeslider_visible=False,
            hovermode="x unified",
            height=600,
        )
        st.plotly_chart(fig, use_container_width=True, theme=None)

        pct, base_p, latest_p = pct_change_over_period(hist, chart_start, today)
        if pct is not None:
            st.metric(
                label=f"{focus_name} 表示期間トータル騰落率",
                value=f"{latest_p:.1f} 円",
                delta=f"{pct:+.2f}% (期間開始: {base_p:.1f}円)",
            )

        if visible_earnings:
            st.caption(
                "次回/直近の決算発表日: "
                + "、".join(str(d) for d in visible_earnings[:4])
            )
        else:
            st.caption("この銘柄の決算発表日情報は取得できませんでした。")
