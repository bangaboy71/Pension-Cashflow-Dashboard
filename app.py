import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf
from datetime import datetime

# 1. 페이지 설정 및 UI 스타일
st.set_page_config(page_title="현금흐름 방어 관제탑", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.6rem !important; font-weight: bold !important; }
    .market-box { text-align: center; padding: 12px; border-radius: 12px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); }
    .market-title { color: #aaa; font-size: 0.8rem; font-weight: bold; margin-bottom: 5px; }
    .status-card { padding: 20px; border-radius: 12px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); height: 100%; }
    .section-title { font-size: 1.0rem; font-weight: bold; color: #87CEEB; margin-bottom: 15px; border-left: 4px solid #87CEEB; padding-left: 10px; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 정제 (신뢰성 있는 데이터 처리)
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 숫자형 데이터 세척
    numeric_cols = ['투자원금', '수량', '매입단가', '목표가', '주당 월분배금']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '').str.replace('원', ''), errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.stop()

# 인출 목표 및 사적 자산 필터링
WITHDRAWAL_TARGETS = {"IRP": 2900000, "ISA": 400000, "일반": 100000}
TOTAL_WITHDRAWAL = sum(WITHDRAWAL_TARGETS.values())
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
private_assets['종목명'] = private_assets['종목명'].str.strip()

# ---------------------------------------------------------
# 3. 사이드바 시뮬레이션 세팅 (상한 500원 / 1원 단위)
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ 분배금 시뮬레이션")
    st.info("주당 월분배금(원)을 1원 단위로 정밀 조절합니다.")
    sim_dist = {}
    for _, row in private_assets.iterrows():
        name = row['종목명']
        default_dist = float(row.get('주당 월분배금', 0))
        # [수정] 상한선 500원, 최소 단위 1원 조정
        sim_dist[name] = st.slider(f"{name} (원/주)", 0.0, 500.0, default_dist, 1.0)
    
    if st.button("🔄 설정 초기화"):
        st.rerun()

# ---------------------------------------------------------
# 4. 실시간 데이터 엔진 (시장 지표 및 주가)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_status():
    data = {"KOSPI": {"val": "-", "delta": "0.00", "color": "#ffffff"}, "KOSDAQ": {"val": "-", "delta": "0.00", "color": "#ffffff"}, "USD/KRW": {"val": "-", "delta": "0원", "color": "#ffffff"}, "VOLUME": {"val": "-", "delta": "천주", "color": "#ffffff"}}
    header = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
    try:
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5); res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            data[code]["val"] = soup.select_one("#now_value").get_text(strip=True)
            diff_raw = soup.select_one("#change_value_and_rate").get_text(" ", strip=True)
            for w in ["상승", "하락", "보합"]: diff_raw = diff_raw.replace(w, "")
            data[code]["delta"] = diff_raw.strip()
            if "+" in diff_raw: data[code]["color"] = "#FF4B4B"
            elif "-" in diff_raw: data[code]["color"] = "#87CEEB"
            if code == "KOSPI": data["VOLUME"]["val"] = soup.select_one("#quant").get_text(strip=True)
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value").get_text(strip=True)
        ex_change = ex_soup.select_one("span.change").get_text(strip=True)
        ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
        sign = "+" if "상승" in ex_blind else ("-" if "하락" in ex_blind else "")
        data["USD/KRW"]["val"] = ex_val
        data["USD/KRW"]["delta"] = f"{sign}{ex_change}원"
        data["USD/KRW"]["color"] = "#FF4B4B" if sign == "+" else "#87CEEB"
    except: pass
    return data

@st.cache_data(ttl=600)
def fetch_prices(tickers):
    data = yf.download(tickers, period="1y", interval="1d", progress=False)
    return data['Close'] if isinstance(data.columns, pd.MultiIndex) else pd.DataFrame({tickers[0]: data['Close']})

# 데이터 연동 및 계산
tickers = private_assets['종목코드'].unique().tolist()
price_hist = fetch_prices(tickers)
curr_prices = price_hist.iloc[-1]

sim_assets = private_assets.copy()
sim_assets['현재가'] = sim_assets['종목코드'].map(curr_prices)
sim_assets['현재가치'] = sim_assets['현재가'] * sim_assets['수량']

# [월 단위 로직] 예상수입 = 주당 월분배금 * 수량
sim_assets['예상수입'] = sim_assets.apply(
    lambda x: (sim_dist[x['종목명']] * x['수량']), axis=1
)

summary = sim_assets.groupby('계좌 유형').agg({'예상수입':'sum', '현재가치':'sum', '투자원금':'sum'}).reset_index()
summary['인출목표'] = summary['계좌 유형'].map(WITHDRAWAL_TARGETS)
summary['원금 손익'] = summary['예상수입'] - summary['인출목표']

# ---------------------------------------------------------
# 5. 메인 레이아웃 및 KPI
# ---------------------------------------------------------
st.markdown("<h3 style='text-align: center; color: #87CEEB; margin-bottom: 20px;'>🛡️ 실시간 현금흐름 방어 관제탑</h3>", unsafe_allow_html=True)

# 시장 HUD
m_data = get_market_status()
h1, h2, h3, h4 = st.columns(4)
keys, titles = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"], ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
for i, col in enumerate([h1, h2, h3, h4]):
    d = m_data[keys[i]]
    col.markdown(f"""<div class="market-box" style="border: 1px solid {d['color']}44;"><div class="market-title">{titles[i]}</div><div style="color: {d['color']}; font-size: 1.6rem; font-weight: bold;">{d['val']}</div><div style="color: {d['color']}; font-size: 0.9rem;">{d['delta']}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# 핵심 KPI
k1, k2, k3, k4 = st.columns(4)
total_inc = summary['예상수입'].sum()
total_profit_loss = summary['원금 손익'].sum()
total_current_val = summary['현재가치'].sum()
total_principal = sim_assets['투자원금'].sum()
total_variance = total_current_val - total_principal

k1.metric("월 예상 총 수입", f"{total_inc:,.0f}원", delta=f"{total_inc - TOTAL_WITHDRAWAL:,.0f}원")
k2.metric("월 원금 손익", f"{total_profit_loss:+,.0f}원", delta_color="normal" if total_profit_loss >= 0 else "inverse")
k3.metric("실시간 자산가치", f"{total_current_val:,.0f}원", delta=f"원금: {total_principal:,.0f}원", delta_color="off")
k4.metric("자산 증감액", f"{total_variance:+,.0f}원", delta=f"{(total_variance/total_principal*100):+.2f}%" if total_principal > 0 else "0.00%")

st.markdown("---")

# 6. 현금흐름 분석 테이블
c_left, c_right = st.columns([3, 2])
with c_left:
    st.markdown("<div class='section-title'>📊 계좌별 현금흐름 방어 현황</div>", unsafe_allow_html=True)
    def style_summary(val, col):
        if col == '인출목표': return 'color: #00FF00'
        if col == '예상수입': return 'color: #87CEEB'
        if col == '원금 손익': return 'color: #87CEEB' if val >= 0 else 'color: #FF4B4B'
        return ''

    st.dataframe(
        summary[['계좌 유형', '인출목표', '예상수입', '원금 손익']].style.apply(
            lambda x: [style_summary(v, x.name) for v in x], axis=0
        ).format({'인출목표': '{:,.0f}원', '예상수입': '{:,.0f}원', '원금 손익': '{:+,.0f}원'}),
        use_container_width=True, hide_index=True
    )

with c_right:
    st.markdown("<div class='section-title'>🌊 수입 vs 인출목표 비중</div>", unsafe_allow_html=True)
    pie_clr = '#00FF00' if total_profit_loss >= 0 else '#FF4B4B'
    fig_pie = go.Figure(data=[go.Pie(labels=['실제수입', '손익차액'], values=[total_inc, abs(total_profit_loss)], hole=.5, marker_colors=['#87CEEB', pie_clr])])
    fig_pie.update_layout(height=230, margin=dict(l=0,r=0,t=0,b=0))
    st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("---")

# 7. 종목별 딥다이브 (탭 처리)
st.markdown("<div class='section-title'>🔍 종목별 딥다이브 관제</div>", unsafe_allow_html=True)
tabs = st.tabs(sim_assets['종목명'].tolist())
for i, tab in enumerate(tabs):
    with tab:
        row = sim_assets.iloc[i]
        t_code = row['종목코드']
        col_chart, col_risk = st.columns([2, 1])
        with col_chart:
            period = st.radio("기간", ["3M", "6M", "1Y"], horizontal=True, key=f"btn_{t_code}")
            days = {"3M": 90, "6M": 180, "1Y": 365}[period]
            fig_l = px.line(price_hist[t_code].tail(days), title=f"{row['종목명']} ({period})")
            fig_l.update_layout(height=300, template="plotly_white")
            st.plotly_chart(fig_l, use_container_width=True)
        with col_risk:
            curr_p, buy_p, target_p = row['현재가'], row['매입단가'], row['목표가']
            sig, clr = ("🚨 원금방어 경보", "#FF4B4B") if curr_p < buy_p * 0.9 else (("💰 익절 검토", "#87CEEB") if curr_p >= target_p > 0 else ("✅ 정상 보유", "#aaa"))
            st.markdown(f"""<div class="status-card" style="border: 1px solid {clr}66;"><div style="font-size: 0.8rem; color: #888;">{row['종목명']}</div><div style="font-size: 1.2rem; font-weight: bold; color: {clr}; margin: 10px 0;">{sig}</div><hr style="border: 0.1px solid #333;"><div style="display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: 5px;"><span>현재가</span><span style="color: #FFD700;">{curr_p:,.0f}원</span></div><div style="display: flex; justify-content: space-between; font-size: 0.9rem;"><span>수익률</span><span style="color: {clr};">{((curr_p/buy_p-1)*100):+.2f}%</span></div><div style="margin-top: 10px; font-size: 0.75rem; color: #666; border-top: 1px solid #333; padding-top: 10px;">월 예상 기여: {row['예상수입']:,.0f}원</div></div>""", unsafe_allow_html=True)

st.markdown("---")

# 8. 하단 통합 시각화
col_a, col_b, col_c = st.columns(3)
with col_a:
    fig_sun = px.sunburst(sim_assets, path=['계좌 유형', '투자성격', '종목명'], values='현재가치', color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'}, template="plotly_white")
    st.plotly_chart(fig_sun, use_container_width=True)
with col_b:
    fig_water = go.Figure(go.Waterfall(measure=["relative"]*len(sim_assets)+["total"], x=list(sim_assets['종목명'])+["총 수입"], y=list(sim_assets['예상수입'])+[0], text=[f"{v/10000:,.0f}만" for v in sim_assets['예상수입']]+[f"{total_inc/10000:,.0f}만"], connector={"line":{"color":"#ddd"}}))
    st.plotly_chart(fig_water, use_container_width=True)
with col_c:
    sched_df = sim_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='예상수입', color='계좌 유형', text='종목명', color_discrete_sequence=px.colors.qualitative.Safe)
    st.plotly_chart(fig_bar, use_container_width=True)
