# -*- coding: utf-8 -*-
"""이의제기 검증용 데모: 프롬프트/LLM 출력이 흔들려도 최종 출력이 일정한지 보여준다.
실행: python3 -m tests.demo_stability
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import OUTPUT_KEYS  # noqa: E402
from src.classifier import Classifier  # noqa: E402
from src.goal_pose_resolver import NullGoalPoseResolver  # noqa: E402
from src.llama_runner import MockLlamaRunner, ScriptedRunner  # noqa: E402
from src.mission_service import MissionService  # noqa: E402


def svc(runner):
    return MissionService(classifier=Classifier(runner), resolver=NullGoalPoseResolver())


print("=" * 80)
print("A. 같은 의도인데 LLM이 프롬프트마다 제각각으로 출력 → 최종은 일정한가?")
print("=" * 80)
raw_variants = {
    "1 깨끗한 JSON           ": '{"mission_name":"open_door"}',
    "2 엉뚱한 action/태그 추가": '{"mission_name":"open_door","action":"unlock","target_refs":["gate"],"goal_pose":{"x":9}}',
    "3 마크다운+수다         ": 'Sure! ```json\n{"mission_name": "open_door"}\n``` 도움이 됐길!',
    "4 JSON아님, 토큰만 존재  ": 'The correct mission is open_door.',
    "5 순수 문장(토큰 없음)   ": '문을 여는 미션인 것 같습니다.',
    "6 빈 응답               ": '',
}
for label, raw in raw_variants.items():
    out = svc(ScriptedRunner(raw)).handle("문 열어")
    keys_ok = set(out.keys()) == set(OUTPUT_KEYS)
    code = out["error"]["code"] if out["error"] else "-"
    print(f"[{label}] keys동일={keys_ok} | status={out['status']:7} | "
          f"mission={str(out['mission_name']):11} | action={str(out['action']):7} | err={code}")

print()
print("=" * 80)
print("B. 사용자 명령 표현이 미세하게 달라도(동의어) 같은 결과인가? (mock)")
print("=" * 80)
for cmd in ["문을 열어.", "출입문 개방해.", "도어를 열어줘.", "문 좀 밀어서 열어줘"]:
    out = svc(MockLlamaRunner()).handle(cmd)
    keys_ok = list(out.keys()) == OUTPUT_KEYS
    print(f"입력={cmd:18} → mission={str(out['mission_name']):11} "
          f"action={str(out['action']):7} keys동일={keys_ok}")

print()
print("=" * 80)
print("C. 최종 출력의 key 집합은 성공/실패 무관하게 항상 동일한가?")
print("=" * 80)
print("계약 key:", OUTPUT_KEYS)
