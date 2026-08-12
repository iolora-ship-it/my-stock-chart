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
  - ローソク足チャートにカーソルを合わせると、その時点のOHLC株価を表示
  - 決算発表日をチャート上・一覧表の両方で確認できる
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="My Stock Chart", layout="wide", page_icon="📈")

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

UP_COLOR = "#e5484d"
DOWN_COLOR = "#0d6efd"
NEUTRAL_COLOR = "#8a8f98"


# ---------------------------------------------------------------------------
# 見た目（配色・レイアウト・フォント）
# ---------------------------------------------------------------------------
def inject_style():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap');

        html, body, [class*="css"] {
            font-family: 'Noto Sans JP', sans-serif;
        }

        /* サイドバー */
        section[data-testid="stSidebar"] {
            background-color: #f7f8fa;
            border-right: 1px solid #e7e9ec;
        }
        section[data-testid="stSidebar"] .stRadio label,
        section[data-testid="stSidebar"] .stSelectbox label,
        section[data-testid="stSidebar"] .stMultiSelect label {
            font-weight: 700;
            font-size: 0.95rem;
        }

        /* メインタイトル */
        h1 {
            font-weight: 900 !important;
            letter-spacing: -0.02em;
        }

        /* タブ */
        button[data-baseweb="tab"] {
            font-size: 1.05rem;
            font-weight: 700;
            padding-top: 10px;
            padding-bottom: 10px;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            color: #e5484d;
        }
        div[data-baseweb="tab-highlight"] {
            background-color: #e5484d;
            height: 3px;
        }

        /* 銘柄カード */
        .stock-card {
            border-radius: 14px;
            border: 1px solid #eceef1;
            background: #ffffff;
            padding: 16px 20px;
            margin-bottom: 10px;
            box-shadow: 0 1px 3px rgba(20, 20, 30, 0.04);
        }
        .stock-card .name {
            font-size: 1.05rem;
            font-weight: 700;
            color: #1a1a1a;
        }
        .stock-card .code {
            font-size: 0.8rem;
            color: #9aa0a6;
            margin-left: 6px;
        }
        .stock-card .pct {
            font-size: 1.6rem;
            font-weight: 900;
        }
        .stock-card .price {
            font-size: 0.85rem;
            color: #6b7280;
        }
        .stock-card .badge {
            display: inline-block;
            font-size: 0.75rem;
            font-weight: 600;
            padding: 3px 10px;
            border-radius: 999px;
            background: #fff4e5;
            color: #b06a00;
        }
        .stock-card .badge-none {
            background: #f1f2f4;
            color: #9aa0a6;
        }

        div[data-testid="stMetricValue"] {
            font-weight: 900;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    try:
        df = yf.download(
            ticker,
            start=start - dt.timedelta(days=10),
            end=end + dt.timedelta(days=1),
            progress=False,
            auto_adjust=False,
        )
    except Exception:
        return pd.DataFrame()
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
        return sorted(d.date() for d in edf.index.to_pydatetime())
    except Exception:
        return []


def next_earnings_label(earnings_dates: list, today: dt.date) -> str:
    """一覧表示用: 直近の決算発表日を「予定」「発表済」付きで返す"""
    if not earnings_dates:
        return None
    future = [d for d in earnings_dates if d >= today]
    if future:
        return f"{future[0]}（予定）"
    past = [d for d in earnings_dates if d < today]
    if past:
        return f"{max(past)}（発表済）"
    return None


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


def pct_color(v):
    if v is None:
        return NEUTRAL_COLOR
    if v > 0:
        return UP_COLOR
    if v < 0:
        return DOWN_COLOR
    return NEUTRAL_COLOR


def pct_arrow(v):
    if v is None:
        return ""
    if v > 0:
        return "▲"
    if v < 0:
        return "▼"
    return "―"


# ---------------------------------------------------------------------------
# サイドバー（要素を絞ってシンプルに）
# ---------------------------------------------------------------------------
inject_style()

st.sidebar.markdown("## ⚙️ 設定")

master = load_master(STOCKS_CSV)
sectors = ["すべて"] + sorted(master["sector"].unique().tolist())
selected_sector = st.sidebar.selectbox("セクター", sectors)

if selected_sector == "すべて":
    sector_df = master
else:
    sector_df = master[master["sector"] == selected_sector]

sector_df = sector_df.copy()
sector_df["label"] = sector_df["code"] + " " + sector_df["name"]

# セクター切り替え時に選択銘柄を自動でリセットする（key にセクター名を含める）
default_n = min(6, len(sector_df))
selected_labels = st.sidebar.multiselect(
    "銘柄",
    options=sector_df["label"].tolist(),
    default=sector_df["label"].tolist()[:default_n],
    key=f"stock_select_{selected_sector}",
)
selected_codes = [lbl.split(" ")[0] for lbl in selected_labels]

period_choice = st.sidebar.radio(
    "期間",
    list(PERIOD_PRESETS.keys()),
    index=2,
    horizontal=True,
)

today = dt.date.today()
if period_choice == "カスタム":
    with st.sidebar.expander("開始日〜終了日を指定", expanded=True):
        date_range = st.date_input(
            "期間",
            value=(today - dt.timedelta(days=30), today),
            label_visibility="collapsed",
        )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = today - dt.timedelta(days=30), today
else:
    days = PERIOD_PRESETS[period_choice]
    start_date = today - dt.timedelta(days=days)
    end_date = today

with st.sidebar.expander("銘柄を追加したい場合"):
    st.caption(
        "`stocks.csv` に `code,name,sector` の形式で行を追加してください。"
    )

# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
st.title("📈 日本株チャートビューア")

if not selected_codes:
    st.info("👈 左のサイドバーで、見たいセクターと銘柄を選んでください。")
    st.stop()

rows = []
with st.spinner("株価データを取得中です…"):
    for code in selected_codes:
        name = master.loc[master["code"] == code, "name"].values[0]
        hist = fetch_history(code, start_date, end_date)
        pct, base_p, latest_p = pct_change_over_period(hist, start_date, end_date)
        earnings_dates = fetch_earnings_dates(code)
        rows.append(
            {
                "code": code,
                "name": name,
                "pct": round(pct, 2) if pct is not None else None,
                "base_p": round(base_p, 1) if base_p is not None else None,
                "latest_p": round(latest_p, 1) if latest_p is not None else None,
                "earnings": next_earnings_label(earnings_dates, today),
            }
        )

result_df = pd.DataFrame(rows).sort_values("pct", ascending=False, na_position="last")

tab1, tab2 = st.tabs(["📊 セクター比較", "🕯️ 個別チャート"])

with tab1:
    st.caption(f"「{selected_sector}」・直近{period_choice}のトータル騰落率")

    for _, r in result_df.iterrows():
        color = pct_color(r["pct"])
        arrow = pct_arrow(r["pct"])
        pct_text = f"{r['pct']:+.2f}%" if r["pct"] is not None else "取得できません"
        price_text = (
            f"{r['base_p']:.1f}円 → {r['latest_p']:.1f}円"
            if r["base_p"] is not None
            else ""
        )
        badge_class = "badge" if r["earnings"] else "badge badge-none"
        badge_text = r["earnings"] if r["earnings"] else "決算情報なし"

        st.markdown(
            f"""
            <div class="stock-card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span class="name">{r['name']}</span>
                        <span class="code">{r['code']}</span>
                        <div class="price">{price_text}</div>
                    </div>
                    <div style="text-align:right;">
                        <div class="pct" style="color:{color};">{arrow} {pct_text}</div>
                        <span class="{badge_class}">🗓 {badge_text}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

with tab2:
    focus_label = st.selectbox("銘柄を選択", options=selected_labels)
    focus_code = focus_label.split(" ")[0]
    focus_name = master.loc[master["code"] == focus_code, "name"].values[0]

    chart_days = st.select_slider(
        "表示期間",
        options=[30, 60, 90, 180, 365, 730],
        value=180,
        format_func=lambda d: f"{d}日",
    )
    chart_start = today - dt.timedelta(days=chart_days)
    hist = fetch_history(focus_code, chart_start, today)

    if hist.empty:
        st.warning("株価データを取得できませんでした。銘柄コードや通信環境をご確認ください。")
    else:
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=hist.index,
                    open=hist["Open"],
                    high=hist["High"],
                    low=hist["Low"],
                    close=hist["Close"],
                    increasing_line_color=UP_COLOR,
                    decreasing_line_color=DOWN_COLOR,
                    name=focus_name,
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>"
                        "始値: %{open:.1f}円<br>"
                        "高値: %{high:.1f}円<br>"
                        "安値: %{low:.1f}円<br>"
                        "終値: %{close:.1f}円<extra></extra>"
                    ),
                )
            ]
        )

        earnings_dates = fetch_earnings_dates(focus_code)
        visible_earnings = [
            d for d in earnings_dates if chart_start <= d <= today + dt.timedelta(days=365)
        ]
        for d in visible_earnings:
            fig.add_vline(x=pd.Timestamp(d), line_width=1, line_dash="dash", line_color="#f5a623")
        if visible_earnings:
            fig.add_annotation(
                x=pd.Timestamp(visible_earnings[0]),
                y=1,
                yref="paper",
                text="決算発表",
                showarrow=False,
                font=dict(color="#f5a623", size=11),
                yshift=10,
            )

        fig.update_layout(
            title=f"{focus_code} {focus_name}",
            xaxis_title=None,
            yaxis_title="株価(円)",
            xaxis_rangeslider_visible=False,
            hovermode="x unified",
            height=560,
            margin=dict(t=50, b=10, l=10, r=10),
            plot_bgcolor="white",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption("チャート上にマウスを合わせると、その日の始値・高値・安値・終値が表示されます。")

        pct, base_p, latest_p = pct_change_over_period(hist, chart_start, today)
        m1, m2 = st.columns(2)
        with m1:
            if pct is not None:
                st.metric(
                    label="表示期間トータル騰落率",
                    value=f"{latest_p:.1f} 円",
                    delta=f"{pct:+.2f}%",
                )
        with m2:
            if visible_earnings:
                st.metric(
                    label="直近の決算発表日",
                    value=str(visible_earnings[0]),
                )
            else:
                st.metric(label="決算発表日", value="情報なし")
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
  - ローソク足チャートにカーソルを合わせると、その時点のOHLC株価を表示
  - 決算発表日をチャート上・一覧表の両方で確認できる
"""

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="My Stock Chart", layout="wide", page_icon="📈")

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
    try:
        df = yf.download(
            ticker,
            start=start - dt.timedelta(days=10),
            end=end + dt.timedelta(days=1),
            progress=False,
            auto_adjust=False,
        )
    except Exception:
        return pd.DataFrame()
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
        return sorted(d.date() for d in edf.index.to_pydatetime())
    except Exception:
        return []


def next_earnings_label(earnings_dates: list, today: dt.date) -> str:
    """一覧表示用: 直近の決算発表日を「予定」「発表済」付きで返す"""
    if not earnings_dates:
        return "—"
    future = [d for d in earnings_dates if d >= today]
    if future:
        return f"{future[0]}（予定）"
    past = [d for d in earnings_dates if d < today]
    if past:
        return f"{max(past)}（発表済）"
    return "—"


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


def color_pct(val):
    if not isinstance(val, (int, float)) or pd.isna(val):
        return ""
    if val > 0:
        return "color: #d62728; font-weight: 600"
    if val < 0:
        return "color: #1f77b4; font-weight: 600"
    return ""


def style_pct_column(df: pd.DataFrame, column: str):
    """pandasのバージョン差異(applymap→map改名)を吸収して色付けする"""
    styler = df.style.format({column: "{:+.2f}%"}, na_rep="—")
    try:
        return styler.map(color_pct, subset=[column])
    except AttributeError:
        return styler.applymap(color_pct, subset=[column])


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ 設定")

master = load_master(STOCKS_CSV)
sectors = ["すべて"] + sorted(master["sector"].unique().tolist())
selected_sector = st.sidebar.selectbox("① セクターを選ぶ", sectors)

if selected_sector == "すべて":
    sector_df = master
else:
    sector_df = master[master["sector"] == selected_sector]

sector_df = sector_df.copy()
sector_df["label"] = sector_df["code"] + " " + sector_df["name"]

default_n = min(8, len(sector_df))
selected_labels = st.sidebar.multiselect(
    "② 比較したい銘柄を選ぶ",
    options=sector_df["label"].tolist(),
    default=sector_df["label"].tolist()[:default_n],
)
selected_codes = [lbl.split(" ")[0] for lbl in selected_labels]

st.sidebar.markdown("---")
st.sidebar.subheader("③ 騰落率を計算する期間")
period_choice = st.sidebar.radio(
    "前日比ではなく、この期間トータルでの騰落率を表示します",
    list(PERIOD_PRESETS.keys()),
    index=2,
)

today = dt.date.today()
if period_choice == "カスタム":
    date_range = st.sidebar.date_input(
        "開始日〜終了日を指定",
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

st.sidebar.caption(f"📅 集計期間: {start_date} 〜 {end_date}")
st.sidebar.markdown("---")
st.sidebar.caption(
    "銘柄を追加したい場合は `stocks.csv` に "
    "`code,name,sector` の形式で行を追加してください。"
)

st.title("📈 自分専用 日本株チャートビューア")
st.caption("前日比だけでなく、好きな期間のトータル騰落率・決算発表日をまとめて確認できます。")

if not selected_codes:
    st.info("👈 左のサイドバーで、見たいセクターと銘柄を選んでください。")
    st.stop()

# ---------------------------------------------------------------------------
# データ取得（タブ共通）
# ---------------------------------------------------------------------------
rows = []
with st.spinner("株価データを取得中です…"):
    for code in selected_codes:
        name = master.loc[master["code"] == code, "name"].values[0]
        hist = fetch_history(code, start_date, end_date)
        pct, base_p, latest_p = pct_change_over_period(hist, start_date, end_date)
        earnings_dates = fetch_earnings_dates(code)
        rows.append(
            {
                "コード": code,
                "銘柄名": name,
                "騰落率(%)": round(pct, 2) if pct is not None else None,
                "起点株価": round(base_p, 1) if base_p is not None else None,
                "直近株価": round(latest_p, 1) if latest_p is not None else None,
                "決算発表日": next_earnings_label(earnings_dates, today),
            }
        )

result_df = pd.DataFrame(rows).sort_values("騰落率(%)", ascending=False, na_position="last")

# ---------------------------------------------------------------------------
# タブでUIを整理
# ---------------------------------------------------------------------------
tab1, tab2 = st.tabs(["📊 セクター内の騰落率比較", "🕯️ 個別銘柄のローソク足チャート"])

with tab1:
    st.subheader(f"「{selected_sector}」セクターの騰落率比較（直近{period_choice}）")
    st.caption("株価は日本語の東証コード＋「.T」でYahoo!ファイナンスから取得しています。")

    col1, col2 = st.columns([1.1, 1])
    with col1:
        st.dataframe(
            style_pct_column(result_df, "騰落率(%)"),
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
                    "#d62728" if v is not None and v > 0 else "#1f77b4"
                    for v in result_df["騰落率(%)"]
                ],
            )
        )
        bar.update_layout(
            height=max(320, 32 * len(result_df)),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="騰落率(%)",
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(bar, use_container_width=True)

    st.caption("「決算発表日」列: 未来の日付は（予定）、過去の日付は（発表済）を表します。データが無い銘柄は「—」と表示されます。")

with tab2:
    st.subheader("個別銘柄のチャートを見る")

    focus_label = st.selectbox("銘柄を選択", options=selected_labels)
    focus_code = focus_label.split(" ")[0]
    focus_name = master.loc[master["code"] == focus_code, "name"].values[0]

    chart_days = st.slider("表示するチャートの期間（日数）", 30, 730, 180, step=10)
    chart_start = today - dt.timedelta(days=chart_days)
    hist = fetch_history(focus_code, chart_start, today)

    if hist.empty:
        st.warning("株価データを取得できませんでした。銘柄コードや通信環境をご確認ください。")
    else:
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=hist.index,
                    open=hist["Open"],
                    high=hist["High"],
                    low=hist["Low"],
                    close=hist["Close"],
                    increasing_line_color="#d62728",
                    decreasing_line_color="#1f77b4",
                    name=focus_name,
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>"
                        "始値: %{open:.1f}円<br>"
                        "高値: %{high:.1f}円<br>"
                        "安値: %{low:.1f}円<br>"
                        "終値: %{close:.1f}円<extra></extra>"
                    ),
                )
            ]
        )

        earnings_dates = fetch_earnings_dates(focus_code)
        visible_earnings = [
            d for d in earnings_dates if chart_start <= d <= today + dt.timedelta(days=365)
        ]
        for d in visible_earnings:
            fig.add_vline(x=pd.Timestamp(d), line_width=1, line_dash="dash", line_color="orange")
        if visible_earnings:
            fig.add_annotation(
                x=pd.Timestamp(visible_earnings[0]),
                y=1,
                yref="paper",
                text="決算発表",
                showarrow=False,
                font=dict(color="orange", size=11),
                yshift=10,
            )

        fig.update_layout(
            title=f"{focus_code} {focus_name}　― マウスを合わせるとその日の株価が表示されます",
            xaxis_title="日付",
            yaxis_title="株価(円)",
            xaxis_rangeslider_visible=False,
            hovermode="x unified",
            height=600,
        )
        st.plotly_chart(fig, use_container_width=True)

        pct, base_p, latest_p = pct_change_over_period(hist, chart_start, today)
        m1, m2 = st.columns(2)
        with m1:
            if pct is not None:
                st.metric(
                    label=f"{focus_name}　表示期間トータル騰落率",
                    value=f"{latest_p:.1f} 円",
                    delta=f"{pct:+.2f}%（期間開始時点: {base_p:.1f}円）",
                )
        with m2:
            if visible_earnings:
                st.markdown("**決算発表日（直近〜今後1年）**")
                st.write("、".join(str(d) for d in visible_earnings[:6]))
            else:
                st.markdown("**決算発表日**")
                st.caption("この銘柄の決算発表日情報は取得できませんでした。")
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
