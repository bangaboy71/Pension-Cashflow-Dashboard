import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.graph_objects as go

# 1. 페이지 설정 및 UI 스타일 (가족 관제탑 스타일 이식)
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: bold !important; }
    .market-box { text-align: center; padding: 15px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); }
    .market-title { color: #aaa; font-size: 0.85rem; font-weight: bold; margin-bottom: 5px; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 숫자형 데이터 정제
    numeric_cols = ['투자원금', '현재 가치', '목표인출액']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링 (공적연금 제외 340만 목표)
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# ---------------------------------------------------------
# 3. [가족 관제탑 엔진] 시장 지표 크롤링 함수
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_status():
    data = {
        "KOSPI": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "KOSDAQ": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원", "color": "#ffffff"},
        "VOLUME": {"val": "-", "pct": "천주", "color": "#ffffff"}
    }
    header = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
    try:
        # 1. 코스피/코스닥 수집
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            now_el = soup.select_one("#now_value")
            if now_el: data[code]["val"] = now_el.get_text(strip=True)
            
            diff_el = soup.select_one("#change_value_and_rate")
            if diff_el:
                raw_txt = diff_el.get_text(" ", strip=True)
                for word in ["상승", "하락", "보합"]: raw_txt = raw_txt.replace(word, "")
                if "+" in raw_txt: data[code]["color"] = "#FF4B4B" # 빨강
                elif "-" in raw_txt: data[code]["color"] = "#87CEEB" # 파랑
                data[code]["pct"] = raw_txt.strip()

            if code == "KOSPI":
                vol_el = soup.select_one("#quant")
                if vol_el: 
                    data["VOLUME"]["val"] = vol_el.get_text(strip=True)
                    data["VOLUME"]["pct"] = "천주"

        # 2. 환율 수집
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value")
        if ex_val:
            data["USD/KRW"]["val"] = ex_val.get_text(strip=True)
            ex_change = ex_soup.select_one("span.change").get_text(strip=True)
            ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
            if "상승" in ex_blind: data["USD/KRW"]["color"], sign = "#FF4B4B", "+"
            elif "하락" in ex_blind: data["USD/KRW"]["color"], sign = "#87CEEB", "-"
            else: data["USD/KRW"]["color"], sign = "#ffffff", ""
            data["USD/KRW"]["pct"] = f"{sign}{ex_change}원"
    except: pass
    return data

# ---------------------------------------------------------
# 4. 화면 구성 (HUD 렌더링)
# ---------------------------------------------------------
# 중앙 제목
st.markdown("<h2 style='text-align: center; color: #87CEEB;'>🛡️ 현금흐름 통합 관제탑</h2>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #aaa;'>자산 목표: <b>월 {TARGET_PRIVATE/10000:,.0f}만 원</b> (공적연금 제외)</p>", unsafe_allow_html=True)

# [가족 관제탑 방식] HUD 렌더링
m_status = get_market_status()
hud_cols = st.columns(4)
titles = ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
keys = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"]

for i, col in enumerate(hud_cols):
    with col:
        d = m_status[keys[i]]
        # 환율 색상 반전 (하락 시 긍정 파랑)
        display_color = d['color']
        if keys[i] == "USD/KRW" and "-" in d['pct']: display_color = "#87CEEB" 
        
        border = f"{display_color}44" if keys[i] != "VOLUME" else "rgba(255,255,255,0.1)"
        
        st.markdown(f"""
            <div class="market-box" style="border: 1px solid {border};">
                <div class="market-title">{titles[i]}</div>
                <div style="color: {display_color}; font-size: 1.8rem; font-weight: bold;">{d['val']}</div>
                <div style="color: {display_color if keys[i] != 'VOLUME' else '#aaa'}; font-size: 1.0rem; margin-top: 5px;">
                    {d['pct']}
                </div>
            </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# 자산 KPI 리포트
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 예상 수입", f"{current_total:,.0f}원")
k2.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
k3.metric("자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

st.markdown("---")

# 5. 시각화 탭
t1, t2, t3, t4 = st.tabs(["📊 자산 구조", "🌊 수입 폭포", "📅 입금 일정", "🛡️ 세금 보안"])

with t1:
    fig_sun = px.sunburst(private_assets, path=['계좌 유형', '투자성격', '종목명'], values='투자원금',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'},
                          template="plotly_white")
    st.plotly_chart(fig_sun, use_container_width=True)

with t2:
    fig_water = go.Figure(go.Waterfall(
        measure = ["relative"] * len(private_assets) + ["total"],
        x = list(private_assets['종목명']) + ["현재 총 수입"],
        y = list(private_assets['목표인출액']) + [0],
        text = [f"{v/10000:,.1f}만" for v in private_assets['목표인출액']] + [f"{current_total/10000:,.1f}만"],
        connector = {"line":{"color":"#ddd"}},
    ))
    fig_water.update_layout(title="340만 원 목표 달성 엔진 (사적 자산)", template="plotly_white")
    st.plotly_chart(fig_water, use_container_width=True)

with t3:
    sched_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명',
                     color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(xaxis_type='category', title="월간 현금 유입 일정")
    st.plotly_chart(fig_bar, use_container_width=True)

with t4:
    tax_df = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
    fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.5,
                     color_discrete_sequence=px.colors.sequential.RdBu)
    st.plotly_chart(fig_tax, use_container_width=True)
