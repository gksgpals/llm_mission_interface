# -*- coding: utf-8 -*-
"""
서비스 엔드투엔드 테스트 (mock/scripted 백엔드).

사용자가 명시한 로컬 검증 항목을 그대로 커버한다:
  - 동의어 명령이 같은 mission_name 으로 변환되는지
  - LLM 이 잘못된 action 을 내도 고정 action 으로 교정되는지
  - LLM 이 JSON 이 아닌 텍스트를 내도 error JSON 이 나오는지
  - 허용되지 않은 명령에서 error JSON 이 나오는지
  - goal_pose 가 없어도 필드가 null 로 유지되는지 (그리고 주입 시 ok 로 승격)
  - 모든 최종 출력이 JSON 파싱 가능한지
  - 모든 최종 출력 key 가 항상 동일한지
"""
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import OUTPUT_KEYS, ErrorCode  # noqa: E402
from src.classifier import Classifier  # noqa: E402
from src.goal_pose_resolver import NullGoalPoseResolver, StaticGoalPoseResolver  # noqa: E402
from src.llama_runner import MockLlamaRunner, ScriptedRunner  # noqa: E402
from src.mission_service import MissionService  # noqa: E402

_CMD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_commands.json")


def make_service(runner, resolver=None):
    return MissionService(
        classifier=Classifier(runner),
        resolver=resolver or NullGoalPoseResolver(),
    )


class TestSynonymClassification(unittest.TestCase):
    """test_commands.json 의 표현 변형이 기대 mission_name 으로 분류되는지 (mock)."""

    @classmethod
    def setUpClass(cls):
        with open(_CMD_PATH, "r", encoding="utf-8") as fh:
            cls.cases = json.load(fh)["cases"]
        cls.service = make_service(MockLlamaRunner())

    def test_all_cases(self):
        wrong = []
        for case in self.cases:
            out = self.service.handle(case["text"])
            # 계약 불변식: 키 동일 + JSON 직렬화 가능
            self.assertEqual(set(out.keys()), set(OUTPUT_KEYS), case["text"])
            json.dumps(out, ensure_ascii=False)
            if out["mission_name"] != case["expected_mission"]:
                wrong.append((case["text"], case["expected_mission"], out["mission_name"]))
        acc = 1.0 - len(wrong) / len(self.cases)
        self.assertEqual(wrong, [], f"분류 정확도={acc:.1%}, 오분류={wrong}")

    def test_synonyms_map_to_same_mission(self):
        synonyms = ["공기정화 버튼을 눌러.", "환풍 장치를 켜.", "연기 제거 장치를 작동시켜.", "공기를 정화해."]
        names = {self.service.handle(t)["mission_name"] for t in synonyms}
        self.assertEqual(names, {"press_purifier_button"})


class TestContractEnforcement(unittest.TestCase):
    def test_llm_bogus_action_is_overridden(self):
        # LLM 이 mission_name 외에 엉뚱한 action/target_refs 를 넣어도 무시되어야 한다.
        raw = json.dumps({
            "mission_name": "press_purifier_button",
            "action": "turn_on",
            "target_refs": ["ventilator"],
            "goal_pose": {"x": 9, "y": 9, "yaw": 9},
        })
        service = make_service(ScriptedRunner(raw))
        out = service.handle("아무 말")
        self.assertEqual(out["mission_name"], "press_purifier_button")
        self.assertEqual(out["action"], "press")                   # 교정됨
        self.assertEqual(out["target_refs"], ["purifier_button"])  # 교정됨
        self.assertIsNone(out["goal_pose"])                        # LLM 좌표 폐기
        self.assertEqual(out["status"], "partial")

    def test_non_json_output_yields_error(self):
        service = make_service(ScriptedRunner("죄송하지만 도와드릴 수 없습니다."))
        out = service.handle("공기정화 버튼 눌러")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["error"]["code"], ErrorCode.LLM_OUTPUT_UNPARSEABLE)

    def test_explicit_null_mission_yields_unsupported(self):
        service = make_service(ScriptedRunner('{"mission_name": null}'))
        out = service.handle("춤춰봐")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["error"]["code"], ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND)

    def test_unknown_mission_name_yields_unsupported(self):
        service = make_service(ScriptedRunner('{"mission_name": "make_coffee"}'))
        out = service.handle("커피 타줘")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["error"]["code"], ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND)

    def test_llama_call_failure_yields_error(self):
        service = make_service(ScriptedRunner("RAISE"))
        out = service.handle("문 열어")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["error"]["code"], ErrorCode.LLM_CALL_FAILED)

    def test_empty_input_yields_invalid_request(self):
        service = make_service(MockLlamaRunner())
        out = service.handle("   ")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["error"]["code"], ErrorCode.INVALID_REQUEST)

    def test_messy_json_with_prose_still_parses(self):
        raw = 'Sure! Here is the result:\n```json\n{"mission_name": "open_door"}\n```\nThanks.'
        service = make_service(ScriptedRunner(raw))
        out = service.handle("문 열어")
        self.assertEqual(out["mission_name"], "open_door")
        self.assertEqual(out["action"], "open")


class TestGoalPoseInjection(unittest.TestCase):
    def test_no_map_keeps_goal_pose_null_and_partial(self):
        service = make_service(MockLlamaRunner(), resolver=NullGoalPoseResolver())
        out = service.handle("출입문 개방해.")
        self.assertIsNone(out["goal_pose"])
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["error"]["code"], ErrorCode.GOAL_POSE_UNRESOLVED)

    def test_injected_pose_promotes_to_ok(self):
        resolver = StaticGoalPoseResolver({
            "door": {"frame_id": "map", "x": 3.0, "y": 0.4, "yaw": 0.0},
        })
        service = make_service(MockLlamaRunner(), resolver=resolver)
        out = service.handle("출입문 개방해.")
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["error"])
        self.assertEqual(out["goal_pose"]["x"], 3.0)


if __name__ == "__main__":
    unittest.main()
