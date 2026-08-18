# -*- coding: utf-8 -*-
"""
自分専用 日本株チャートビューア
============================
使い方:
    streamlit run app.py

必要なライブラリ (初回のみ):
    pip install -r requirements.txt

機能:
  - トップに日経平均・SOX指数・ドル円・米国10年金利、CPI(日米)を表示（用語説明つき、選択期間に連動）
  - stocks.csv に登録した銘柄をセクターごとに一覧・比較
  - サイドバー上部で銘柄名・証券コードを検索し、その場で一覧に追加できる
  - お気に入りグループを作成して、好きな銘柄をまとめて管理できる
  - 1日〜1年まで細かく期間を指定してトータルの騰落率(%)を表示
  - 大きなローソク足チャート。クリックした地点の株価をピン留め表示
  - 決算発表日をカード・カレンダー表の両方で確認できる
"""

import datetime as dt
import json
import os
import re

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="My Stock Chart", layout="wide", page_icon="📈")

STOCKS_CSV = "stocks.csv"
GROUPS_FILE = "groups.json"

PERIOD_PRESETS = {
    "1日": 1,
    "3日": 3,
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

MARKET_INDICATORS = [
    {
        "ticker": "^N225",
        "label": "日経平均株価",
        "unit": "円",
        "decimals": 0,
        "help": "東証プライム市場の代表225銘柄の平均株価。日本株市場全体の値動きを示す最も基本的な指標です。",
    },
    {
        "ticker": "^SOX",
        "label": "SOX指数",
        "unit": "pt",
        "decimals": 1,
        "help": "フィラデルフィア半導体指数。米国の主要な半導体関連企業で構成され、半導体セクター全体の勢いを示します。前日の米国市場の値が表示されます。",
    },
    {
        "ticker": "JPY=X",
        "label": "ドル円",
        "unit": "円",
        "decimals": 2,
        "help": "1米ドル＝何円かを示す為替レート。円安（数値が大きくなる）は輸出企業に有利、円高は輸入企業に有利とされます。",
    },
    {
        "ticker": "^TNX",
        "label": "米国10年金利",
        "unit": "%",
        "decimals": 2,
        "help": "米国財務省が発行する10年物国債の利回り。世界の金利・株式市場に影響する重要指標です。上昇は株価にマイナスに働きやすいとされます。",
    },
]

CPI_SERIES = [
    {
        "series_id": "CPIAUCSL",
        "label": "米国CPI（前年比）",
        "help": "米国消費者物価指数の前年同月比。インフレの強さを示し、FRB（米国の中央銀行）の利上げ・利下げ判断材料になります。",
    },
    {
        "series_id": "JPNCPIALLMINMEI",
        "label": "日本CPI（前年比）",
        "help": "日本の消費者物価指数の前年同月比。日銀の金融政策判断や、生活実感としての物価上昇率の目安になります。",
    },
]

# セクターが多いので、業種グループにまとめてサイドバーを見やすくする
SECTOR_GROUPS = {
    "素材・化学": ["化学", "医薬品", "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "パルプ・紙", "繊維製品"],
    "機械・電機": ["機械", "電気機器", "半導体", "精密機器"],
    "自動車・輸送機器": ["輸送用機器"],
    "食品・消費財": ["食料品", "日用品・化粧品", "その他製品", "ゲーム"],
    "建設・不動産": ["建設業", "不動産業"],
    "電力・ガス": ["電気・ガス業"],
    "運輸": ["陸運業", "海運業", "空運業", "倉庫・運輸関連業"],
    "商社・小売": ["卸売業", "アパレル小売", "スーパー・総合小売", "家具・生活雑貨", "リユース・中古", "百貨店", "外食", "ドラッグストア"],
    "金融": ["銀行業", "証券・商品先物取引業", "保険業", "その他金融業"],
    "情報・サービス": ["情報・通信業", "ITサービス", "広告", "ネットサービス", "人材サービス", "レジャー・エンタメ"],
}


# ---------------------------------------------------------------------------
# 見た目（配色・レイアウト・フォント・アニメーション）
# ---------------------------------------------------------------------------
def inject_style():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700;900&display=swap');

        html, body, [class*="css"] {
            font-family: 'Noto Sans JP', sans-serif;
        }

        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* 背景 */
        [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #f7f8fc 0%, #eef1f8 100%);
        }

        /* サイドバー */
        section[data-testid="stSidebar"] {
            background-color: #12131a;
        }
        section[data-testid="stSidebar"] * {
            color: #e8e9ee !important;
        }
        section[data-testid="stSidebar"] .stRadio label,
        section[data-testid="stSidebar"] .stSelectbox label,
        section[data-testid="stSidebar"] .stMultiSelect label {
            font-weight: 700;
            font-size: 0.95rem;
        }
        /* 入力欄・プルダウンは白背景なので、文字は濃色にして見やすくする */
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea,
        section[data-testid="stSidebar"] [data-baseweb="select"] * {
            color: #1a1a1a !important;
        }
        section[data-testid="stSidebar"] input::placeholder {
            color: #8a8f98 !important;
        }
        /* 成功・エラーなどの通知ボックスも背景が明るいので文字を濃色に */
        section[data-testid="stSidebar"] [data-testid="stAlert"],
        section[data-testid="stSidebar"] [data-testid="stAlert"] * {
            color: #1a1a1a !important;
        }
        /* サイドバー内のボタンも背景が明るいので文字を濃色に */
        section[data-testid="stSidebar"] button,
        section[data-testid="stSidebar"] button * {
            color: #1a1a1a !important;
        }

        /* ヘッダー帯 */
        .app-header {
            background: linear-gradient(120deg, #14192b 0%, #262f4a 60%, #3a2b57 100%);
            border-radius: 18px;
            padding: 22px 28px;
            margin-bottom: 18px;
            animation: fadeInUp 0.5s ease;
            box-shadow: 0 8px 24px rgba(20, 25, 43, 0.25);
        }
        .app-header h1 {
            color: #ffffff !important;
            font-weight: 900 !important;
            font-size: 1.7rem;
            margin-bottom: 2px;
            letter-spacing: -0.02em;
        }
        .app-header p {
            color: #b9c0d6;
            margin: 0;
            font-size: 0.9rem;
        }

        /* 指標カード（市場ダッシュボード） */
        div[data-testid="stMetric"] {
            background: #ffffff;
            border-radius: 14px;
            padding: 14px 16px 10px 16px;
            box-shadow: 0 1px 4px rgba(20, 20, 30, 0.06);
            border: 1px solid #eef0f4;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
            animation: fadeInUp 0.5s ease;
        }
        div[data-testid="stMetric"]:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 20px rgba(20, 20, 30, 0.10);
        }
        div[data-testid="stMetricValue"] {
            font-weight: 800 !important;
            font-size: 1.55rem !important;
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: unset !important;
            line-height: 1.25 !important;
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
            transition: transform 0.12s ease, box-shadow 0.12s ease;
            animation: fadeInUp 0.4s ease;
        }
        .stock-card:hover {
            transform: translateY(-2px) scale(1.003);
            box-shadow: 0 8px 18px rgba(20, 20, 30, 0.08);
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
        .stock-card .yahoo-link {
            display: inline-block;
            margin-top: 6px;
            font-size: 0.78rem;
            font-weight: 600;
            color: #6b46c1;
            text-decoration: none;
            border-bottom: 1px solid transparent;
        }
        .stock-card .yahoo-link:hover {
            border-bottom: 1px solid #6b46c1;
        }

        /* クリックした株価のピン留め表示 */
        .pin-price {
            background: linear-gradient(120deg, #14192b, #262f4a);
            color: #ffffff;
            border-radius: 14px;
            padding: 14px 20px;
            margin-top: 10px;
            animation: fadeInUp 0.3s ease;
            font-size: 0.95rem;
        }
        .pin-price b { font-size: 1.1rem; }
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
def fetch_history(ticker_code: str, start: dt.date, end: dt.date, is_index: bool = False) -> pd.DataFrame:
    """コードから .T ティッカーで日足OHLCを取得（指数の場合はそのままのティッカーを使用）"""
    ticker = ticker_code if is_index else f"{ticker_code}.T"
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
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    else:
        df = df.dropna(how="all")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_company_name(code: str):
    """証券コードから会社名を取得（見つからない/データが無い場合は None）"""
    ticker = f"{code}.T"
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        name = info.get("longName") or info.get("shortName") or code
        return name
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def search_ticker_by_name(query: str) -> list:
    """Yahoo!ファイナンス(日本版)の銘柄検索ページを使い、会社名(日本語)などから候補を探す"""
    try:
        url = "https://finance.yahoo.co.jp/search/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, params={"query": query}, headers=headers, timeout=8)
        resp.raise_for_status()
        html = resp.text
        pattern = re.compile(
            r'href="https://finance\.yahoo\.co\.jp/quote/([0-9A-Za-z]+)\.T"'
            r'[\s\S]*?<h2 class="SearchItem__name[^"]*">([\s\S]*?)</h2>'
        )
        results = []
        seen = set()
        for m in pattern.finditer(html):
            code = m.group(1)
            name = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            name = re.sub(r"^[\(（]株[\)）]", "", name).strip()
            if code and code not in seen:
                seen.add(code)
                results.append((code, name or code))
        return results[:10]
    except Exception:
        return []


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_cpi_yoy(series_id: str):
    """FREDから物価指数を取得し、前年同月比(%)を計算する"""
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        df = pd.read_csv(url)
        date_col, value_col = df.columns[0], df.columns[1]
        df[date_col] = pd.to_datetime(df[date_col])
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
        df = df.dropna().sort_values(date_col)
        if df.empty:
            return None, None
        latest_date = df[date_col].iloc[-1]
        latest_val = df[value_col].iloc[-1]
        target = latest_date - pd.DateOffset(years=1)
        past = df[df[date_col] <= target]
        if past.empty:
            return None, None
        prev_val = past[value_col].iloc[-1]
        if prev_val == 0:
            return None, None
        yoy = (latest_val - prev_val) / prev_val * 100
        return yoy, latest_date.date()
    except Exception:
        return None, None


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


def next_earnings_label(earnings_dates: list, today: dt.date):
    """一覧表示用: 直近の決算発表日を「予定」「発表済」付きで返す
    （出来高の少ない銘柄ではYahoo!のデータが数年前で止まっていることがあるため、
    　あまりに古い過去日は「データなし」扱いにする）
    """
    if not earnings_dates:
        return None
    future = [d for d in earnings_dates if d >= today]
    if future:
        return f"{future[0]}（予定）"
    stale_cutoff = today - dt.timedelta(days=730)
    past = [d for d in earnings_dates if stale_cutoff <= d < today]
    if past:
        return f"{max(past)}（発表済）"
    return None


def pct_change_over_period(df: pd.DataFrame, start: dt.date, end: dt.date):
    """指定期間の始点の終値 → 直近終値のトータル騰落率(%)
    出来高が極端に少ない銘柄は該当期間の取引データが1件以下しかないことがあり、
    その場合は「変化なし(0.00%)」ではなく取得不可として扱う。
    """
    if df.empty:
        return None, None, None
    window = df[(df.index.date >= start) & (df.index.date <= end)]
    if len(window) < 2:
        window = df
    if len(window) < 2:
        return None, None, None
    base_price = float(window["Close"].iloc[0])
    latest_price = float(window["Close"].iloc[-1])
    if base_price == 0:
        return None, None, None
    pct = (latest_price - base_price) / base_price * 100
    return pct, base_price, latest_price


@st.cache_data(ttl=900, show_spinner=False)
def compute_sector_momentum(df: pd.DataFrame, start: dt.date, end: dt.date, sample_per_sector: int = 3) -> pd.DataFrame:
    """セクターごとに代表銘柄を数本サンプリングし、平均騰落率で「勢い」を算出する（まとめて一括取得して高速化）"""
    sample_rows = df.groupby("sector", group_keys=False).head(sample_per_sector)
    tickers = [f"{c}.T" for c in sample_rows["code"]]
    if not tickers:
        return pd.DataFrame(columns=["sector", "avg_pct", "sample_n"])
    try:
        raw = yf.download(
            tickers,
            start=start - dt.timedelta(days=10),
            end=end + dt.timedelta(days=1),
            progress=False,
            auto_adjust=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return pd.DataFrame(columns=["sector", "avg_pct", "sample_n"])

    sector_pcts = {}
    for _, r in sample_rows.iterrows():
        code = r["code"]
        sector = r["sector"]
        ticker = f"{code}.T"
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    continue
                sub = raw[ticker]
            else:
                sub = raw
            sub = sub.dropna(subset=["Close"]) if "Close" in sub.columns else sub.dropna(how="all")
            pct, _, _ = pct_change_over_period(sub, start, end)
            if pct is not None:
                sector_pcts.setdefault(sector, []).append(pct)
        except Exception:
            continue

    rows = [
        {"sector": s, "avg_pct": sum(v) / len(v), "sample_n": len(v)}
        for s, v in sector_pcts.items()
    ]
    return pd.DataFrame(rows)


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
# お気に入りグループの読み書き
# ---------------------------------------------------------------------------
def load_groups() -> dict:
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_groups(groups: dict):
    try:
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 画面構築
# ---------------------------------------------------------------------------
inject_style()

st.markdown(
    """
    <div class="app-header">
        <h1>📈 日本株チャートビューア</h1>
        <p>市場全体の空気を確認してから、セクター比較・個別チャートへ。数値にカーソルを合わせると（?）用語の説明が出ます。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# サイドバー（期間もここで確定させる。市場ダッシュボードがこの値を使うため先に処理する）
# ---------------------------------------------------------------------------
if "extra_stocks" not in st.session_state:
    st.session_state["extra_stocks"] = {}

st.sidebar.markdown("## ⚙️ 設定")

master_base = load_master(STOCKS_CSV)
if st.session_state["extra_stocks"]:
    extra_df = pd.DataFrame(
        [
            {"code": c, "name": n, "sector": "🔍 検索で追加した銘柄"}
            for c, n in st.session_state["extra_stocks"].items()
        ]
    )
    master = pd.concat([master_base, extra_df], ignore_index=True)
else:
    master = master_base

if "pinned_codes" not in st.session_state:
    st.session_state["pinned_codes"] = []

st.sidebar.markdown("### 🔍 銘柄を検索して追加")
search_query = st.sidebar.text_input(
    "銘柄名 または 証券コードで検索",
    key="stock_search_query",
    placeholder="例: トヨタ / 7203 / 285A",
)

if search_query.strip():
    q = search_query.strip()
    q_upper = q.upper()
    match_df = master[
        master["name"].str.contains(q, case=False, na=False, regex=False)
        | master["code"].str.upper().str.contains(q_upper, na=False, regex=False)
    ].head(8)

    if not match_df.empty:
        st.sidebar.caption(f"「{q}」に一致する銘柄（{len(match_df)}件）")
        for _, m in match_df.iterrows():
            already = m["code"] in st.session_state["pinned_codes"]
            c1, c2 = st.sidebar.columns([3, 1])
            c1.write(f"{m['code']} {m['name']}")
            if already:
                c2.markdown("✅")
            else:
                if c2.button("＋", key=f"pin_{m['code']}", help="この銘柄を今すぐ一覧に追加"):
                    st.session_state["pinned_codes"].append(m["code"])
                    st.rerun()
    else:
        with st.sidebar:
            with st.spinner("ネットで検索中…"):
                online_results = search_ticker_by_name(q)
        online_results = [
            (c, n) for c, n in online_results if c not in master["code"].values
        ]
        if online_results:
            st.sidebar.caption(f"ネット検索の候補（{len(online_results)}件）")
            for code, name in online_results:
                already = code in st.session_state["pinned_codes"]
                c1, c2 = st.sidebar.columns([3, 1])
                c1.write(f"{code} {name}")
                if already:
                    c2.markdown("✅")
                else:
                    if c2.button("＋", key=f"onlinepin_{code}", help="この銘柄を今すぐ一覧に追加"):
                        st.session_state["extra_stocks"][code] = name
                        st.session_state["pinned_codes"].append(code)
                        st.rerun()
        else:
            st.sidebar.caption("見つかりませんでした。証券コードが分かれば下で直接検索できます。")
            if st.sidebar.button(f"証券コード「{q_upper}」として検索する", key="new_code_search_btn"):
                name = fetch_company_name(q_upper)
                if name:
                    st.session_state["extra_stocks"][q_upper] = name
                    if q_upper not in st.session_state["pinned_codes"]:
                        st.session_state["pinned_codes"].append(q_upper)
                    st.sidebar.success(f"「{name}」({q_upper}) を追加しました。")
                    st.rerun()
                else:
                    st.sidebar.error("データが見つかりませんでした。会社名の表記や証券コードをご確認ください。")

if st.session_state["pinned_codes"]:
    st.sidebar.markdown("**📌 追加した銘柄（下の一覧に自動で表示されます）**")
    for c in list(st.session_state["pinned_codes"]):
        if c in master["code"].values:
            nm = master.loc[master["code"] == c, "name"].values[0]
        else:
            nm = st.session_state["extra_stocks"].get(c, c)
        c1, c2 = st.sidebar.columns([3, 1])
        c1.write(f"{c} {nm}")
        if c2.button("✕", key=f"unpin_{c}", help="表示から外す"):
            st.session_state["pinned_codes"].remove(c)
            st.rerun()

st.sidebar.markdown("---")

view_mode = st.sidebar.radio("銘柄の選び方", ["セクターから選ぶ", "⭐ グループから選ぶ"])

groups = load_groups()

if view_mode == "セクターから選ぶ":
    all_sectors_in_master = master["sector"].unique().tolist()
    group_names = ["すべて"] + list(SECTOR_GROUPS.keys())
    selected_group = st.sidebar.selectbox("業種グループ（絞り込み）", group_names)

    if selected_group == "すべて":
        sectors = ["すべて"] + sorted(all_sectors_in_master)
    else:
        group_sectors = set(SECTOR_GROUPS[selected_group]) & set(all_sectors_in_master)
        sectors = ["すべて"] + sorted(group_sectors)

    selected_sector = st.sidebar.selectbox("セクター", sectors, key=f"sector_select_{selected_group}")

    if selected_sector == "すべて":
        if selected_group == "すべて":
            sector_df = master
        else:
            sector_df = master[master["sector"].isin(sectors[1:])]
    else:
        sector_df = master[master["sector"] == selected_sector]

    sector_df = sector_df.copy()
    sector_df["label"] = sector_df["code"] + " " + sector_df["name"]

    default_n = min(6, len(sector_df))
    selected_labels = st.sidebar.multiselect(
        "銘柄",
        options=sector_df["label"].tolist(),
        default=sector_df["label"].tolist()[:default_n],
        key=f"stock_select_{selected_group}_{selected_sector}",
    )
    selected_codes = [lbl.split(" ")[0] for lbl in selected_labels]
else:
    selected_sector = "グループ"
    if not groups:
        st.sidebar.info("まだグループがありません。下の「⭐ グループを管理」から作成してください。")
        selected_codes, selected_labels = [], []
    else:
        group_name = st.sidebar.selectbox("グループ", list(groups.keys()))
        group_codes = groups.get(group_name, [])
        valid_codes = [c for c in group_codes if c in master["code"].values]
        selected_labels = [
            f"{c} {master.loc[master['code'] == c, 'name'].values[0]}" for c in valid_codes
        ]
        selected_codes = valid_codes
        missing = len(group_codes) - len(valid_codes)
        if missing > 0:
            st.sidebar.caption(f"⚠️ {missing}件、現在のリストで見つからない銘柄があります。")

# 検索して追加した銘柄（📌）は、セクター/グループの選択にかかわらず必ず一覧に表示する
for c in st.session_state["pinned_codes"]:
    if c not in selected_codes:
        if c in master["code"].values:
            nm = master.loc[master["code"] == c, "name"].values[0]
        else:
            nm = st.session_state["extra_stocks"].get(c, c)
        selected_codes.append(c)
        selected_labels.append(f"{c} {nm}")

period_choice = st.sidebar.radio(
    "期間",
    list(PERIOD_PRESETS.keys()),
    index=3,
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

st.sidebar.markdown("---")
with st.sidebar.expander("⭐ グループを管理"):
    new_group_name = st.text_input("新しいグループ名", key="new_group_name")
    if st.button("グループを作成", key="create_group_btn"):
        if new_group_name.strip():
            groups.setdefault(new_group_name.strip(), [])
            save_groups(groups)
            st.success(f"グループ「{new_group_name.strip()}」を作成しました。")
            st.rerun()

    if groups:
        target_group = st.selectbox("追加先グループ", list(groups.keys()), key="add_target_group")
        all_labels = (master["code"] + " " + master["name"]).tolist()
        add_labels = st.multiselect("グループに追加する銘柄", options=all_labels, key="add_labels_ms")
        if st.button("追加する", key="add_to_group_btn") and add_labels:
            codes_to_add = [lbl.split(" ")[0] for lbl in add_labels]
            groups[target_group] = sorted(set(groups[target_group] + codes_to_add))
            save_groups(groups)
            st.success("グループに追加しました。")
            st.rerun()

        remove_target = st.selectbox("グループから削除する銘柄", ["選択なし"] + groups.get(target_group, []), key="remove_from_group")
        if remove_target != "選択なし" and st.button("この銘柄を削除", key="remove_stock_btn"):
            groups[target_group] = [c for c in groups[target_group] if c != remove_target]
            save_groups(groups)
            st.success("削除しました。")
            st.rerun()

        st.markdown("---")
        delete_group = st.selectbox("グループ自体を削除", ["選択なし"] + list(groups.keys()), key="delete_group_select")
        if delete_group != "選択なし" and st.button("このグループを削除する", key="delete_group_btn"):
            del groups[delete_group]
            save_groups(groups)
            st.success("グループを削除しました。")
            st.rerun()
    st.caption("⚠️ グループ情報はアプリのサーバーに保存されます。今後アプリの機能追加・修正で更新すると、リセットされる場合があります。")

# ---------------------------------------------------------------------------
# 市場ダッシュボード（選択中の期間に連動）
# ---------------------------------------------------------------------------
idx_cols = st.columns(len(MARKET_INDICATORS))
for col, ind in zip(idx_cols, MARKET_INDICATORS):
    idx_hist = fetch_history(ind["ticker"], start_date, end_date, is_index=True)
    pct, base_p, latest_p = pct_change_over_period(idx_hist, start_date, end_date)
    with col:
        if latest_p is not None:
            st.metric(
                ind["label"],
                f"{latest_p:,.{ind['decimals']}f}{ind['unit']}",
                delta=f"{pct:+.2f}%" if pct is not None else None,
                help=ind["help"],
            )
        else:
            st.metric(ind["label"], "取得できません", help=ind["help"])
st.caption(f"📅 上の指標は選択中の期間「{period_choice}」でのトータル変化率です。")

st.markdown("")

# ---------------------------------------------------------------------------
# セクターの勢い（上位・下位）— トップページのハイライト
# ---------------------------------------------------------------------------
st.markdown("### 🔥 セクターの勢い")
st.caption(f"各セクターの代表銘柄をもとにした、期間「{period_choice}」の平均騰落率ランキングです。")

with st.spinner("セクターの勢いを計算中…"):
    sector_momentum = compute_sector_momentum(master_base, start_date, end_date)

if sector_momentum.empty:
    st.caption("セクターの勢いを取得できませんでした。")
else:
    sector_momentum = sector_momentum.sort_values("avg_pct", ascending=False).reset_index(drop=True)
    top_n = min(5, len(sector_momentum) // 2) if len(sector_momentum) >= 2 else len(sector_momentum)
    top_n = max(top_n, 1)
    gainers = sector_momentum.head(top_n)
    losers = sector_momentum.tail(top_n).sort_values("avg_pct")

    def _sector_row_html(r):
        color = pct_color(r["avg_pct"])
        arrow = pct_arrow(r["avg_pct"])
        return f"""
        <div class="stock-card" style="padding:10px 18px; margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span class="name" style="font-size:0.95rem;">{r['sector']}</span>
                <span class="pct" style="font-size:1.1rem; color:{color};">{arrow} {r['avg_pct']:+.2f}%</span>
            </div>
        </div>
        """

    col_g, col_l = st.columns(2)
    with col_g:
        st.markdown("**📈 勢いのあるセクター（上昇幅が大きい）**")
        for _, r in gainers.iterrows():
            st.markdown(_sector_row_html(r), unsafe_allow_html=True)
    with col_l:
        st.markdown("**📉 勢いのないセクター（下落幅が大きい）**")
        for _, r in losers.iterrows():
            st.markdown(_sector_row_html(r), unsafe_allow_html=True)
    st.caption("👈 気になるセクターがあれば、左のサイドバーの「業種グループ」→「セクター」で絞り込んで見てみましょう。")

st.markdown("")

with st.expander("📎 物価指数（CPI）を見る"):
    cpi_cols = st.columns(len(CPI_SERIES))
    for col, series in zip(cpi_cols, CPI_SERIES):
        yoy, asof = fetch_cpi_yoy(series["series_id"])
        with col:
            if yoy is not None:
                st.metric(
                    series["label"],
                    f"{yoy:+.1f}%",
                    help=series["help"] + f"（基準月: {asof}）",
                )
            else:
                st.metric(series["label"], "取得できません", help=series["help"])
    st.caption("データ提供: FRED（セントルイス連銀）。月次更新のため、日々の値動きとはタイミングが異なります。")

st.markdown("")

if not selected_codes:
    st.info("👈 左のサイドバーで、見たいセクター（またはグループ）と銘柄を選んでください。")
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
                "code": code,
                "name": name,
                "pct": round(pct, 2) if pct is not None else None,
                "base_p": round(base_p, 1) if base_p is not None else None,
                "latest_p": round(latest_p, 1) if latest_p is not None else None,
                "earnings": next_earnings_label(earnings_dates, today),
                "earnings_raw": earnings_dates,
            }
        )

result_df = pd.DataFrame(rows).sort_values("pct", ascending=False, na_position="last")

tab1, tab2, tab3 = st.tabs(["📊 セクター比較", "🕯️ 個別チャート", "🗓️ 決算カレンダー"])

# --- タブ1: セクター比較 ---------------------------------------------------
with tab1:
    st.caption(f"「{selected_sector}」・直近{period_choice}のトータル騰落率")

    for _, r in result_df.iterrows():
        color = pct_color(r["pct"])
        arrow = pct_arrow(r["pct"])
        pct_text = f"{r['pct']:+.2f}%" if pd.notna(r["pct"]) else "取得できません"
        price_text = (
            f"{r['base_p']:.1f}円 → {r['latest_p']:.1f}円" if pd.notna(r["base_p"]) else ""
        )
        # 全銘柄で決算日が取得できなかった場合、DataFrame化の際にこの列がfloat型のNaNに
        # 変換されてしまうことがある（NaNはPythonのbool判定でTrueになるため、
        # 素朴な if r["earnings"] だと「nan」という文字列がそのまま表示されてしまう）
        badge_class = "badge" if pd.notna(r["earnings"]) else "badge badge-none"
        badge_text = r["earnings"] if pd.notna(r["earnings"]) else "決算情報なし"
        yahoo_url = f"https://finance.yahoo.co.jp/quote/{r['code']}.T"

        st.markdown(
            f"""
            <div class="stock-card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span class="name">{r['name']}</span>
                        <span class="code">{r['code']}</span>
                        <div class="price">{price_text}</div>
                        <a class="yahoo-link" href="{yahoo_url}" target="_blank" rel="noopener noreferrer">Yahoo!ファイナンスで見る →</a>
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

# --- タブ2: 個別チャート ---------------------------------------------------
with tab2:
    focus_label = st.selectbox("銘柄を選択", options=selected_labels)
    focus_code = focus_label.split(" ")[0]
    focus_name = master.loc[master["code"] == focus_code, "name"].values[0]
    st.caption(f"🔗 詳しく調べる → [Yahoo!ファイナンスで{focus_name}を見る](https://finance.yahoo.co.jp/quote/{focus_code}.T)")

    chart_days = st.select_slider(
        "表示期間",
        options=[10, 30, 60, 90, 180, 365, 730],
        value=180,
        format_func=lambda d: f"{d}日",
    )
    chart_start = today - dt.timedelta(days=chart_days)
    hist_full = fetch_history(focus_code, chart_start, today)
    if not hist_full.empty:
        hist = hist_full[(hist_full.index.date >= chart_start) & (hist_full.index.date <= today)]
        if hist.empty:
            hist = hist_full
    else:
        hist = hist_full

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
            height=680,
            margin=dict(t=50, b=10, l=10, r=10),
            plot_bgcolor="white",
            dragmode="zoom",
        )
        # 土日は取引がなく空白になるため、詰めて表示する（短い期間ほど間延びして見づらくなるのを防ぐ）
        fig.update_xaxes(
            rangebreaks=[dict(bounds=["sat", "mon"])],
            rangeslider_visible=False,
        )

        # クリックした地点の株価をピン留め表示（対応バージョンのStreamlitのみ）
        event = None
        try:
            event = st.plotly_chart(
                fig,
                use_container_width=True,
                on_select="rerun",
                selection_mode=("points",),
                key=f"candle_{focus_code}_{chart_days}",
            )
        except TypeError:
            # 古いバージョンのStreamlitでは on_select 未対応 → 通常表示にフォールバック
            st.plotly_chart(fig, use_container_width=True)

        clicked_row = None
        clicked_date = None
        if event is not None:
            try:
                sel = getattr(event, "selection", None)
                if sel is None and isinstance(event, dict):
                    sel = event.get("selection")
                points = None
                if sel is not None:
                    points = sel.get("points") if isinstance(sel, dict) else getattr(sel, "points", None)
                if points:
                    p0 = points[0]
                    idx = p0.get("point_index") if isinstance(p0, dict) else getattr(p0, "point_index", None)
                    if idx is not None and 0 <= idx < len(hist):
                        clicked_row = hist.iloc[idx]
                        clicked_date = hist.index[idx].date()
            except Exception:
                clicked_row = None

        if clicked_row is not None:
            st.markdown(
                f"""
                <div class="pin-price">
                    📍 <b>{clicked_date}</b> の株価
                    始値 {clicked_row['Open']:.1f}円 ／
                    高値 {clicked_row['High']:.1f}円 ／
                    安値 {clicked_row['Low']:.1f}円 ／
                    終値 <b>{clicked_row['Close']:.1f}円</b>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.caption("チャート上の見たい地点をクリックすると、その日の株価がピン留め表示されます。マウスを合わせるだけでも数値が出ます。")

        pct, base_p, latest_p = pct_change_over_period(hist, chart_start, today)
        m1, m2 = st.columns(2)
        with m1:
            if pct is not None:
                st.metric(label="表示期間トータル騰落率", value=f"{latest_p:.1f} 円", delta=f"{pct:+.2f}%")
        with m2:
            if visible_earnings:
                st.metric(label="直近の決算発表日", value=str(visible_earnings[0]))
            else:
                st.metric(label="決算発表日", value="情報なし")

# --- タブ3: 決算カレンダー -------------------------------------------------
with tab3:
    st.caption("選択中の銘柄の決算発表日を一覧で確認できます。")
    cal_rows = []
    for _, r in result_df.iterrows():
        for d in r["earnings_raw"]:
            cal_rows.append(
                {
                    "日付": d,
                    "状態": "予定" if d >= today else "発表済",
                    "コード": r["code"],
                    "銘柄名": r["name"],
                }
            )
    if not cal_rows:
        st.info("選択中の銘柄について、決算発表日のデータが取得できませんでした。")
    else:
        cal_df = pd.DataFrame(cal_rows).sort_values("日付")
        upcoming = cal_df[cal_df["状態"] == "予定"]
        past = cal_df[cal_df["状態"] == "発表済"].sort_values("日付", ascending=False)

        st.subheader("📌 今後の決算発表予定")
        if upcoming.empty:
            st.caption("今後の決算発表予定は取得できませんでした。")
        else:
            st.dataframe(upcoming, use_container_width=True, hide_index=True)

        st.subheader("📖 発表済みの決算")
        if past.empty:
            st.caption("発表済みの決算データはありません。")
        else:
            st.dataframe(past, use_container_width=True, hide_index=True)

