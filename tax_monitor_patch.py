"""
tax_monitor_patch.py
====================
pension_app.py 에 과세 모니터 탭을 추가하기 위한 패치 코드.

◆ 변경 위치 1곳 + 추가 위치 2곳 (총 3곳)
"""

# ════════════════════════════════════════════════════════
# [변경 1] 탭 정의 줄 — _main_tab8 추가
# ════════════════════════════════════════════════════════
#
# 기존 (pension_app.py 약 2966번째 줄):
# _main_tab1, ..., _main_tab7 = st.tabs([
#     ..., "🤖 AI 자문",
# ])
#
# 변경 후:
# _main_tab1, ..., _main_tab7, _main_tab8 = st.tabs([
#     ..., "🤖 AI 자문", "🏦 과세관리",
# ])


# ════════════════════════════════════════════════════════
# [추가 1] 데이터 로드 섹션 (load_sheet 호출 부분 근처, 약 1950번째 줄 이후)
# ════════════════════════════════════════════════════════
#
# 아래 코드를 hh_df 로드 직후에 삽입하세요:

LOAD_DIST_TAX = """
# ── 분배금과세 시트 로드 ──────────────────────────────
@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def _load_dist_tax(url: str, gid: str) -> pd.DataFrame:
    from pension_tax_monitor import load_dist_tax_sheet
    return load_dist_tax_sheet(url, gid)

_dist_tax_gid = st.secrets.get("DIST_TAX_SHEET_GID", "")
dist_tax_df   = _load_dist_tax(SHEET_URL, _dist_tax_gid)
"""


# ════════════════════════════════════════════════════════
# [추가 2] 파일 맨 끝 — with _main_tab8 블록
# ════════════════════════════════════════════════════════

ADD_TO_END = """
# ════════════════════════════════════════════════════════
# 🏦 과세 관리 탭
# ════════════════════════════════════════════════════════
with _main_tab8:
    from pension_tax_monitor import render_tax_monitor_tab

    _tax_ctx = {
        "dist_df":        dist_tax_df,          # 분배금과세 시트 (없으면 추산 모드)
        "year":           datetime.now().year,
        "current_month":  datetime.now().month,
        "irp_monthly":    irp_income,
        "isa_monthly":    isa_income,
        "gen_monthly":    _gen_monthly_income,
        "ps_monthly":     ps_income,
        "target_monthly": target_monthly,
    }

    render_tax_monitor_tab(_tax_ctx)
"""
