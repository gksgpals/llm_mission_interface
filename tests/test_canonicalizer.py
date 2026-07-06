# -*- coding: utf-8 -*-
"""canonicalizer 단위 테스트: 계약(action/target_refs 고정, 키 불변) 검증."""
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import MISSION_SPEC, OUTPUT_KEYS, ErrorCode  # noqa: E402
from src.canonicalizer import (  # noqa: E402
    canonicalize,
    make_error,
    normalize_goal_pose,
)


class TestCanonicalizer(unittest.TestCase):
    def test_supported_mission_fixes_action_and_targets(self):
        out = canonicalize("press_purifier_button")
        self.assertEqual(out["mission_name"], "press_purifier_button")
        self.assertEqual(out["action"], "press")                      # 고정값
        self.assertEqual(out["target_refs"], ["purifier_button"])     # 고정값
        self.assertEqual(out["status"], "partial")
        self.assertIsNone(out["goal_pose"])
        self.assertEqual(out["error"]["code"], ErrorCode.GOAL_POSE_UNRESOLVED)

    def test_every_mission_matches_spec(self):
        for name, spec in MISSION_SPEC.items():
            out = canonicalize(name)
            self.assertEqual(out["action"], spec["action"], name)
            self.assertEqual(out["target_refs"], spec["target_refs"], name)

    def test_fixed_constraints_and_room_order(self):
        # 코드가 채우는 고정 부가 필드 (노션 흡수분)
        rescue = canonicalize("rescue_sequence")
        self.assertTrue(rescue["constraints"]["use_existing_object_only"])
        self.assertTrue(rescue["constraints"]["keep_relief_item_until_finish"])
        self.assertEqual(rescue["room_order"], ["room_1", "room_2", "room_3"])

        inspect = canonicalize("inspect_rescue_rooms")
        self.assertEqual(inspect["room_order"], ["room_1", "room_2", "room_3"])

        press = canonicalize("press_purifier_button")
        self.assertEqual(press["constraints"], {"use_existing_object_only": True})
        self.assertEqual(press["room_order"], [])

    def test_error_json_has_empty_constraints_and_room_order(self):
        out = canonicalize(None)
        self.assertEqual(out["constraints"], {})
        self.assertEqual(out["room_order"], [])

    def test_goal_pose_injection_makes_status_ok(self):
        pose = {"frame_id": "map", "x": 1.25, "y": -0.8, "yaw": 1.57}
        out = canonicalize("open_door", goal_pose=pose)
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["error"])
        self.assertEqual(out["goal_pose"], pose)
        self.assertEqual(out["action"], "open")

    def test_unsupported_mission_returns_error_json(self):
        out = canonicalize("turn_on_the_tv")
        self.assertEqual(out["status"], "error")
        self.assertIsNone(out["mission_name"])
        self.assertIsNone(out["action"])
        self.assertEqual(out["target_refs"], [])
        self.assertIsNone(out["goal_pose"])
        self.assertEqual(out["error"]["code"], ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND)

    def test_none_mission_returns_error_json(self):
        out = canonicalize(None)
        self.assertEqual(out["status"], "error")
        self.assertIsNone(out["mission_name"])

    def test_keys_are_always_identical(self):
        cases = [
            canonicalize("press_purifier_button"),
            canonicalize("go_to_finish", goal_pose={"frame_id": "map", "x": 0, "y": 0, "yaw": 0}),
            canonicalize(None),
            make_error(ErrorCode.LLM_CALL_FAILED),
        ]
        for out in cases:
            self.assertEqual(set(out.keys()), set(OUTPUT_KEYS))

    def test_all_outputs_json_serializable(self):
        for out in [canonicalize("count_fire_targets"), canonicalize("nope"), make_error("X")]:
            json.dumps(out, ensure_ascii=False)  # 예외 없어야 함

    def test_make_error_default_message(self):
        out = make_error(ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND)
        self.assertIn("극한로봇", out["error"]["message"])

    def test_normalize_goal_pose_coerces_ints_to_float(self):
        pose = normalize_goal_pose({"frame_id": "map", "x": 1, "y": 2, "yaw": 0})
        self.assertIsInstance(pose["x"], float)
        self.assertEqual(pose["x"], 1.0)

    def test_normalize_goal_pose_rejects_missing_field(self):
        with self.assertRaises(ValueError):
            normalize_goal_pose({"frame_id": "map", "x": 1, "y": 2})


if __name__ == "__main__":
    unittest.main()
