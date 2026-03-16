import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 1. 페이지 설정
st.set_page_config(page_title="사적연금 340만 원 관제탑", layout="wide")

# 2. 데이터 로드 및 '무적' 정제 작업
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # ttl=0을 설정하여 항상 최신 시트 데이터를 가져오도록 합니다.
    df = conn.read(ttl=0)
    
    # [핵심 해결책] 컬럼명 정제: 앞뒤 공백 제거 및 숨겨진 유령 문자(BOM) 제거
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 만약 '계좌 유형' 컬럼을 여전히 못 찾는다면 첫 번째 컬럼을 강제로 지정
    if '계좌 유형' not in df.columns:
        df.rename(columns={df.columns[0]: '계좌 유형'}, inplace=True)

    # 숫자 데이터 정제 (쉼표 제거 및 변환)
    cols_to_fix = ['투자원금', '현재 가치', '목표인출액']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.info("구글 시트의 첫 번째 줄(제목)이 정상적인지 확인해 주세요.")
    st.stop()

# 3. 사적 자산 필터링 (공적연금 및 설정 행 제외)
# '계좌 유형'이 IRP, ISA, 일반인 데이터만 모읍니다.
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()

# 4. 목표 설정 (사적 자산 전용 340만 원)
TARGET_PRIVATE = 3400000
current_private_total = private_assets['목표인출액'].sum()
achievement = (current_private_total / TARGET_PRIVATE) * 100

# 5. 메인 화면 출력
st.title("🛡️ 사적 자산 현금흐름 관제탑")
st.subheader(f"사적 자산 목표: 월 {TARGET_PRIVATE/10000:,.0f}만 원")

m1, m2, m3 = st.columns(3)
m1.metric("현재 사적 수입", f"{current_private_total:,.0f}원")
m2.metric("사적 목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
m3.metric("사적 자산 규모", f"{private_assets['현재 가치'].sum():,.0f}원")

st.markdown("---")

# 6. 시각화 (340만 원 기준)
c1, c2 = st.columns(2)
with c1:
    st.subheader("종목별 기여도")
    fig_pie = px.pie(private_assets, values='목표인출액', names='종목명', 
                     hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
    st.plotly_chart(fig_pie, use_container_width=True)

with c2:
    st.subheader("목표 달성 현황")
    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(x=['현재 사적 수입'], y=[current_private_total], name='현재', marker_color='#1E88E5'))
    fig_bar.add_trace(go.Bar(x=['사적 목표 (340만)'], y=[TARGET_PRIVATE], name='목표', marker_color='#E53935'))
    st.plotly_chart(fig_bar, use_container_width=True)

