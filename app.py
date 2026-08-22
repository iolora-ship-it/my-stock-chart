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
  - 大きなローソク足チャート（25日/75日移動平均線・出来高・RSIを併記）。クリックした地点の株価をピン留め表示
  - 決算発表日をカード・カレンダー表の両方で確認できる
  - PER・PBR・配当利回り・時価総額などのファンダメンタルズ指標を表示
  - 出来高から「薄商い」銘柄を検知し、データの信頼度が低い可能性を注意表示
  - 出来高急増・急な値動きを検知し、薄商いでの仕手株疑い（注意）と、出来高を伴う値動き（注目）を区別して表示
  - 個別チャートに52週高値・安値からの位置を表示
  - PER上限・配当利回り下限・薄商い除外・仕手株疑い除外・購入予算・だまし初動除外でのスクリーニング（絞り込み）
  - 5日/25日/75日移動平均線のパーフェクトオーダーと25日線乖離率から「トレンド確立度」を判定・表示
  - 陽線/陰線・出来高・終値位置・5日移動平均線から「初動の信頼度（だまし判定）」を判定・表示
  - 保有銘柄・株数・取得単価を登録できるポートフォリオ機能（評価額・含み損益を表示）
  - TOPIX・NYダウ・WTI原油やセクター別騰落ランキングをまとめて確認できる「市場概況」タブ
  - 専門用語（PER・PBR・RSIなど）にはカーソルを合わせると説明が出るツールチップ＋用語集
"""

import datetime as dt
import json
import os
import re
import time
import uuid

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="My Stock Chart", layout="wide", page_icon="📈")

STOCKS_CSV = "stocks.csv"
GROUPS_FILE = "groups.json"
PORTFOLIO_FILE = "portfolio.json"

# 1日あたりの平均売買代金がこれを下回る銘柄は「薄商い」として注意バッジを表示する
# （出来高が極端に少ない銘柄は、株価データが実勢を反映していない・更新が古いことがあるため）
LIQUIDITY_THIN_THRESHOLD = 30_000_000  # 3,000万円/日

# 「値動き急変（仕手株リスク）」バッジの判定基準
SPEC_VOLUME_SPIKE_RATIO = 3.0   # 直近の出来高が、それ以前の平均出来高の何倍以上で急増とみなすか
SPEC_DAY_PCT_THRESHOLD = 15.0   # 直近1営業日の値動きが何%以上で急変とみなすか
SPEC_WEEK_PCT_THRESHOLD = 30.0  # 直近5営業日の値動きが何%以上で急変とみなすか

# 「トレンド確立度」判定基準（MA5/MA25/MA75のパーフェクトオーダー＋25日線からの乖離率）
TREND_EXTENDED_DEV_THRESHOLD = 15.0  # 25日線からの乖離率がこれ以上だと「伸びすぎ」の注記を出す

# 「初動の信頼度（だまし判定）」の基準
ENTRY_VOLUME_RATIO_THRESHOLD = 1.2   # 出来高が直近5日平均の何倍以上あれば「出来高を伴う」とみなすか
ENTRY_CLOSE_POS_THRESHOLD = 0.6      # 終値がその日の値幅の上位何割以内にあれば「強い引け」とみなすか（0〜1）

# 100株単位で購入する場合の1単元あたり株数
SHARE_UNIT = 100

# 用語集（カード上のツールチップ・用語集エキスパンダーの両方で使う）
GLOSSARY = {
    "PER": "株価収益率（Price Earnings Ratio）。株価が1株当たり利益(EPS)の何倍かを示す指標です。数値が低いほど利益に対して株価が割安と判断されることが多いですが、業種によって適正水準は異なります。",
    "PBR": "株価純資産倍率（Price Book-value Ratio）。株価が1株当たり純資産(BPS)の何倍かを示す指標です。1倍が理論上の解散価値の目安とされ、1倍を下回ると割安と判断されることがあります。",
    "配当利回り": "1株当たり年間配当金を現在の株価で割った割合です。株価に対してどれだけ配当を受け取れるかの目安になりますが、業績悪化で減配・無配になるリスクもあります。",
    "時価総額": "株価 × 発行済株式数で計算される、企業の市場価値の大きさを示す指標です。数値が大きいほど大企業・値動きが相対的に安定しやすい傾向があります。",
    "出来高": "一定期間内に売買が成立した株数（または金額）です。出来高が少ない「薄商い」の銘柄は、株価データが実勢を反映しにくく、急な値動きが出やすい傾向があります。",
    "移動平均線": "一定期間（例: 25日・75日）の終値の平均値を結んだ線です。株価そのものより滑らかに動くため、上昇・下降トレンドの方向性を把握するのに使われます。",
    "RSI": "相対力指数（Relative Strength Index）。一定期間の値上がり幅と値下がり幅の比率から算出される、0〜100で表されるテクニカル指標です。一般に70以上で「買われすぎ」、30以下で「売られすぎ」の目安とされますが、あくまで参考値です。",
    "仕手株疑い": (
        "普段の売買代金が少ない「薄商い」の銘柄で、出来高が急増したり株価が短期間で大きく変動したりしている状態です。"
        "取引参加者が少ないため、少数の投資家グループでも株価を動かしやすく、"
        "相場操縦（いわゆる「仕手株」）的な値動きに巻き込まれるリスクが相対的に高いと考えられます。"
        "統計的な目安に過ぎず断定的な判断ではありませんが、値動きが荒く損失リスクも大きいため、投資判断は特に慎重に行ってください。"
    ),
    "出来高急増（注目）": (
        "普段からある程度の売買代金がある銘柄で、出来高が急増し株価が大きく動いている状態です。"
        "決算発表や好材料のニュースなど、何らかの材料をきっかけに市場の関心が高まっている可能性があります。"
        "薄商いの銘柄が急変する場合に比べると、相対的に多くの参加者に支持された動きである可能性が高いですが、"
        "値動きが大きいこと自体に変わりはないため、飛びつく前に材料や需給を確認することをおすすめします。"
    ),
    "52週レンジ": "過去52週間（約1年間）の最高値・最安値です。現在値がそのレンジのどのあたりに位置するかで、直近の相対的な高値圏・安値圏を把握する目安になります。",
    "トレンド確立度": (
        "5日・25日・75日移動平均線の並び順（短期>中期>長期＝パーフェクトオーダー）で、"
        "上昇トレンドがどれだけ「確立」しているかを判定します。"
        "パーフェクトオーダーでない場合は、まだ底値圏からの反発を試している段階（トレンド未確立）である可能性があります。"
        "底値を正確に当てるのは難しいため、トレンドが確立してから乗る方が再現性が高いとされます。"
    ),
    "25日線乖離率": (
        "現在の株価が25日移動平均線からどれだけ離れているかを示す割合です。"
        "プラスが大きいほど短期的に「伸びすぎ」ており、平均線に向けて戻される（反落する）リスクが相対的に高まる目安になります。"
    ),
    "初動の信頼度": (
        "直近1日の値動きが「本物の初動」か「だまし」かを、陽線/陰線・出来高（直近5日平均比）・"
        "終値が値幅のどこにあるか（強い引けか弱い引けか）・5日移動平均線より上かどうかから機械的に判定します。"
        "出来高を伴わない上昇や、値幅の下の方で引けた上昇は、翌日以降に反落する「だまし」の可能性が相対的に高いとされます。"
        "あくまで直近1日のパターンによる目安であり、材料の有無などは別途ご確認ください。"
    ),
    "信用倍率": (
        "信用取引で「買い建て（信用買い）」している株数と「売り建て（空売り）」している株数の比率（信用買い残 ÷ 信用売り残）です。"
        "信用買い残は将来いずれ反対売買（返済売り）される「将来の売り圧力」、信用売り残は将来の買い戻し（＝将来の買い圧力）とみなされます。"
        "信用買い残が積み上がっている（倍率が高い）ほど、株価が伸び悩んだ際に期日を迎えた信用買いの投げ売りが出やすく、上値を抑える要因になり得ます。"
        "逆に信用売り残が多い（倍率が低い）場合は、将来の買い戻し需要が相対的に大きいと考えられます。週次データのため直近の急な変化は反映されません。"
    ),
}


def glossary_help(*terms: str) -> str:
    """複数の用語をまとめてツールチップ用の説明文にする"""
    return "\n\n".join(f"【{t}】{GLOSSARY[t]}" for t in terms if t in GLOSSARY)


def render_html(html: str):
    """st.markdown(unsafe_allow_html=True) 用のヘルパー。
    Streamlitのマークダウン処理は、行頭に空白のみの行が挟まると
    それ以降の字下げされた行を「インデントコードブロック」とみなし、
    HTMLタグをそのまま文字列として表示してしまうことがある
    （f-string内の変数が空文字列になり、空白だけの行ができるケースで特に起きやすい）。
    各行の前後の空白を取り除いてから渡すことで、常にHTMLとして解釈させる。"""
    lines = [line.strip() for line in html.strip().splitlines()]
    st.markdown("\n".join(lines), unsafe_allow_html=True)

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

MARKET_INDICATORS_EXTRA = [
    {
        "ticker": "1306.T",
        "label": "TOPIX（ETF）",
        "unit": "円",
        "decimals": 1,
        "help": "東証株価指数(TOPIX)に連動するETF(1306)の価格。日経平均が値がさ株に影響されやすいのに対し、TOPIXは東証プライム全銘柄の時価総額加重平均に近く、市場全体の実感に近いとされます。",
    },
    {
        "ticker": "^DJI",
        "label": "NYダウ",
        "unit": "ドル",
        "decimals": 0,
        "help": "ニューヨークダウ工業株30種平均。米国株市場の代表指数で、前日の米国市場の値が表示されます。日本株は米国株の流れを引き継ぎやすい傾向があります。",
    },
    {
        "ticker": "CL=F",
        "label": "WTI原油先物",
        "unit": "ドル",
        "decimals": 2,
        "help": "米国産WTI原油の先物価格。資源・エネルギー関連株や、インフレ動向を通じて幅広い銘柄に影響します。",
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
        .stock-card .badge-thin {
            background: #fdecec;
            color: #c0392b;
            margin-left: 6px;
        }
        .stock-card .badge-spec {
            background: #fff1cc;
            color: #8a5a00;
            margin-left: 6px;
            animation: fadeInUp 0.4s ease;
        }
        .stock-card .badge-attention {
            background: #e7f0ff;
            color: #1552b5;
            margin-left: 6px;
            animation: fadeInUp 0.4s ease;
        }
        .stock-card .badge-trend-ok {
            background: #e8f8ee;
            color: #14804a;
            margin-left: 6px;
        }
        .stock-card .badge-trend-none {
            background: #f1f2f4;
            color: #9aa0a6;
            margin-left: 6px;
        }
        .stock-card .badge-entry-good {
            background: #e8f8ee;
            color: #14804a;
            margin-left: 6px;
        }
        .stock-card .badge-entry-bad {
            background: #fdecec;
            color: #c0392b;
            margin-left: 6px;
        }
        .stock-card .fundamentals-row {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px dashed #eceef1;
            display: flex;
            flex-wrap: wrap;
            gap: 6px 14px;
        }
        .stock-card .fundamentals-row span {
            font-size: 0.78rem;
            color: #6b7280;
            cursor: help;
        }
        .portfolio-summary {
            background: linear-gradient(120deg, #14192b, #262f4a);
            color: #ffffff;
            border-radius: 14px;
            padding: 18px 22px;
            margin-bottom: 14px;
        }
        .portfolio-summary .label {
            font-size: 0.8rem;
            color: #b9c0d6;
        }
        .portfolio-summary .value {
            font-size: 1.5rem;
            font-weight: 800;
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

        /* ---------------------------------------------------------------
           スマホ表示向けの調整（画面幅640px以下）
           ・st.columns()の横並びは狭い画面だと1列あたりが狭くなりすぎて
             数値や文字が折り返し・見切れの原因になるため、2列グリッドに変更
             （サイドバーは元々コンパクトなので対象外にする）。
           ・銘柄カード上部の「名前＋コード」と「騰落率＋バッジ」の行は、
             インラインstyleでflex-wrapが指定されていないため、バッジが
             複数付くと画面外にはみ出して文字が見えなくなっていた。
             ここを!important付きのクラスセレクタで上書きして折り返す
             （author stylesheetの!importantはインラインstyleより優先される）。
           ・見出しや指標・バッジのフォントサイズも全体的に少し縮小する。
           --------------------------------------------------------------- */
        @media (max-width: 640px) {
            .app-header {
                padding: 16px 16px;
            }
            .app-header h1 {
                font-size: 1.25rem;
            }
            .app-header p {
                font-size: 0.82rem;
            }
            div[data-testid="stMetric"] {
                padding: 10px 10px 8px 10px;
            }
            div[data-testid="stMetricValue"] {
                font-size: 1.15rem !important;
            }
            div[data-testid="stMetricLabel"] {
                font-size: 0.8rem !important;
            }

            /* st.columns()の横並びを2列グリッドに（サイドバーは除く） */
            [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                row-gap: 10px;
            }
            [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                min-width: 46% !important;
                flex: 1 1 46% !important;
                width: 46% !important;
            }
            section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
                flex-wrap: nowrap !important;
            }
            section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
                min-width: 0 !important;
                width: auto !important;
            }

            /* 銘柄カード：ヘッダー行がバッジではみ出さないよう折り返す */
            .stock-card {
                padding: 14px 14px;
            }
            .stock-card > div:first-child {
                flex-wrap: wrap !important;
                row-gap: 8px;
            }
            .stock-card > div:first-child > div:last-child {
                text-align: left !important;
                width: 100%;
            }
            .stock-card .name {
                font-size: 0.98rem;
            }
            .stock-card .pct {
                font-size: 1.35rem;
            }
            .stock-card .badge {
                font-size: 0.7rem;
                padding: 3px 8px;
                margin-left: 0;
                margin-right: 6px;
                margin-top: 4px;
            }
            .stock-card .badge-thin,
            .stock-card .badge-spec,
            .stock-card .badge-attention,
            .stock-card .badge-trend-ok,
            .stock-card .badge-trend-none,
            .stock-card .badge-entry-good,
            .stock-card .badge-entry-bad {
                margin-left: 0;
                margin-right: 6px;
            }
            .stock-card .fundamentals-row {
                gap: 6px 10px;
            }
            .stock-card .fundamentals-row span {
                font-size: 0.74rem;
            }

            .portfolio-summary {
                padding: 14px 16px;
            }
            .portfolio-summary .value {
                font-size: 1.2rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------
def _with_retry(fn, retries: int = 2, delay: float = 0.8):
    """yfinance/外部APIは一時的なタイムアウト・レート制限で失敗することがあるため、
    短い間隔を空けて数回リトライしてから諦める。"""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(delay)
    if last_exc is not None:
        raise last_exc


@st.cache_data(ttl=600, show_spinner=False)
def load_master(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    return df


@st.cache_data(ttl=600, show_spinner=False)
def fetch_history(ticker_code: str, start: dt.date, end: dt.date, is_index: bool = False) -> pd.DataFrame:
    """コードから .T ティッカーで日足OHLCを取得（指数の場合はそのままのティッカーを使用）"""
    ticker = ticker_code if is_index else f"{ticker_code}.T"

    def _do():
        return yf.download(
            ticker,
            start=start - dt.timedelta(days=10),
            end=end + dt.timedelta(days=1),
            progress=False,
            auto_adjust=False,
        )

    try:
        df = _with_retry(_do)
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
def fetch_fundamentals(code: str) -> dict:
    """PER・PBR・配当利回り・時価総額を取得する（取得できない項目は None）。
    yfinanceのinfo取得は失敗しやすいため、リトライしたうえで
    全滅した場合も空の辞書ではなく全項目Noneの辞書を返し、呼び出し側の分岐を簡単にする。"""
    empty = {"per": None, "pbr": None, "dividend_yield": None, "market_cap": None}

    def _do():
        t = yf.Ticker(f"{code}.T")
        return t.info or {}

    try:
        info = _with_retry(_do)
    except Exception:
        return empty
    if not info:
        return empty
    return {
        "per": info.get("trailingPE"),
        "pbr": info.get("priceToBook"),
        "dividend_yield": info.get("dividendYield"),
        "market_cap": info.get("marketCap"),
    }


def compute_liquidity(hist: pd.DataFrame, window: int = 20):
    """直近window営業日の平均出来高・平均売買代金(円)を計算する。
    薄商い銘柄の検知に使う。"""
    if hist is None or hist.empty or "Volume" not in hist.columns or "Close" not in hist.columns:
        return None, None
    recent = hist.tail(window)
    recent = recent.dropna(subset=["Volume", "Close"])
    if recent.empty:
        return None, None
    avg_volume = float(recent["Volume"].mean())
    avg_value = float((recent["Volume"] * recent["Close"]).mean())
    return avg_volume, avg_value


@st.cache_data(ttl=1800, show_spinner=False)
def detect_speculative_signal(code: str):
    """出来高急増・短期急騰急落を検知し、その性質を「薄商いでの急変（仕手株疑い）」と
    「出来高を伴う急変（注目＝決算・好材料などによる健全な値動きの可能性）」に区別する。

    区別の考え方: 急変が起きる直前（スパイク当日を含めない期間）の平均売買代金が
    LIQUIDITY_THIN_THRESHOLD を下回っていれば、もともと参加者が少ない薄商いの銘柄と判断し、
    少数の投資家でも値を動かしやすい＝仕手株的なリスクが相対的に高いとみなす。
    逆に普段からある程度の売買代金がある銘柄の急変は、決算・材料公表など何らかのきっかけで
    多くの参加者の関心が集まっている可能性が高いため、警告ではなく「注目」として扱う。

    あくまで統計的な目安であり、断定的な判断ではない点に注意（呼び出し側でその旨を必ず案内すること）。
    戻り値: (level, reason)
      level: "warning"（薄商い×急変）, "attention"（出来高を伴う急変）, None（該当なし）
      reason: 判定理由の説明文（str）または None"""
    end = dt.date.today()
    start = end - dt.timedelta(days=60)
    try:
        hist = fetch_history(code, start, end)
    except Exception:
        return None, None
    if hist is None or hist.empty or "Volume" not in hist.columns or "Close" not in hist.columns:
        return None, None
    hist = hist.sort_index().dropna(subset=["Volume", "Close"])
    if len(hist) < 2:
        return None, None

    reasons = []

    # 出来高急増: 直近1日の出来高が、それ以前の平均出来高の一定倍率以上
    prior_volume = hist["Volume"].iloc[-21:-1] if len(hist) >= 21 else hist["Volume"].iloc[:-1]
    prior_close = hist["Close"].iloc[-21:-1] if len(hist) >= 21 else hist["Close"].iloc[:-1]
    if not prior_volume.empty and prior_volume.mean() > 0:
        latest_volume = hist["Volume"].iloc[-1]
        spike_ratio = latest_volume / prior_volume.mean()
        if spike_ratio >= SPEC_VOLUME_SPIKE_RATIO:
            reasons.append(f"出来高が直近平均の{spike_ratio:.1f}倍に急増")

    # 短期急騰・急落: 直近1営業日の値動き
    c_prev, c_last = hist["Close"].iloc[-2], hist["Close"].iloc[-1]
    if c_prev and c_prev > 0:
        day_pct = (c_last - c_prev) / c_prev * 100
        if abs(day_pct) >= SPEC_DAY_PCT_THRESHOLD:
            reasons.append(f"直近1営業日で{day_pct:+.1f}%の値動き")

    # 短期急騰・急落: 直近5営業日の値動き
    if len(hist) >= 6:
        c0, c1 = hist["Close"].iloc[-6], hist["Close"].iloc[-1]
        if c0 and c0 > 0:
            week_pct = (c1 - c0) / c0 * 100
            if abs(week_pct) >= SPEC_WEEK_PCT_THRESHOLD:
                reasons.append(f"直近5営業日で{week_pct:+.1f}%の値動き")

    if not reasons:
        return None, None

    # 急変が起きる「前」の売買代金で薄商いかどうかを判定する
    # （急増した出来高そのものを含めると、薄商いの銘柄でも一時的に「厚く」見えてしまうため）
    baseline_value = None
    if not prior_volume.empty and not prior_close.empty:
        baseline_value = float((prior_volume * prior_close).mean())
    is_thin_baseline = baseline_value is not None and baseline_value < LIQUIDITY_THIN_THRESHOLD

    reason_text = "・".join(reasons)
    level = "warning" if is_thin_baseline else "attention"
    return level, reason_text


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_52w_range(code: str):
    """過去52週間（約1年）の高値・安値を取得する。取得できない場合は (None, None)。"""
    end = dt.date.today()
    start = end - dt.timedelta(days=365)
    try:
        hist = fetch_history(code, start, end)
    except Exception:
        return None, None
    if hist is None or hist.empty or "High" not in hist.columns or "Low" not in hist.columns:
        return None, None
    high = hist["High"].max()
    low = hist["Low"].min()
    if pd.isna(high) or pd.isna(low):
        return None, None
    return float(high), float(low)


@st.cache_data(ttl=1800, show_spinner=False)
def compute_trend_status(code: str):
    """MA5・MA25・MA75の並び順（パーフェクトオーダー）と25日線からの乖離率・25日線の傾きから、
    上昇トレンドが「確立」しているか、まだ底値圏からの反発を試している段階（未確立）かを判定する。

    戻り値: dict または None（データ不足で判定できない場合）
      structure: "perfect_order"（短期>中期>長期＝上昇トレンド確立） / "below"（それ以外＝未確立）
      dev25: 25日線からの乖離率(%)
      ma25_slope10: 直近10営業日での25日線自体の変化率(%)（プラス＝25日線自体が上向き）
      extended: 乖離率が伸びすぎ水準（TREND_EXTENDED_DEV_THRESHOLD）を超えているか
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=200)
    try:
        hist = fetch_history(code, start, end)
    except Exception:
        return None
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    closes = hist["Close"].dropna()
    if len(closes) < 35:
        return None

    def _ma(period, end_offset):
        idx_end = len(closes) - end_offset
        window = closes.iloc[max(0, idx_end - period):idx_end]
        if len(window) < period:
            return None
        return float(window.mean())

    price = float(closes.iloc[-1])
    ma5 = _ma(5, 0)
    ma25 = _ma(25, 0)
    ma75 = _ma(75, 0)
    ma25_10d_ago = _ma(25, 10)
    if ma5 is None or ma25 is None:
        return None
    structure = "perfect_order" if (ma5 > ma25 and (ma75 is None or ma25 > ma75)) else "below"
    dev25 = (price - ma25) / ma25 * 100 if ma25 else None
    ma25_slope10 = (
        (ma25 - ma25_10d_ago) / ma25_10d_ago * 100
        if (ma25_10d_ago is not None and ma25_10d_ago != 0)
        else None
    )
    extended = dev25 is not None and dev25 >= TREND_EXTENDED_DEV_THRESHOLD
    return {
        "structure": structure,
        "dev25": dev25,
        "ma25_slope10": ma25_slope10,
        "extended": extended,
    }


@st.cache_data(ttl=1800, show_spinner=False)
def compute_entry_quality(code: str):
    """直近1営業日の値動きが「本物の初動」か「だまし」かを判定する。
    陽線/陰線・出来高（直近5営業日平均比）・終値の値幅内位置・5日移動平均線との位置関係から機械的に判定する。

    戻り値: dict または None（データ不足・陰線で判定対象外の場合）
      bullish: 陽線かどうか
      vol_ratio: 出来高の直近5営業日平均比
      close_pos: 終値がその日の値幅のどこにあるか（0=安値、1=高値）
      above_ma5: 終値が5日移動平均線より上かどうか
      reliable: 陽線かつ出来高・終値位置・MA5の3条件を満たすか（True=信頼度が高い初動、False=だましの可能性）
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=60)
    try:
        hist = fetch_history(code, start, end)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(hist.columns):
        return None
    hist = hist.dropna(subset=list(needed)).sort_index()
    if len(hist) < 7:
        return None

    last = hist.iloc[-1]
    o, h, l, c, v = last["Open"], last["High"], last["Low"], last["Close"], last["Volume"]
    bullish = c >= o

    prior_vol = hist["Volume"].iloc[-6:-1]
    vol_ratio = float(v / prior_vol.mean()) if prior_vol.mean() > 0 else None

    close_pos = float((c - l) / (h - l)) if h > l else 1.0

    ma5_closes = hist["Close"].iloc[-6:-1]
    above_ma5 = bool(c > ma5_closes.mean()) if len(ma5_closes) == 5 else None

    reliable = None
    if bullish:
        reliable = (
            vol_ratio is not None and vol_ratio >= ENTRY_VOLUME_RATIO_THRESHOLD
            and close_pos >= ENTRY_CLOSE_POS_THRESHOLD
            and bool(above_ma5)
        )

    return {
        "bullish": bool(bullish),
        "vol_ratio": vol_ratio,
        "close_pos": close_pos,
        "above_ma5": above_ma5,
        "reliable": reliable,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_margin_balance(code: str):
    """かぶたんの週次信用残時系列データ（kabuka?ashi=shin）から、直近の信用買い残・売り残・信用倍率と、
    前週からの買い残の増減（積み上がっているか減っているか）を取得する。
    信用買い残は将来いずれ反対売買（返済売り）される「将来の売り圧力」の目安になる。

    戻り値: dict または None（取得・解析できない場合）
      date: 直近データの週（YY/MM/DD文字列）
      buy_balance / sell_balance: 信用買い残・売り残（株）
      margin_ratio: 信用倍率（買い残 ÷ 売り残）
      buy_change_pct: 前週からの買い残の増減率(%)
      trend: "increasing" / "decreasing" / "flat"（買い残の増減傾向）
    """

    def _num(s):
        s = (s or "").strip().replace(",", "")
        if s in ("", "－", "-", "―"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _date_key(d):
        try:
            y, mo, da = d.split("/")
            return (int(y), int(mo), int(da))
        except Exception:
            return (0, 0, 0)

    try:
        url = f"https://kabutan.jp/stock/kabuka?code={code}&ashi=shin"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=8)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return None

    row_re = re.compile(
        r"<tr>\s*<th[^>]*><time[^>]*>([\d/]+)</time></th>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"<td[^>]*><span[^>]*>([^<]*)</span></td>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"<td[^>]*>([^<]*)</td>\s*"
        r"</tr>"
    )
    rows = []
    try:
        for m in row_re.finditer(html):
            date_str, _close_s, _week_pct_s, _avg_price_s, _volume_s, sell_s, buy_s, ratio_s = m.groups()
            buy_balance = _num(buy_s)
            sell_balance = _num(sell_s)
            if buy_balance is None and sell_balance is None:
                continue
            rows.append(
                {
                    "date": date_str,
                    "buy_balance": buy_balance,
                    "sell_balance": sell_balance,
                    "margin_ratio": _num(ratio_s),
                }
            )
    except Exception:
        return None
    if not rows:
        return None

    rows.sort(key=lambda r: _date_key(r["date"]), reverse=True)
    latest = rows[0]
    if latest["buy_balance"] is None:
        return None
    prev = next((r for r in rows[1:] if r["buy_balance"] is not None), None)

    buy_change_pct = None
    trend = None
    if prev is not None and prev["buy_balance"]:
        buy_change_pct = (latest["buy_balance"] - prev["buy_balance"]) / prev["buy_balance"] * 100
        if buy_change_pct > 3:
            trend = "increasing"
        elif buy_change_pct < -3:
            trend = "decreasing"
        else:
            trend = "flat"

    return {
        "date": latest["date"],
        "buy_balance": latest["buy_balance"],
        "sell_balance": latest["sell_balance"],
        "margin_ratio": latest["margin_ratio"],
        "buy_change_pct": buy_change_pct,
        "trend": trend,
    }


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """簡易RSI（相対力指数）を計算する。0〜100で、70以上=買われすぎ、30以下=売られすぎの目安。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    # 値下がりが一度もない区間ではavg_loss=0でRSIが未定義になるため100（買われすぎ側の極値）とする
    rsi = rsi.fillna(100)
    rsi[avg_gain.isna()] = pd.NA
    return rsi


def format_ratio(v, unit: str = "倍") -> str:
    if v is None or pd.isna(v) or v <= 0:
        return "―"
    return f"{v:.1f}{unit}"


def format_dividend_yield(v) -> str:
    if v is None or pd.isna(v):
        return "―"
    # yfinanceのdividendYieldは版によって「0.023」(比率)と「2.3」(％そのもの)が混在するため、
    # 1未満なら比率とみなして100倍する
    pct = v * 100 if v < 1 else v
    if pct <= 0:
        return "―"
    return f"{pct:.2f}%"


def format_market_cap(v) -> str:
    if v is None or pd.isna(v) or v <= 0:
        return "―"
    if v >= 1e12:
        return f"{v / 1e12:.2f}兆円"
    if v >= 1e8:
        return f"{v / 1e8:,.0f}億円"
    return f"{v:,.0f}円"


def format_trading_value(v) -> str:
    if v is None or pd.isna(v) or v <= 0:
        return "―"
    if v >= 1e8:
        return f"{v / 1e8:,.1f}億円/日"
    if v >= 1e4:
        return f"{v / 1e4:,.0f}万円/日"
    return f"{v:,.0f}円/日"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_company_name(code: str):
    """証券コードから会社名を取得（見つからない/データが無い場合は None）"""
    ticker = f"{code}.T"

    def _do():
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        return info.get("longName") or info.get("shortName") or code

    try:
        return _with_retry(_do)
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

    def _do():
        t = yf.Ticker(ticker)
        edf = t.get_earnings_dates(limit=12)
        if edf is None or edf.empty:
            return []
        return sorted(d.date() for d in edf.index.to_pydatetime())

    try:
        return _with_retry(_do)
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
# ポートフォリオ（保有銘柄）の読み書き
# ---------------------------------------------------------------------------
def load_portfolio() -> list:
    """保有銘柄のリストを読み込む。各要素は
    {id, code, shares, cost}（cost=取得単価/株）"""
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            return []
    return []


def save_portfolio(holdings: list):
    try:
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(holdings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_latest_price(code: str):
    """ポートフォリオの評価額計算用に、直近の終値を1件だけ取得する"""
    hist = fetch_history(code, dt.date.today() - dt.timedelta(days=14), dt.date.today())
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


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
# セクターの勢い（上位・下位） — トップページのハイライト
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
            render_html(_sector_row_html(r))
    with col_l:
        st.markdown("**📉 勢いのないセクター（下落幅が大きい）**")
        for _, r in losers.iterrows():
            render_html(_sector_row_html(r))
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

with st.expander("📖 用語集（PER・PBR・配当利回り・RSIなど）"):
    for term, desc in GLOSSARY.items():
        st.markdown(f"**{term}**：{desc}")

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
        fundamentals = fetch_fundamentals(code)
        avg_volume, avg_value = compute_liquidity(hist)
        spec_level, spec_reason = detect_speculative_signal(code)
        trend_status = compute_trend_status(code)
        entry_quality = compute_entry_quality(code)
        rows.append(
            {
                "code": code,
                "name": name,
                "pct": round(pct, 2) if pct is not None else None,
                "base_p": round(base_p, 1) if base_p is not None else None,
                "latest_p": round(latest_p, 1) if latest_p is not None else None,
                "earnings": next_earnings_label(earnings_dates, today),
                "earnings_raw": earnings_dates,
                "per": fundamentals.get("per"),
                "pbr": fundamentals.get("pbr"),
                "dividend_yield": fundamentals.get("dividend_yield"),
                "market_cap": fundamentals.get("market_cap"),
                "avg_volume": avg_volume,
                "avg_value": avg_value,
                "is_thin": (avg_value is not None) and (avg_value < LIQUIDITY_THIN_THRESHOLD),
                "spec_level": spec_level,
                "spec_reason": spec_reason,
                "trend_status": trend_status,
                "entry_quality": entry_quality,
            }
        )

result_df = pd.DataFrame(rows).sort_values("pct", ascending=False, na_position="last")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📊 セクター比較", "🕯️ 個別チャート", "🗓️ 決算カレンダー", "💼 ポートフォリオ", "🌍 市場概況"]
)

# --- タブ1: セクター比較 ---------------------------------------------------
with tab1:
    st.caption(f"「{selected_sector}」・直近{period_choice}のトータル騰落率")

    with st.expander("🔎 絞り込み条件（スクリーニング）"):
        f1, f2 = st.columns(2)
        with f1:
            per_max = st.number_input(
                "PER 上限（倍）", min_value=0.0, value=0.0, step=1.0,
                help=glossary_help("PER") + "\n\n0のときは絞り込みません。",
            )
        with f2:
            dy_min = st.number_input(
                "配当利回り 下限（%）", min_value=0.0, value=0.0, step=0.1,
                help=glossary_help("配当利回り") + "\n\n0のときは絞り込みません。",
            )
        f3, f4 = st.columns(2)
        with f3:
            exclude_thin = st.checkbox(
                "薄商い銘柄を除外する",
                value=False,
                help=glossary_help("出来高")
                + f"\n\n平均売買代金が{LIQUIDITY_THIN_THRESHOLD:,}円/日を下回る銘柄を一覧から除外します。",
            )
        with f4:
            exclude_spec = st.checkbox(
                "仕手株疑いの銘柄を除外する",
                value=False,
                help=glossary_help("仕手株疑い")
                + "\n\n薄商いの状態で出来高急増や急な値動きが検知された銘柄を一覧から除外します。"
                "出来高を伴う値動き（注目バッジ）は対象外です。",
            )
        f5, f6 = st.columns(2)
        with f5:
            budget_max = st.number_input(
                "購入予算 上限（円・100株単位）", min_value=0, value=0, step=10000,
                help="入力した予算で100株（1単元）買える株価（予算÷100円）以下の銘柄だけに絞り込みます。"
                "\n\n0のときは絞り込みません。",
            )
        with f6:
            exclude_fake_start = st.checkbox(
                "だまし初動（信頼度が低い上昇）を除外する",
                value=False,
                help=glossary_help("初動の信頼度")
                + "\n\n直近1営業日が陽線でも、出来高・終値位置・5日移動平均線のいずれかの条件を満たさない銘柄を除外します。"
                "陰線の銘柄や、上昇していない銘柄は対象外（除外されません）。",
            )

    filtered_df = result_df.copy()
    if per_max > 0:
        filtered_df = filtered_df[filtered_df["per"].apply(lambda v: pd.notna(v) and 0 < v <= per_max)]
    if dy_min > 0:
        def _dy_pct(v):
            if pd.isna(v):
                return None
            return v * 100 if v < 1 else v
        filtered_df = filtered_df[filtered_df["dividend_yield"].apply(lambda v: (_dy_pct(v) or -1) >= dy_min)]
    if exclude_thin:
        filtered_df = filtered_df[~filtered_df["is_thin"].fillna(False)]
    if exclude_spec:
        filtered_df = filtered_df[filtered_df["spec_level"] != "warning"]
    if budget_max > 0:
        max_price = budget_max / SHARE_UNIT
        filtered_df = filtered_df[filtered_df["latest_p"].apply(lambda v: pd.notna(v) and v <= max_price)]
    if exclude_fake_start:
        def _is_fake_start(eq):
            return isinstance(eq, dict) and eq.get("bullish") and eq.get("reliable") is False
        filtered_df = filtered_df[~filtered_df["entry_quality"].apply(_is_fake_start)]

    if filtered_df.empty:
        st.warning("絞り込み条件に一致する銘柄がありませんでした。条件を緩めてみてください。")
    else:
        st.caption(f"表示中: {len(filtered_df)} / {len(result_df)} 銘柄")

    for _, r in filtered_df.iterrows():
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

        per_text = format_ratio(r["per"])
        pbr_text = format_ratio(r["pbr"])
        dy_text = format_dividend_yield(r["dividend_yield"])
        mcap_text = format_market_cap(r["market_cap"])
        vol_text = format_trading_value(r["avg_value"])
        thin_badge = (
            f'<span class="badge badge-thin" title="{GLOSSARY["出来高"]}">⚠ 薄商い</span>'
            if r["is_thin"]
            else ""
        )
        if r["spec_level"] == "warning":
            spec_tooltip = f"{r['spec_reason']}。{GLOSSARY['仕手株疑い']}"
            spec_badge = f'<span class="badge badge-spec" title="{spec_tooltip}">🚨 仕手株疑い</span>'
        elif r["spec_level"] == "attention":
            spec_tooltip = f"{r['spec_reason']}。{GLOSSARY['出来高急増（注目）']}"
            spec_badge = f'<span class="badge badge-attention" title="{spec_tooltip}">👀 注目</span>'
        else:
            spec_badge = ""

        trend = r["trend_status"]
        if trend and trend.get("structure") == "perfect_order":
            dev_txt = f"{trend['dev25']:+.1f}%" if trend.get("dev25") is not None else "―"
            extended_note = "（伸びすぎ注意）" if trend.get("extended") else ""
            trend_tooltip = f"25日線乖離率 {dev_txt}{extended_note}。{GLOSSARY['トレンド確立度']}"
            trend_badge = f'<span class="badge badge-trend-ok" title="{trend_tooltip}">📈 トレンド確立 {dev_txt}</span>'
        elif trend:
            trend_badge = f'<span class="badge badge-trend-none" title="{GLOSSARY["トレンド確立度"]}">🌱 未確立</span>'
        else:
            trend_badge = ""

        eq = r["entry_quality"]
        if eq and eq.get("bullish") and eq.get("reliable") is True:
            entry_badge = f'<span class="badge badge-entry-good" title="{GLOSSARY["初動の信頼度"]}">✅ 出来高を伴う陽線</span>'
        elif eq and eq.get("bullish") and eq.get("reliable") is False:
            entry_badge = f'<span class="badge badge-entry-bad" title="{GLOSSARY["初動の信頼度"]}">⚠ だまし初動の可能性</span>'
        else:
            entry_badge = ""

        render_html(
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
                        {thin_badge}
                        {spec_badge}
                        {trend_badge}
                        {entry_badge}
                    </div>
                </div>
                <div class="fundamentals-row">
                    <span title="{GLOSSARY['PER']}">PER {per_text}</span>
                    <span title="{GLOSSARY['PBR']}">PBR {pbr_text}</span>
                    <span title="{GLOSSARY['配当利回り']}">配当利回り {dy_text}</span>
                    <span title="{GLOSSARY['時価総額']}">時価総額 {mcap_text}</span>
                    <span title="{GLOSSARY['出来高']}">出来高 {vol_text}</span>
                </div>
            </div>
            """
        )

# --- タブ2: 個別チャート ---------------------------------------------------
with tab2:
    focus_label = st.selectbox("銘柄を選択", options=selected_labels)
    focus_code = focus_label.split(" ")[0]
    focus_name = master.loc[master["code"] == focus_code, "name"].values[0]
    st.caption(f"🔗 詳しく調べる → [Yahoo!ファイナンスで{focus_name}を見る](https://finance.yahoo.co.jp/quote/{focus_code}.T)")

    focus_fundamentals = fetch_fundamentals(focus_code)
    fc1, fc2, fc3, fc4, fc5 = st.columns(5)
    with fc1:
        st.metric("PER", format_ratio(focus_fundamentals.get("per")), help=glossary_help("PER"))
    with fc2:
        st.metric("PBR", format_ratio(focus_fundamentals.get("pbr")), help=glossary_help("PBR"))
    with fc3:
        st.metric(
            "配当利回り",
            format_dividend_yield(focus_fundamentals.get("dividend_yield")),
            help=glossary_help("配当利回り"),
        )
    with fc4:
        st.metric("時価総額", format_market_cap(focus_fundamentals.get("market_cap")), help=glossary_help("時価総額"))
    with fc5:
        high_52w, low_52w = fetch_52w_range(focus_code)
        if high_52w is not None and low_52w is not None and high_52w > low_52w:
            latest_close_for_range = get_latest_price(focus_code)
            if latest_close_for_range is not None:
                position_pct = (latest_close_for_range - low_52w) / (high_52w - low_52w) * 100
                range_value = f"位置 {position_pct:.0f}%"
            else:
                range_value = "―"
            st.metric(
                "52週レンジ",
                range_value,
                help=glossary_help("52週レンジ") + f"\n\n52週高値: {high_52w:,.1f}円 ／ 52週安値: {low_52w:,.1f}円",
            )
        else:
            st.metric("52週レンジ", "―", help=glossary_help("52週レンジ"))

    spec_level_focus, spec_reason_focus = detect_speculative_signal(focus_code)
    if spec_level_focus == "warning":
        st.warning(
            f"🚨 この銘柄は薄商いの状態で値動きが急変している可能性があります（{spec_reason_focus}）。"
            "参加者が少ないため、少数の投資家グループでも株価を動かしやすく、仕手株的な値動きに巻き込まれるリスクが相対的に高いと考えられます。"
            "値動きが荒く損失リスクも大きいため、投資判断は特に慎重に行ってください。"
        )
    elif spec_level_focus == "attention":
        st.info(
            f"👀 この銘柄は出来高を伴って値動きが急変しています（{spec_reason_focus}）。"
            "決算発表や好材料のニュースなど、何らかの材料で市場の関心が高まっている可能性があります。"
            "値動きが大きいこと自体に変わりはないため、材料や需給を確認したうえでご検討ください。"
        )

    trend_focus = compute_trend_status(focus_code)
    entry_focus = compute_entry_quality(focus_code)
    margin_focus = fetch_margin_balance(focus_code)
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        if trend_focus:
            if trend_focus.get("structure") == "perfect_order":
                dev_txt = f"{trend_focus['dev25']:+.1f}%" if trend_focus.get("dev25") is not None else "―"
                extended_note = "（伸びすぎ注意）" if trend_focus.get("extended") else ""
                st.metric("トレンド確立度", f"📈 確立 {dev_txt}{extended_note}", help=glossary_help("トレンド確立度", "25日線乖離率"))
            else:
                st.metric("トレンド確立度", "🌱 未確立（反発初期の可能性）", help=glossary_help("トレンド確立度", "25日線乖離率"))
        else:
            st.metric("トレンド確立度", "―", help=glossary_help("トレンド確立度"))
    with tc2:
        if entry_focus and entry_focus.get("bullish"):
            if entry_focus.get("reliable") is True:
                st.metric("直近1日の初動の信頼度", "✅ 出来高を伴う陽線", help=glossary_help("初動の信頼度"))
            elif entry_focus.get("reliable") is False:
                st.metric("直近1日の初動の信頼度", "⚠ だましの可能性", help=glossary_help("初動の信頼度"))
        elif entry_focus is not None:
            st.metric("直近1日の初動の信頼度", "陰線（対象外）", help=glossary_help("初動の信頼度"))
        else:
            st.metric("直近1日の初動の信頼度", "―", help=glossary_help("初動の信頼度"))
    with tc3:
        if margin_focus and margin_focus.get("buy_balance") is not None:
            trend_note = {"increasing": "（増加）", "decreasing": "（減少）", "flat": "（横ばい）"}.get(
                margin_focus.get("trend"), ""
            )
            help_text = glossary_help("信用倍率") + f"\n\n{margin_focus['date']}時点のデータです。"
            if margin_focus.get("margin_ratio") is not None:
                help_text += f" 信用倍率: {margin_focus['margin_ratio']:.2f}倍。"
            if margin_focus.get("sell_balance") is not None:
                help_text += f" 信用売り残: {margin_focus['sell_balance']:,.0f}株。"
            st.metric("信用買い残", f"{margin_focus['buy_balance']:,.0f}株{trend_note}", help=help_text)
        else:
            st.metric("信用買い残", "―", help=glossary_help("信用倍率") + "\n\nこの銘柄はデータを取得できませんでした。")

    chart_days = st.select_slider(
        "表示期間",
        options=[10, 30, 60, 90, 180, 365, 730],
        value=180,
        format_func=lambda d: f"{d}日",
    )
    show_technical = st.checkbox(
        "テクニカル指標を表示する（移動平均線・出来高・RSI）",
        value=True,
        help=glossary_help("移動平均線", "出来高", "RSI"),
    )
    chart_start = today - dt.timedelta(days=chart_days)

    # 移動平均線(最大75日)が表示期間の最初から途切れないよう、表示開始日より前のぶんも多めに取得する
    ma_buffer_start = chart_start - dt.timedelta(days=160)
    hist_ext = fetch_history(focus_code, ma_buffer_start, today)
    if not hist_ext.empty:
        hist_ext = hist_ext.sort_index()
        hist_ext["MA5"] = hist_ext["Close"].rolling(window=5, min_periods=5).mean()
        hist_ext["MA25"] = hist_ext["Close"].rolling(window=25, min_periods=25).mean()
        hist_ext["MA75"] = hist_ext["Close"].rolling(window=75, min_periods=75).mean()
        hist_ext["RSI14"] = compute_rsi(hist_ext["Close"], period=14)
        hist = hist_ext[(hist_ext.index.date >= chart_start) & (hist_ext.index.date <= today)]
        if hist.empty:
            hist = hist_ext
    else:
        hist = hist_ext

    if hist.empty:
        st.warning("株価データを取得できませんでした。銘柄コードや通信環境をご確認ください。")
    else:
        avg_volume, avg_value = compute_liquidity(hist)
        if avg_value is not None and avg_value < LIQUIDITY_THIN_THRESHOLD:
            st.warning(
                f"⚠ この銘柄は直近の平均売買代金が{format_trading_value(avg_value)}と少なめです（薄商い）。"
                "株価データが実勢の値動きを反映しにくく、急な値動きが出やすいので参考程度にご覧ください。"
            )

        if show_technical:
            fig = make_subplots(
                rows=3,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.04,
                row_heights=[0.55, 0.18, 0.27],
            )
        else:
            fig = make_subplots(rows=1, cols=1)

        fig.add_trace(
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
            ),
            row=1,
            col=1,
        )

        if show_technical:
            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=hist["MA5"], mode="lines", name="5日移動平均線",
                    line=dict(color="#14804a", width=1.1),
                ),
                row=1, col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=hist["MA25"], mode="lines", name="25日移動平均線",
                    line=dict(color="#f5a623", width=1.3),
                ),
                row=1, col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=hist["MA75"], mode="lines", name="75日移動平均線",
                    line=dict(color="#6b46c1", width=1.3),
                ),
                row=1, col=1,
            )

            volume_colors = [
                UP_COLOR if c >= o else DOWN_COLOR for o, c in zip(hist["Open"], hist["Close"])
            ]
            fig.add_trace(
                go.Bar(x=hist.index, y=hist["Volume"], name="出来高", marker_color=volume_colors, opacity=0.6),
                row=2, col=1,
            )

            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=hist["RSI14"], mode="lines", name="RSI(14)",
                    line=dict(color="#0d6efd", width=1.3),
                ),
                row=3, col=1,
            )
            fig.add_hline(y=70, row=3, col=1, line_dash="dot", line_color=UP_COLOR, line_width=1)
            fig.add_hline(y=30, row=3, col=1, line_dash="dot", line_color=DOWN_COLOR, line_width=1)
            fig.update_yaxes(title_text="株価(円)", row=1, col=1)
            fig.update_yaxes(title_text="出来高", row=2, col=1)
            fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
        else:
            fig.update_yaxes(title_text="株価(円)", row=1, col=1)

        earnings_dates = fetch_earnings_dates(focus_code)
        visible_earnings = [
            d for d in earnings_dates if chart_start <= d <= today + dt.timedelta(days=365)
        ]
        vline_rows = (1, 2, 3) if show_technical else (1,)
        for d in visible_earnings:
            for rw in vline_rows:
                fig.add_vline(x=pd.Timestamp(d), row=rw, col=1, line_width=1, line_dash="dash", line_color="#f5a623")
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
            hovermode="x unified",
            height=820 if show_technical else 600,
            margin=dict(t=50, b=10, l=10, r=10),
            plot_bgcolor="white",
            dragmode="zoom",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
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
                key=f"candle_{focus_code}_{chart_days}_{show_technical}",
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
                    curve_num = p0.get("curve_number") if isinstance(p0, dict) else getattr(p0, "curve_number", None)
                    # ローソク足(trace 0)以外（移動平均線・出来高・RSI）のクリックはピン留め対象外にする
                    if curve_num is None or curve_num == 0:
                        idx = p0.get("point_index") if isinstance(p0, dict) else getattr(p0, "point_index", None)
                        if idx is not None and 0 <= idx < len(hist):
                            clicked_row = hist.iloc[idx]
                            clicked_date = hist.index[idx].date()
            except Exception:
                clicked_row = None

        if clicked_row is not None:
            render_html(
                f"""
                <div class="pin-price">
                    📍 <b>{clicked_date}</b> の株価
                    始値 {clicked_row['Open']:.1f}円 ／
                    高値 {clicked_row['High']:.1f}円 ／
                    安値 {clicked_row['Low']:.1f}円 ／
                    終値 <b>{clicked_row['Close']:.1f}円</b>
                </div>
                """
            )
        else:
            st.caption("チャート上の見たい地点をクリックすると、その日の株価がピン留め表示されます。マウスを合わせるだけでも数値が出ます。")

        pct, base_p, latest_p = pct_change_over_period(hist, chart_start, today)
        m1, m2, m3 = st.columns(3)
        with m1:
            if pct is not None:
                st.metric(label="表示期間トータル騰落率", value=f"{latest_p:.1f} 円", delta=f"{pct:+.2f}%")
        with m2:
            if visible_earnings:
                st.metric(label="直近の決算発表日", value=str(visible_earnings[0]))
            else:
                st.metric(label="決算発表日", value="情報なし")
        with m3:
            st.metric(
                label="平均出来高（直近20営業日）",
                value=format_trading_value(avg_value),
                help=glossary_help("出来高"),
            )

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

# --- タブ4: ポートフォリオ -------------------------------------------------
with tab4:
    st.caption("保有銘柄・株数・取得単価を登録すると、現在の評価額と含み損益を確認できます。")

    if "portfolio" not in st.session_state:
        st.session_state["portfolio"] = load_portfolio()
    portfolio = st.session_state["portfolio"]

    with st.expander("➕ 保有銘柄を追加する", expanded=len(portfolio) == 0):
        all_labels_pf = (master["code"] + " " + master["name"]).tolist()
        add_label_pf = st.selectbox("銘柄", options=["選択してください"] + all_labels_pf, key="pf_add_select")
        pf_c1, pf_c2 = st.columns(2)
        with pf_c1:
            add_shares = st.number_input("株数", min_value=0, step=100, value=100, key="pf_add_shares")
        with pf_c2:
            add_cost = st.number_input("取得単価（1株あたり・円）", min_value=0.0, step=1.0, value=0.0, key="pf_add_cost")
        if st.button("ポートフォリオに追加", key="pf_add_btn"):
            if add_label_pf == "選択してください":
                st.warning("銘柄を選択してください。")
            elif add_shares <= 0 or add_cost <= 0:
                st.warning("株数・取得単価はいずれも0より大きい値を入力してください。")
            else:
                add_code = add_label_pf.split(" ")[0]
                add_name = master.loc[master["code"] == add_code, "name"].values[0]
                # 同じ銘柄が既にあれば株数を合算し、取得単価は加重平均に更新する
                existing = next((h for h in portfolio if h["code"] == add_code), None)
                if existing:
                    total_shares = existing["shares"] + add_shares
                    existing["cost"] = (
                        existing["cost"] * existing["shares"] + add_cost * add_shares
                    ) / total_shares
                    existing["shares"] = total_shares
                else:
                    portfolio.append(
                        {
                            "id": str(uuid.uuid4()),
                            "code": add_code,
                            "name": add_name,
                            "shares": add_shares,
                            "cost": add_cost,
                        }
                    )
                save_portfolio(portfolio)
                st.success(f"「{add_name}」を追加しました。")
                st.rerun()

    if not portfolio:
        st.info("まだ保有銘柄が登録されていません。上の「➕ 保有銘柄を追加する」から登録してください。")
    else:
        total_cost = 0.0
        total_value = 0.0
        pf_rows = []
        with st.spinner("評価額を計算中…"):
            for h in portfolio:
                latest_price = get_latest_price(h["code"])
                cost_amount = h["shares"] * h["cost"]
                value_amount = h["shares"] * latest_price if latest_price is not None else None
                pl_amount = (value_amount - cost_amount) if value_amount is not None else None
                pl_pct = (pl_amount / cost_amount * 100) if (pl_amount is not None and cost_amount > 0) else None
                total_cost += cost_amount
                if value_amount is not None:
                    total_value += value_amount
                pf_rows.append(
                    {
                        **h,
                        "latest_price": latest_price,
                        "cost_amount": cost_amount,
                        "value_amount": value_amount,
                        "pl_amount": pl_amount,
                        "pl_pct": pl_pct,
                    }
                )

        total_pl = total_value - total_cost
        total_pl_pct = (total_pl / total_cost * 100) if total_cost > 0 else None
        summary_color = pct_color(total_pl_pct)
        render_html(
            f"""
            <div class="portfolio-summary">
                <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:16px;">
                    <div><div class="label">取得金額合計</div><div class="value">{total_cost:,.0f}円</div></div>
                    <div><div class="label">評価額合計</div><div class="value">{total_value:,.0f}円</div></div>
                    <div><div class="label">評価損益</div>
                        <div class="value" style="color:{summary_color if total_pl_pct is not None else '#ffffff'};">
                            {total_pl:+,.0f}円 （{f'{total_pl_pct:+.2f}%' if total_pl_pct is not None else '―'}）
                        </div>
                    </div>
                </div>
            </div>
            """
        )

        for h in pf_rows:
            color = pct_color(h["pl_pct"])
            arrow = pct_arrow(h["pl_pct"])
            price_text = f"{h['latest_price']:.1f}円" if h["latest_price"] is not None else "取得できません"
            pl_text = (
                f"{h['pl_amount']:+,.0f}円（{h['pl_pct']:+.2f}%）"
                if h["pl_amount"] is not None
                else "評価額を取得できませんでした"
            )
            col_card, col_del = st.columns([9, 1])
            with col_card:
                render_html(
                    f"""
                    <div class="stock-card">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <span class="name">{h['name']}</span>
                                <span class="code">{h['code']}</span>
                                <div class="price">{h['shares']:,}株 ／ 取得単価 {h['cost']:,.1f}円 ／ 現在値 {price_text}</div>
                            </div>
                            <div style="text-align:right;">
                                <div class="pct" style="color:{color}; font-size:1.2rem;">{arrow} {pl_text}</div>
                            </div>
                        </div>
                    </div>
                    """
                )
            with col_del:
                st.write("")
                if st.button("✕", key=f"pf_del_{h['id']}", help="この保有銘柄を削除"):
                    st.session_state["portfolio"] = [x for x in portfolio if x["id"] != h["id"]]
                    save_portfolio(st.session_state["portfolio"])
                    st.rerun()

    st.caption("⚠️ ポートフォリオ情報はアプリのサーバーに保存されます。今後アプリの機能追加・修正で更新すると、リセットされる場合があります。また、これは損益の目安表示であり、税金・手数料等は考慮していません。")

# --- タブ5: 市場概況 ---------------------------------------------------
with tab5:
    st.caption(f"個別銘柄だけでなく、市場全体の空気を確認できます。期間「{period_choice}」でのトータル変化率です。")

    st.markdown("#### 🌐 主要指標")
    all_indicators = MARKET_INDICATORS + MARKET_INDICATORS_EXTRA
    ext_cols = st.columns(len(all_indicators))
    for col, ind in zip(ext_cols, all_indicators):
        ind_hist = fetch_history(ind["ticker"], start_date, end_date, is_index=True)
        pct, base_p, latest_p = pct_change_over_period(ind_hist, start_date, end_date)
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

    st.markdown("")
    st.info(
        "💡 日経平均は構成225銘柄の「株価」を単純平均した指数のため、値がさ株（1株の値段が高い銘柄）の動きに"
        "強く引きずられる傾向があります。TOPIXは時価総額加重平均で市場全体の実感に近いとされるため、"
        "日経平均とTOPIXの向きが逆になっているときは、一部の値がさ株だけが相場を動かしている可能性を疑ってみてください。"
    )

    st.markdown("#### 🏭 セクター別 騰落ランキング（全業種）")
    if sector_momentum.empty:
        st.caption("セクターの勢いを取得できませんでした。")
    else:
        full_ranking = sector_momentum.sort_values("avg_pct", ascending=False).reset_index(drop=True)
        up_n = int((full_ranking["avg_pct"] > 0).sum())
        down_n = int((full_ranking["avg_pct"] < 0).sum())
        flat_n = len(full_ranking) - up_n - down_n
        bc1, bc2, bc3 = st.columns(3)
        bc1.metric("上昇セクター数", f"{up_n} / {len(full_ranking)}")
        bc2.metric("下落セクター数", f"{down_n} / {len(full_ranking)}")
        bc3.metric("変わらず", f"{flat_n} / {len(full_ranking)}")
        st.caption("各セクターの代表銘柄（数本）の平均騰落率をもとにした簡易ランキングです。個別銘柄の詳細は「📊 セクター比較」タブでご確認ください。")

        display_ranking = full_ranking.rename(
            columns={"sector": "セクター", "avg_pct": "平均騰落率(%)", "sample_n": "サンプル数"}
        ).copy()
        display_ranking["平均騰落率(%)"] = display_ranking["平均騰落率(%)"].map(lambda v: f"{v:+.2f}")
        st.dataframe(display_ranking, use_container_width=True, hide_index=True)
