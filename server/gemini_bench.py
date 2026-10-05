r"""Gemini 모델별 속도·인용 확인 비교 (실제 법령 질문, 키 값은 출력하지 않음)
    .venv\Scripts\python.exe gemini_bench.py gemini-flash-latest gemini-3.5-flash-lite ...
"""
import sys, time

import llm
import server as S

QS = ["정부가 보험료를 얼마나 지원하나요?", "보험사업자는 누가 될 수 있어요?", "손해평가인 보수교육은 언제 받아요?"]
k, chars = llm.LIMITS["gemini"]
for model in sys.argv[1:]:
    llm.GEMINI_MODELS = model.split("+")  # 'a+b' 로 주면 자동 전환 순서를 그대로 시험
    times, firsts, ok_n = [], [], 0
    for q in QS:
        with S.db() as c:
            hits = S.search(c, S.pick_versions(c, None), q, k)
        t, first, parts = time.time(), None, []
        try:
            for piece in llm.stream("gemini", S.SYSTEM, S.build_prompt(hits, chars, q), True):
                first = first or time.time() - t
                parts.append(piece)
        except llm.ModelError as e:
            print(f"  {model}: 실패 — {e.user_msg} {getattr(e, 'detail', '')[:150]}")
            break
        times.append(time.time() - t); firsts.append(first or 0)
        data = S.parse_json("".join(parts)) or {}
        ok = any(S.find_quote(str(c.get("quote", "")), h[2]["text"]) for c in data.get("citations", []) for h in hits)
        ok_n += ok
        last_answer = (data.get("answer") or "")[:90]
    if times:
        print(f"{model:28} 첫 글자 평균 {sum(firsts)/len(firsts):4.1f}초 · 전체 평균 {sum(times)/len(times):4.1f}초 · "
              f"인용 확인 {ok_n}/{len(times)} · 생각 끄기 {'미지원' if model in llm._no_thinking_off else '적용'}")
        print(f"{'':28} 예) {last_answer}")
