# -*- coding: utf-8 -*-
"""
GoalPoseResolver: target_ref → 지도 좌표(goal_pose) 조회 인터페이스.

실제 좌표는 semantic map / object DB 를 담당하는 다른 파트가 제공한다.
이 파일은 '그 결과를 받을 수 있는 자리'만 정의한다.

- 지금(맵 데이터 없음): NullGoalPoseResolver → 항상 None → 서비스는 partial 처리.
- 나중(맵 연결): StaticGoalPoseResolver(JSON) 또는 커스텀 Resolver 를 주입하면
  같은 인터페이스로 좌표가 채워지고 서비스가 자동으로 ok 처리한다.

반환 형식: {"frame_id": "map", "x": float, "y": float, "yaw": float} 또는 None.
"""
from __future__ import annotations

import json
import os
import sys
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

GoalPose = Dict[str, object]  # {"frame_id","x","y","yaw"}


class GoalPoseResolver(ABC):
    """모든 좌표 조회기의 공통 인터페이스."""

    @abstractmethod
    def resolve(self, mission_name: str, target_refs: List[str]) -> Optional[GoalPose]:
        """미션의 대상(target_refs)에 대한 goal_pose 를 반환. 없으면 None."""
        raise NotImplementedError


class NullGoalPoseResolver(GoalPoseResolver):
    """항상 None 을 반환한다 (맵 데이터가 아직 없는 현재 기본값)."""

    def resolve(self, mission_name: str, target_refs: List[str]) -> Optional[GoalPose]:
        return None


class StaticGoalPoseResolver(GoalPoseResolver):
    """target_ref → goal_pose 를 정적 매핑(dict/JSON)으로 조회한다.

    mapping 예:
        {
          "purifier_button": {"frame_id":"map","x":1.25,"y":-0.8,"yaw":1.57},
          "door":            {"frame_id":"map","x":3.0, "y":0.4, "yaw":0.0}
        }

    미션의 첫 번째 target_ref 를 대표 좌표로 사용한다.
    (시퀀스 미션은 대표 지점 1곳만 반환; 세부 웨이포인트는 향후 확장 지점.)
    """

    def __init__(self, mapping: Optional[Dict[str, GoalPose]] = None):
        self._mapping: Dict[str, GoalPose] = dict(mapping or {})

    @classmethod
    def from_json_file(cls, path: str) -> "StaticGoalPoseResolver":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def set_pose(self, target_ref: str, pose: GoalPose) -> None:
        self._mapping[target_ref] = pose

    def resolve(self, mission_name: str, target_refs: List[str]) -> Optional[GoalPose]:
        for ref in target_refs:
            if ref in self._mapping:
                return self._mapping[ref]
        return None
