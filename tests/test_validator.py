# -*- coding: utf-8 -*-
"""validator 단위 테스트: 계약 위반을 반드시 잡아내는지 검증."""
import copy
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import ErrorCode  # noqa: E402
from src.canonicalizer import canonicalize, make_error  # noqa: E402
from src.validator import validate  # noqa: E402

VALID_PARTIAL = canonicalize("press_purifier_button")
VALID_OK = canonicalize("open_door", goal_pose={"frame_id": "map", "x": 1.0, "y": 2.0, "yaw": 0.0})
VALID_ERROR = make_error(ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND)


class TestValidator(unittest.TestCase):
    def test_valid_partial_passes(self):
        ok, errs = validate(VALID_PARTIAL)
        self.assertTrue(ok, errs)

    def test_valid_ok_passes(self):
        ok, errs = validate(VALID_OK)
        self.assertTrue(ok, errs)

    def test_valid_error_passes(self):
        ok, errs = validate(VALID_ERROR)
        self.assertTrue(ok, errs)

    def test_mismatched_action_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["action"] = "turn_on"
        ok, errs = validate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("action" in e for e in errs))

    def test_mismatched_target_refs_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["target_refs"] = ["ventilator"]
        ok, errs = validate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("target_refs" in e for e in errs))

    def test_mismatched_constraints_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["constraints"] = {"use_existing_object_only": False}
        ok, errs = validate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("constraints" in e for e in errs))

    def test_mismatched_room_order_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["room_order"] = ["room_9"]
        ok, errs = validate(bad)
        self.assertFalse(ok)
        self.assertTrue(any("room_order" in e for e in errs))

    def test_missing_key_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        del bad["goal_pose"]
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_extra_key_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["extra"] = 123
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_bad_status_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["status"] = "done"
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_invalid_mission_name_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["mission_name"] = "fly_to_moon"
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_ok_status_with_error_not_null_fails(self):
        bad = copy.deepcopy(VALID_OK)
        bad["error"] = {"code": "X", "message": "y"}
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_error_status_with_mission_name_fails(self):
        bad = copy.deepcopy(VALID_ERROR)
        bad["mission_name"] = "open_door"
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_partial_without_error_fails(self):
        bad = copy.deepcopy(VALID_PARTIAL)
        bad["error"] = None
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_bad_goal_pose_shape_fails(self):
        bad = copy.deepcopy(VALID_OK)
        bad["goal_pose"] = {"frame_id": "map", "x": 1.0}  # y, yaw 누락
        ok, errs = validate(bad)
        self.assertFalse(ok)

    def test_non_dict_fails(self):
        ok, errs = validate("not a dict")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
