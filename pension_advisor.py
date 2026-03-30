"""
pension_advisor.py — 연금 AI 자문 모듈
======================================
메인 pension_app.py 에서 import 하여 사용합니다.

사용 예시 (pension_app.py 내):
    _main_tab1, ..., _main_tab7 = st.tabs([..., "🤖 AI 자문"])
    with _main_tab7:
        from pension_advisor import render_advisor_tab
        render_advisor_tab(advisor_context)

필요 패키지: anthropic (pip install anthropic)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import streamlit as st

# ── Anthropic SDK ────────────────────────────────────────
try:
    import anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False


# ════════════════════════════════════════════════════════
# 컨텍스트 빌더: 대시보드 현재 상태 → 텍스트 요약
# ════════════════════════════════════════════════════════
def build_context_summary(ctx: dict[str, Any]) -> str:
    """대시보드 수치를 AI 프롬프트용 텍스트로 변환."""
    now_age = datetime.now().year - ctx.get("birth_year", 1971)
    lines = [
        f"[현재 상황 요약 — {datetime.now().strftime('%Y년 %m월')} 기준]",
        f"• 나이: {now_age}세",
        f"• 은퇴 예정: {ctx.get('retire_age', 55)}세 ({ctx.get('birth_year',1971)+ctx.get('retire_age',55)}년)",
        f"• 공무원연금 개시 예정: {ctx.get('pension_age', 65)}세",
        "",
        "[월 수입 현황 (세후)]",
        f"• 공무원연금: {ctx.get('public_pension', 0):,.0f}원/월",
        f"• IRP 분배금: {ctx.get('irp_income', 0):,.0f}원/월",
        f"• ISA 분배금: {ctx.get('isa_income', 0):,.0f}원/월",
        f"• 연금저축 분배금: {ctx.get('ps_income', 0):,.0f}원/월",
        f"• 총 수입(세후): {ctx.get('display_income', 0):,.0f}원/월",
        f"• 목표 생활비: {ctx.get('target_monthly', 0):,.0f}원/월",
        f"• 목표 달성률: {ctx.get('achievement', 0):.1f}%",
        "",
        "[자산 현황]",
        f"• IRP 평가액: {ctx.get('irp_total', 0):,.0f}원",
        f"• ISA 평가액: {ctx.get('isa_total', 0):,.0f}원",
        f"• 연금저축 평가액: {ctx.get('ps_total', 0):,.0f}원",
        f"• 일반계좌 평가액: {ctx.get('general_total', 0):,.0f}원",
        f"• 총 금융자산: {ctx.get('irp_total',0)+ctx.get('isa_total',0)+ctx.get('ps_total',0)+ctx.get('general_total',0):,.0f}원",
        "",
        "[세금/공제]",
        f"• 세후 표시 모드: {'켜짐' if ctx.get('show_tax') else '꺼짐'}",
        f"• 건강보험료 적용: {'예' if ctx.get('use_health_ins') else '아니오'}",
        "",
        "[적용 시나리오]",
        f"• 현재 시나리오: {ctx.get('sc_choice', '기본 시트 현황')}",
    ]

    # 타임라인 부족 경고
    shortage = ctx.get("shortage_summary", "")
    if shortage:
        lines += ["", "[타임라인 부족 구간]", shortage]

    # 보유 종목 요약
    irp_names = ctx.get("irp_names", [])
    isa_names = ctx.get("isa_names", [])
    ps_names  = ctx.get("ps_names", [])
    gen_names = ctx.get("gen_names", [])
    if any([irp_names, isa_names, ps_names, gen_names]):
        lines.append("")
        lines.append("[보유 종목 요약]")
        if irp_names:
            lines.append(f"• IRP: {', '.join(irp_names)}")
        if isa_names:
            lines.append(f"• ISA: {', '.join(isa_names)}")
        if ps_names:
            lines.append(f"• 연금저축: {', '.join(ps_names)}")
        if gen_names:
            lines.append(f"• 일반계좌: {', '.join(gen_names)}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════
# 시스템 프롬프트
# ════════════════════════════════════════════════════════
SYSTEM_PROMPT = """당신은 대한민국 공무원연금 수급자를 위한 은퇴 재무 전문 AI 어드바이저입니다.
사용자는 공무원연금 + IRP + ISA + 연금저축으로 구성된 연금 포트폴리오를 운용 중이며,
실시간 대시보드를 통해 현금흐름을 관리하고 있습니다.

당신의 역할:
1. 대시보드 수치를 기반으로 현재 재무 상태를 진단
2. 목표 생활비 달성을 위한 구체적인 실행 방안 제시
3. 세금 효율적인 인출 전략 조언 (IRP·ISA·연금저축 특성 반영)
4. 리스크 요소 식별 및 완화 방안 제안
5. 사용자 질문에 명확하고 실용적으로 답변

답변 원칙:
- 항상 대시보드 실제 수치를 인용하여 구체적으로 답변
- 감정적 위로보다 데이터 기반의 냉철한 진단 우선
- 제안은 실행 가능한 행동 단계(Action Step)로 마무리
- 불확실한 세법·규정은 "전문가 확인 필요"로 명시
- 한국어로 답변, 금액은 원/만원 단위 명확히 표기
- 응답은 마크다운 형식으로 구조화 (헤더, 목록 활용)"""


# ════════════════════════════════════════════════════════
# 빠른 질문 템플릿
# ════════════════════════════════════════════════════════
QUICK_QUESTIONS = [
    ("📊 현황 진단", "현재 연금 포트폴리오의 전반적인 재무 건전성을 진단해 주세요. 강점과 취약점, 가장 시급한 개선 과제를 알려주세요."),
    ("💰 목표 달성 전략", "목표 생활비를 안정적으로 달성하기 위한 최적 인출 전략을 제안해 주세요. 각 계좌별 인출 순서와 금액 배분을 포함해 주세요."),
    ("🏦 세금 최적화", "현재 포트폴리오 기준으로 세금 부담을 최소화하는 방법을 알려주세요. IRP, ISA, 연금저축의 세금 특성을 활용한 전략이 궁금합니다."),
    ("⚠️ 리스크 점검", "현재 포트폴리오에서 가장 큰 리스크 요인은 무엇인가요? 특히 IRP/ISA 고갈 리스크와 대응 방안을 알려주세요."),
    ("📈 수익률 개선", "현재 포트폴리오의 수익률을 높이면서 분배금 안정성을 유지하는 리밸런싱 방향을 제안해 주세요."),
    ("🔢 추가 납입 효과", "IRP 또는 연금저축에 추가 납입할 경우 세액공제 혜택과 장기 복리 효과를 계산해 주세요."),
]


# ════════════════════════════════════════════════════════
# Claude API 호출
# ════════════════════════════════════════════════════════
def _call_claude(
    messages: list[dict],
    context_summary: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 2048,
) -> str:
    """Anthropic API 호출 → 스트리밍 응답 반환."""
    client = anthropic.Anthropic(api_key=api_key)

    # 첫 번째 user 메시지에 컨텍스트 삽입
    enriched = list(messages)
    if enriched and enriched[0]["role"] == "user":
        enriched[0] = {
            "role": "user",
            "content": (
                f"[현재 대시보드 데이터]\n{context_summary}\n\n"
                f"---\n\n{enriched[0]['content']}"
            ),
        }

    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=enriched,
    ) as stream:
        return stream.get_final_text()


# ════════════════════════════════════════════════════════
# 메인 탭 렌더러
# ════════════════════════════════════════════════════════
def render_advisor_tab(ctx: dict[str, Any]) -> None:
    """AI 자문 탭 전체 렌더링."""

    if not _ANTHROPIC_OK:
        st.error(
            "❌ `anthropic` 패키지가 설치되지 않았습니다.\n\n"
            "```bash\npip install anthropic\n```"
        )
        return

    # ── API 키 확인 ──────────────────────────────────────
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        st.warning(
            "⚠️ **Anthropic API 키가 설정되지 않았습니다.**\n\n"
            "Streamlit Cloud → Manage app → Settings → Secrets 에 추가:\n"
            "```\nANTHROPIC_API_KEY = \"sk-ant-...\"\n```"
        )
        with st.expander("🔑 로컬 테스트용 임시 키 입력"):
            api_key = st.text_input(
                "API 키 (테스트용, 저장 안 됨)",
                type="password",
                key="advisor_tmp_key",
            )
        if not api_key:
            return

    context_summary = build_context_summary(ctx)

    # ── 헤더 ────────────────────────────────────────────
    st.markdown(
        "<h3 style='margin-bottom:0.2rem;'>🤖 AI 연금 자문</h3>"
        "<p style='color:rgba(255,255,255,0.5); font-size:0.85rem; margin-top:0;'>"
        "현재 대시보드 데이터를 바탕으로 맞춤형 재무 조언을 받아보세요.</p>",
        unsafe_allow_html=True,
    )

    # ── 현재 상태 요약 카드 ──────────────────────────────
    achievement = ctx.get("achievement", 0)
    display_income = ctx.get("display_income", 0)
    target_monthly = ctx.get("target_monthly", 0)
    gap = display_income - target_monthly

    status_color = "#7dffb0" if achievement >= 100 else ("#FFD700" if achievement >= 80 else "#FF4B4B")
    status_emoji = "✅" if achievement >= 100 else ("🟡" if achievement >= 80 else "⚠️")

    st.markdown(
        f"""<div style='background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1);
        border-left:4px solid {status_color}; border-radius:10px; padding:12px 16px;
        margin-bottom:16px;'>
        <div style='font-size:0.82rem; color:rgba(255,255,255,0.5); margin-bottom:4px;'>
        📌 현재 자문 기준 데이터</div>
        <div style='display:flex; gap:24px; flex-wrap:wrap;'>
          <span>{status_emoji} 목표 달성률 <b style='color:{status_color};'>{achievement:.1f}%</b></span>
          <span>💰 월 수입(세후) <b>{display_income:,.0f}원</b></span>
          <span>🎯 목표 생활비 <b>{target_monthly:,.0f}원</b></span>
          <span>{'🟢' if gap >= 0 else '🔴'} 잉여/부족 <b style='color:{status_color};'>{gap:+,.0f}원</b></span>
        </div></div>""",
        unsafe_allow_html=True,
    )

    # ── 세션 상태 초기화 ─────────────────────────────────
    if "advisor_messages" not in st.session_state:
        st.session_state.advisor_messages = []
    if "advisor_context_snap" not in st.session_state:
        st.session_state.advisor_context_snap = ""

    # ── 레이아웃: 좌(빠른질문+채팅) / 우(컨텍스트) ─────────
    chat_col, ctx_col = st.columns([3, 1])

    # ── 오른쪽: 컨텍스트 뷰어 ──────────────────────────────
    with ctx_col:
        with st.expander("📋 전달 데이터 확인", expanded=False):
            st.code(context_summary, language="text")
        if st.button("🗑️ 대화 초기화", use_container_width=True, key="advisor_clear"):
            st.session_state.advisor_messages = []
            st.rerun()

    # ── 왼쪽: 채팅 인터페이스 ────────────────────────────
    with chat_col:

        # 빠른 질문 버튼
        st.markdown(
            "<div style='font-size:0.8rem; color:rgba(255,255,255,0.5); "
            "margin-bottom:6px;'>⚡ 빠른 질문</div>",
            unsafe_allow_html=True,
        )
        # 2열로 배치
        q_cols = st.columns(3)
        for i, (label, prompt) in enumerate(QUICK_QUESTIONS):
            if q_cols[i % 3].button(label, key=f"quick_{i}", use_container_width=True):
                st.session_state.advisor_messages.append(
                    {"role": "user", "content": prompt}
                )
                with st.spinner("분석 중..."):
                    try:
                        reply = _call_claude(
                            st.session_state.advisor_messages,
                            context_summary,
                            api_key,
                        )
                    except Exception as e:
                        reply = f"❌ 오류가 발생했습니다: {e}"
                st.session_state.advisor_messages.append(
                    {"role": "assistant", "content": reply}
                )
                st.rerun()

        st.divider()

        # 채팅 히스토리 렌더링
        chat_container = st.container(height=480)
        with chat_container:
            if not st.session_state.advisor_messages:
                st.markdown(
                    "<div style='text-align:center; padding:60px 20px; "
                    "color:rgba(255,255,255,0.3);'>"
                    "💬 위의 빠른 질문 버튼을 클릭하거나<br>아래 입력창에 질문을 입력하세요.</div>",
                    unsafe_allow_html=True,
                )
            else:
                for msg in st.session_state.advisor_messages:
                    role = msg["role"]
                    content = msg["content"]
                    if role == "user":
                        with st.chat_message("user"):
                            st.markdown(content)
                    else:
                        with st.chat_message("assistant", avatar="🤖"):
                            st.markdown(content)

        # 입력창
        user_input = st.chat_input(
            "연금 전략에 대해 무엇이든 질문하세요...",
            key="advisor_input",
        )
        if user_input:
            st.session_state.advisor_messages.append(
                {"role": "user", "content": user_input}
            )
            with st.spinner("AI 자문 생성 중..."):
                try:
                    reply = _call_claude(
                        st.session_state.advisor_messages,
                        context_summary,
                        api_key,
                    )
                except Exception as e:
                    reply = f"❌ 오류가 발생했습니다: {e}"
            st.session_state.advisor_messages.append(
                {"role": "assistant", "content": reply}
            )
            st.rerun()
