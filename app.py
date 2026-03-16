import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

# 1. 페이지 설정
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

# 2. 기본값 설정 (에러 방지용)
# 코드가 중간에 멈춰도 NameError가 나지 않도록 초기값을 0으로 잡아둡니다.
public_pension = 0
irp_total = 0
isa_total = 0
target_monthly = 7000000
irp_rate_base = 0.012 # 월 1.2% 기본값

# 3. 구글 시트 연결 및 데이터 로드
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read()
    
    # 컬럼명 정리 (공백 제거)
    df.columns = df.columns.str.strip()
    
    # [유연한 이름 찾기] '항목' 혹은 '자산명' 중 있는 것을 사용합니다.
    name_col = '항목' if '항목' in df.columns else ('자산명' if '자산명' in df.columns else None)
    val_col = '금액' if '금액' in df.columns else ('금액(원)' if '금액(원)' in df.columns else None)
    rate_col = '예상수익률(연%)' if '예상수익률(연%)' in df.columns else ('예상 수익률(연 %)' if '예상 수익률(연 %)' in df.columns else None)

    if name_col and val_col:
        # 금액 데이터 숫자 변환 (쉼표 제거)
        df[val_col] = df[val_col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
        df[val_col] = pd.to_numeric(df[val_col], errors='coerce').fillna(0)

        # 데이터 추출 (포함된 글자로 찾기)
        def get_val(keyword):
            found = df.loc[df[name_col].str.contains(keyword, na=False), val_col]
            return found.values[0] if not found.empty else 0

        public_pension = get_val('연금')
        irp_total = get_val('IRP')
        isa_total = get_val('ISA')
        target_monthly = get_val('목표') if get_val('목표') > 0 else 7000000

        # 수익률 추출
        if rate_col:
            irp_row = df.loc[df[name_col].str.contains('IRP', na=False), rate_col]
            if not irp_row.empty:
                irp_rate_base = float(str(irp_row.values[0]).replace('%','')) / 100 / 12

except Exception as e:
    st.warning(f"데이터 일부 로드 실패: {e}. 기본 수치로 표시합니다.")

# 4. 사이드바 - 수익률 슬라이더
st.sidebar.header("📊 수익률 시뮬레이션")
# 이제 irp_rate_base가 무조건 존재하므로 에러가 나지 않습니다.
irp_rate = st.sidebar.slider("IRP 월 분배율 (%)", 0.5, 2.5, float(irp_rate_base*100), step=0.1) / 100
isa_rate = st.sidebar.slider("ISA 월 분배율 (%)", 0.1, 2.0, 0.8, step=0.1) / 100

# 5. 계산 및 시각화
irp_income = irp_total * irp_rate
isa_income = isa_total * isa_rate
total_income = public_pension + irp_income + isa_income
achievement = (total_income / target_monthly) * 100

st.title("💰 연금자산 현금흐름 관제탑")
st.metric("현재 예상 월 수입", f"{total_income:,.0f}원", f"목표 대비 {achievement:.1f}%")

fig = px.pie(values=[public_pension, irp_income, isa_income], 
             names=["공적연금", "IRP수익", "ISA수익"], hole=0.4, title="현금흐름 비중")
st.plotly_chart(fig)
