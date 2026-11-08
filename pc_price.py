# -*- coding: utf-8 -*-
"""
PChome 商品價格查詢與視覺化（Streamlit）
執行方式：
    pip install -r requirements.txt
    streamlit run pc_price.py
"""
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
API_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
REQUEST_DELAY = 1.5  # 每抓一頁後休息幾秒，避免被網站封鎖
# 中文由瀏覽器負責顯示，所以不需要在電腦上安裝字型
CHART_FONT = "Noto Sans TC, Microsoft JhengHei, PingFang TC, sans-serif"
PALETTE = ["#6366F1", "#06B6D4", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6"]

# 抓品牌時要略過的字
STORE_WORDS = ("購物", "商城", "旗艦", "官方", "專賣", "商店", "館")
PROMO_WORDS = ("特價", "免運", "限時", "加碼", "現貨", "熱銷", "下殺", "優惠", "送", "贈", "折")
NOT_BRAND = {"USB", "LED", "PC", "DC", "AC", "RGB", "LCD", "TYPE"}


# ----------------------------------------------------------------------
# 資料：爬取（原 CODE01）
# ----------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def fetch_page(keyword: str, page: int) -> list:
    """抓取 PChome 搜尋結果的單一頁（同樣的關鍵字和頁數 10 分鐘內不重抓）。"""
    resp = requests.get(
        API_URL,
        params={"q": keyword, "page": page, "sort": "sale/dc"},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    prods = resp.json().get("prods") or []
    time.sleep(REQUEST_DELAY)
    return prods


def collect(keyword: str, pages: int) -> list:
    """依序抓取多頁，並顯示進度條。"""
    raw = []
    bar = st.progress(0.0, text="準備查詢…")
    try:
        for i in range(1, pages + 1):
            bar.progress((i - 1) / pages, text=f"正在抓取第 {i} / {pages} 頁…")
            items = fetch_page(keyword, i)
            if not items:  # 這一頁沒資料，後面也不會有
                break
            raw.extend(items)
        bar.progress(1.0, text="抓取完成")
    finally:
        bar.empty()
    return raw


# ----------------------------------------------------------------------
# 資料：清理（原 CODE02）
# ----------------------------------------------------------------------
def extract_brand(describe) -> str:
    """從商品描述第一行猜品牌：優先【品牌】，其次開頭的英文字，抓不到就是「其他」。"""
    if not isinstance(describe, str) or not describe.strip():
        return "其他"
    first = re.split(r"\\r\\n|\r?\n", describe.strip())[0]

    # 1) 【品牌】，略過賣場名稱與促銷字樣
    tags = list(re.finditer(r"【(.+?)】", first))
    for m in tags:
        t = m.group(1).strip()
        if len(t) <= 12 and not any(w in t for w in STORE_WORDS + PROMO_WORDS):
            return t.upper() if t.isascii() else t

    # 2) 開頭的英文字（若前面有賣場【】，就從【】之後開始找）
    text = first[tags[-1].end():].strip() if tags else first.strip()
    m = re.match(r"[A-Za-z][A-Za-z0-9\-]+", text)
    if m and m.group(0).upper() not in NOT_BRAND:
        return m.group(0).upper()
    return "其他"


def add_price_band(df: pd.DataFrame) -> pd.DataFrame:
    """依資料本身把價格分成最多 4 組（每組商品數接近），標籤直接顯示價格範圍。"""
    prices = df["價格"]
    if prices.nunique() < 2:
        df["價格區間"] = f"${prices.iloc[0]:,.0f}"
        return df
    bands = pd.qcut(prices, q=min(4, prices.nunique()), duplicates="drop")
    codes = pd.Series(bands.cat.codes, index=df.index)
    lo = prices.groupby(codes).min()
    hi = prices.groupby(codes).max()
    labels = {c: f"${lo[c]:,.0f} – ${hi[c]:,.0f}" for c in lo.index}
    ordered = [labels[c] for c in sorted(labels)]
    df["價格區間"] = pd.Categorical(codes.map(labels), categories=ordered, ordered=True)
    return df


def prepare(raw: list) -> pd.DataFrame:
    """把原始資料整理成分析用的表格。"""
    df = pd.DataFrame(raw).reindex(columns=["Id", "name", "price", "describe"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])
    df = df.drop_duplicates(subset="Id").reset_index(drop=True)
    out = pd.DataFrame(
        {
            "排序": range(1, len(df) + 1),
            "商品名稱": df["name"].astype(str),
            "品牌": df["describe"].map(extract_brand),
            "價格": df["price"].astype(int),
        }
    )
    return add_price_band(out)


# ----------------------------------------------------------------------
# 圖表（Plotly）
# ----------------------------------------------------------------------
def _style(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=20)),
        font=dict(family=CHART_FONT, size=14),
        margin=dict(l=20, r=20, t=70, b=40),
        height=520,
    )
    return fig


def line_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    mean = df["價格"].mean()
    fig = go.Figure(
        go.Scatter(
            x=df["排序"],
            y=df["價格"],
            mode="lines+markers",
            line=dict(color=PALETTE[0], width=3),
            marker=dict(size=8),
            customdata=df["商品名稱"].str.slice(0, 30),
            hovertemplate="排序 %{x}<br>價格 $%{y:,}<br>%{customdata}<extra></extra>",
        )
    )
    fig.add_hline(
        y=mean,
        line=dict(color=PALETTE[4], dash="dash"),
        annotation_text=f"平均價 ${mean:,.0f}",
        annotation_position="top right",
    )
    fig.update_xaxes(title="排序（依 PChome 銷量排序，越前面賣越多）")
    fig.update_yaxes(title="價格 (NT$)")
    return _style(fig, f"PChome「{keyword}」售價走勢")


def pie_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    cnt = df.groupby("價格區間", observed=True).size().reset_index(name="商品數")
    fig = px.pie(
        cnt, names="價格區間", values="商品數", hole=0.45,
        color_discrete_sequence=PALETTE,
    )
    fig.update_traces(
        textinfo="label+percent",
        hovertemplate="%{label}<br>%{value} 件 (%{percent})<extra></extra>",
    )
    return _style(fig, f"「{keyword}」價格區間佔比")


def sunburst_chart(df: pd.DataFrame, keyword: str) -> go.Figure:
    data = df.assign(商品數=1, 價格區間=df["價格區間"].astype(str))
    fig = px.sunburst(
        data, path=["價格區間", "品牌"], values="商品數",
        color_discrete_sequence=PALETTE,
    )
    fig.update_traces(
        hovertemplate="%{label}<br>%{value} 件<extra></extra>",
        insidetextorientation="radial",
    )
    return _style(fig, f"「{keyword}」價格區間 → 品牌")


# ----------------------------------------------------------------------
# 頁面外觀
# ----------------------------------------------------------------------
CSS = """
<style>
.block-container {padding-top: 2rem;}
.hero {
    background: linear-gradient(120deg, #6366F1 0%, #06B6D4 100%);
    border-radius: 18px; padding: 28px 34px; color: #fff; margin-bottom: 22px;
    box-shadow: 0 8px 24px rgba(99,102,241,.25);
}
.hero h1 {margin: 0; font-size: 2rem; color: #fff;}
.hero p {margin: 6px 0 0; opacity: .92; font-size: 1.02rem;}
.card {
    background: rgba(128,128,128,.10); border-radius: 14px; padding: 16px 18px;
    border-left: 6px solid var(--accent); height: 100%;
}
.card .label {font-size: .85rem; opacity: .7;}
.card .value {font-size: 1.8rem; font-weight: 700; line-height: 1.3;}
.card .sub {font-size: .78rem; opacity: .65; overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap;}
[data-testid="stSidebar"] .stButton > button {width: 100%; font-weight: 600;}
</style>
"""


def card(label: str, value: str, sub: str, accent: str) -> str:
    return (
        f'<div class="card" style="--accent:{accent}">'
        f'<div class="label">{label}</div><div class="value">{value}</div>'
        f'<div class="sub">{sub}</div></div>'
    )


def show_cards(df: pd.DataFrame) -> None:
    top = df.loc[df["價格"].idxmax()]
    low = df.loc[df["價格"].idxmin()]
    cols = st.columns(4)
    items = [
        ("商品數量", f"{len(df)} 件", f"中位數 ${df['價格'].median():,.0f}", PALETTE[0]),
        ("平均價格", f"${df['價格'].mean():,.0f}", "所有商品平均", PALETTE[1]),
        ("最高價格", f"${top['價格']:,}", top["商品名稱"].replace("<", "＜"), PALETTE[4]),
        ("最低價格", f"${low['價格']:,}", low["商品名稱"].replace("<", "＜"), PALETTE[2]),
    ]
    for col, (label, value, sub, accent) in zip(cols, items):
        col.markdown(card(label, value, sub, accent), unsafe_allow_html=True)


def show_dashboard(keyword: str, df: pd.DataFrame) -> None:
    show_cards(df)
    st.write("")
    tab1, tab2, tab3, tab4 = st.tabs(["📈 折線圖", "🥧 圓餅圖", "🌞 旭日圖", "📋 資料表"])
    with tab1:
        st.plotly_chart(line_chart(df, keyword))
    with tab2:
        st.plotly_chart(pie_chart(df, keyword))
    with tab3:
        st.plotly_chart(sunburst_chart(df, keyword))
        st.caption("品牌是從商品描述自動判斷的，抓不到的會歸在「其他」，僅供參考。")
    with tab4:
        shown = df.assign(價格區間=df["價格區間"].astype(str))
        st.dataframe(shown, hide_index=True)
        st.download_button(
            "⬇️ 下載 CSV",
            shown.to_csv(index=False).encode("utf-8-sig"),  # utf-8-sig：Excel 開啟中文不會亂碼
            file_name=f"pchome_{keyword}.csv",
            mime="text/csv",
        )


# ----------------------------------------------------------------------
# 主程式
# ----------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="PChome 價格分析", page_icon="🛍️", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>🛍️ PChome 商品價格分析</h1>'
        "<p>輸入商品與頁數，立刻看到價格走勢、區間佔比與品牌分布</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("🔎 查詢條件")
        keyword = st.text_input("商品關鍵字", value="螢幕掛燈")
        pages = st.slider("爬取頁數（每頁約 20 件）", min_value=1, max_value=10, value=2)
        search = st.button("開始查詢", type="primary")
        st.caption(f"每頁間隔 {REQUEST_DELAY} 秒，避免被網站封鎖。")

    if search:
        kw = keyword.strip()
        if not kw:
            st.warning("請先輸入商品關鍵字。")
        else:
            try:
                raw = collect(kw, pages)
            except (requests.RequestException, ValueError) as e:
                st.error(f"連線或資料解析失敗，請稍後再試。（{e}）")
            else:
                df = prepare(raw) if raw else pd.DataFrame()
                if df.empty:
                    st.session_state.pop("result", None)
                    st.warning(f"找不到「{kw}」的商品，換個關鍵字試試。")
                else:
                    st.session_state["result"] = (kw, df)

    result = st.session_state.get("result")
    if result is None:
        st.info("👈 在左側輸入商品關鍵字和頁數，按「開始查詢」。")
        return
    show_dashboard(*result)


if __name__ == "__main__":
    main()
