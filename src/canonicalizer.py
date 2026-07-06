# -*- coding: utf-8 -*-
"""
Canonicalizer: mission_name(+선택적 goal_pose) → 최종 Mission JSON.

이 모듈이 계약을 강제한다:
  - action, target_refs 는 오직 MISSION_SPEC 에서 가져온다.
  - LLM 이 만든 action/target_refs/goal_pose 는 이 단계에서 완전히 버려진다.
  - 출력은 항상 OUTPUT_KEYS 10개 key 를 정확히 같은 집합으로 가진다.

상태 규칙:
  - 미지원/모호        → status=error   (UNSUPPORTED_OR_AMBIGUOUS_COMMAND 등)
  - 지원 + 좌표 없음   → status=partial (GOAL_POSE_UNRESOLVED)
  - 지원 + 좌표 있음   → status=ok      (error=null)
"""
from __future__ import annotations

import os
import sys
import uuid
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import (  # noqa: E402
    ERROR_MESSAGES,
    ErrorCode,
    OUTPUT_KEYS,
    SCHEMA_VERSION,
    get_action,
    get_constraints,
    get_room_order,
    get_target_refs,
    is_supported,
)


def new_request_id() -> str:
    return str(uuid.uuid4())


def _skeleton(request_id: Optional[str]) -> Dict[str, object]:
    """항상 동일한 10개 key 를 가진 빈 골격을 만든다."""
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or new_request_id(),
        "status": None,
        "mission_name": None,
        "action": None,
        "target_refs": [],
        "constraints": {},
        "room_order": [],
        "goal_pose": None,
        "error": None,
    }


def normalize_goal_pose(pose: object) -> Dict[str, object]:
    """goal_pose 를 {frame_id,x,y,yaw} 로 정규화. 형식이 잘못되면 ValueError."""
    if not isinstance(pose, dict):
        raise ValueError("goal_pose 는 dict 여야 합니다.")
    try:
        return {
            "frame_id": str(pose.get("frame_id", "map")),
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "yaw": float(pose["yaw"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"goal_pose 필드가 올바르지 않습니다: {pose}") from exc


def make_error(
    code: str,
    request_id: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, object]:
    """고정 구조의 오류 Mission JSON 을 생성한다 (status=error)."""
    out = _skeleton(request_id)
    out["status"] = "error"
    out["mission_name"] = None
    out["action"] = None
    out["target_refs"] = []
    out["goal_pose"] = None
    out["error"] = {
        "code": code,
        "message": message or ERROR_MESSAGES.get(code, code),
    }
    return out


def canonicalize(
    mission_name: object,
    request_id: Optional[str] = None,
    goal_pose: Optional[object] = None,
) -> Dict[str, object]:
    """mission_name 을 계약에 맞는 최종 JSON 으로 변환한다.

    - mission_name 이 미지원/None 이면 UNSUPPORTED_OR_AMBIGUOUS_COMMAND 오류 JSON.
    - 지원 미션이면 action/target_refs 를 MISSION_SPEC 값으로 고정한다.
    - goal_pose 가 없으면 partial(GOAL_POSE_UNRESOLVED), 있으면 ok.
    """
    rid = request_id or new_request_id()

    if not is_supported(mission_name):
        return make_error(ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND, rid)

    name = str(mission_name)
    action = get_action(name)              # 코드가 결정 (LLM 값 무시)
    target_refs: List[str] = get_target_refs(name)  # 코드가 결정 (LLM 값 무시)

    out = _skeleton(rid)
    out["mission_name"] = name
    out["action"] = action
    out["target_refs"] = target_refs
    out["constraints"] = get_constraints(name)   # 코드가 결정 (LLM 값 무시)
    out["room_order"] = get_room_order(name)      # 코드가 결정 (LLM 값 무시)

    if goal_pose is None:
        out["status"] = "partial"
        out["goal_pose"] = None
        primary = target_refs[0] if target_refs else name
        out["error"] = {
            "code": ErrorCode.GOAL_POSE_UNRESOLVED,
            "message": f"{primary}의 지도 좌표가 아직 조회되지 않았습니다.",
        }
    else:
        out["status"] = "ok"
        out["goal_pose"] = normalize_goal_pose(goal_pose)
        out["error"] = None

    # 방어적으로 key 집합을 강제 (혹시 모를 오염 방지)
    assert set(out.keys()) == set(OUTPUT_KEYS)
    return out
