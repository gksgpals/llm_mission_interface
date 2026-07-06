# -*- coding: utf-8 -*-
"""
Validator: 최종 Mission JSON 이 계약을 100% 지키는지 검사한다.

이 계층은 '마지막 방어선'이다. canonicalizer 가 잘못 만들거나, 누군가 중간에
필드를 오염시켜도, 여기서 걸러서 서비스가 규격 밖 JSON 을 내보내지 못하게 한다.

validate() 는 (is_valid, errors) 를 돌려주고 예외를 던지지 않는다.
서비스는 검증 실패 시 응답을 고정 오류 JSON 으로 교체한다.
"""
from __future__ import annotations

import numbers
import os
import sys
from typing import List, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import (  # noqa: E402
    ALLOWED_MISSIONS,
    ALLOWED_STATUSES,
    OUTPUT_KEYS,
    SCHEMA_VERSION,
    get_action,
    get_constraints,
    get_room_order,
    get_target_refs,
)


def _is_number(v) -> bool:
    return isinstance(v, numbers.Real) and not isinstance(v, bool)


def _validate_goal_pose(pose, errors: List[str]) -> None:
    if pose is None:
        return
    if not isinstance(pose, dict):
        errors.append("goal_pose 는 null 또는 object 여야 합니다.")
        return
    expected = {"frame_id", "x", "y", "yaw"}
    if set(pose.keys()) != expected:
        errors.append(f"goal_pose key 는 {sorted(expected)} 여야 합니다. 실제: {sorted(pose.keys())}")
    if not isinstance(pose.get("frame_id"), str):
        errors.append("goal_pose.frame_id 는 문자열이어야 합니다.")
    for k in ("x", "y", "yaw"):
        if not _is_number(pose.get(k)):
            errors.append(f"goal_pose.{k} 는 숫자여야 합니다.")


def _validate_error(err, errors: List[str]) -> None:
    if err is None:
        return
    if not isinstance(err, dict):
        errors.append("error 는 null 또는 object 여야 합니다.")
        return
    if set(err.keys()) != {"code", "message"}:
        errors.append(f"error key 는 ['code','message'] 여야 합니다. 실제: {sorted(err.keys())}")
    if not isinstance(err.get("code"), str) or not err.get("code"):
        errors.append("error.code 는 비어있지 않은 문자열이어야 합니다.")
    if not isinstance(err.get("message"), str):
        errors.append("error.message 는 문자열이어야 합니다.")


def validate(mission_json) -> Tuple[bool, List[str]]:
    """최종 JSON 을 검사하고 (통과여부, 오류목록) 을 반환한다."""
    errors: List[str] = []

    # 0) 타입/키 집합
    if not isinstance(mission_json, dict):
        return False, ["최상위는 object(dict) 여야 합니다."]
    if set(mission_json.keys()) != set(OUTPUT_KEYS):
        errors.append(
            f"key 집합이 계약과 다릅니다. 기대: {sorted(OUTPUT_KEYS)}, "
            f"실제: {sorted(mission_json.keys())}"
        )
        # key 가 다르면 이후 검사가 KeyError 위험 → 여기서 중단
        return False, errors

    m = mission_json

    # 1) schema_version
    if m["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version 은 '{SCHEMA_VERSION}' 여야 합니다. 실제: {m['schema_version']!r}")

    # 2) request_id
    if not isinstance(m["request_id"], str) or not m["request_id"]:
        errors.append("request_id 는 비어있지 않은 문자열이어야 합니다.")

    # 3) status
    status = m["status"]
    if status not in ALLOWED_STATUSES:
        errors.append(f"status 는 {list(ALLOWED_STATUSES)} 중 하나여야 합니다. 실제: {status!r}")

    # 4) mission_name
    mission_name = m["mission_name"]
    if mission_name is not None and mission_name not in ALLOWED_MISSIONS:
        errors.append(f"mission_name 은 허용 목록 또는 null 이어야 합니다. 실제: {mission_name!r}")

    # 5) action / target_refs / constraints / room_order 가 mission_name 고정값과 일치
    if not isinstance(m["target_refs"], list) or not all(isinstance(x, str) for x in m["target_refs"]):
        errors.append("target_refs 는 문자열 배열이어야 합니다.")
    if not isinstance(m["constraints"], dict):
        errors.append("constraints 는 object 여야 합니다.")
    if not isinstance(m["room_order"], list) or not all(isinstance(x, str) for x in m["room_order"]):
        errors.append("room_order 는 문자열 배열이어야 합니다.")

    if mission_name in ALLOWED_MISSIONS:
        expected_action = get_action(mission_name)
        expected_refs = get_target_refs(mission_name)
        if m["action"] != expected_action:
            errors.append(
                f"action 이 mission_name({mission_name}) 고정값과 다릅니다. "
                f"기대: {expected_action!r}, 실제: {m['action']!r}"
            )
        if m["target_refs"] != expected_refs:
            errors.append(
                f"target_refs 가 mission_name({mission_name}) 고정값과 다릅니다. "
                f"기대: {expected_refs}, 실제: {m['target_refs']}"
            )
        if m["constraints"] != get_constraints(mission_name):
            errors.append(
                f"constraints 가 mission_name({mission_name}) 고정값과 다릅니다. "
                f"기대: {get_constraints(mission_name)}, 실제: {m['constraints']}"
            )
        if m["room_order"] != get_room_order(mission_name):
            errors.append(
                f"room_order 가 mission_name({mission_name}) 고정값과 다릅니다. "
                f"기대: {get_room_order(mission_name)}, 실제: {m['room_order']}"
            )
    else:
        # mission_name 이 null 이면 action null, 나머지는 비어 있어야 함
        if m["action"] is not None:
            errors.append("mission_name 이 null 이면 action 도 null 이어야 합니다.")
        if m["target_refs"] != []:
            errors.append("mission_name 이 null 이면 target_refs 는 [] 여야 합니다.")
        if m["constraints"] != {}:
            errors.append("mission_name 이 null 이면 constraints 는 {} 여야 합니다.")
        if m["room_order"] != []:
            errors.append("mission_name 이 null 이면 room_order 는 [] 여야 합니다.")

    # 6) goal_pose / error 구조
    _validate_goal_pose(m["goal_pose"], errors)
    _validate_error(m["error"], errors)

    # 7) status 별 교차 규칙
    if status == "ok":
        if m["error"] is not None:
            errors.append("status=ok 이면 error 는 null 이어야 합니다.")
        if m["goal_pose"] is None:
            errors.append("status=ok 이면 goal_pose 가 존재해야 합니다.")
        if mission_name is None:
            errors.append("status=ok 이면 mission_name 이 null 이 아니어야 합니다.")
    elif status == "partial":
        if m["error"] is None:
            errors.append("status=partial 이면 error 가 존재해야 합니다.")
        if mission_name is None:
            errors.append("status=partial 이면 mission_name 이 null 이 아니어야 합니다.")
        if m["goal_pose"] is not None:
            errors.append("status=partial 이면 goal_pose 는 null 이어야 합니다.")
    elif status == "error":
        if m["error"] is None:
            errors.append("status=error 이면 error 가 존재해야 합니다.")
        if mission_name is not None:
            errors.append("status=error 이면 mission_name 은 null 이어야 합니다.")
        if m["goal_pose"] is not None:
            errors.append("status=error 이면 goal_pose 는 null 이어야 합니다.")

    return (len(errors) == 0), errors


def is_valid(mission_json) -> bool:
    ok, _ = validate(mission_json)
    return ok


class ValidationError(Exception):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_or_raise(mission_json) -> None:
    ok, errors = validate(mission_json)
    if not ok:
        raise ValidationError(errors)
