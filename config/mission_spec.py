# -*- coding: utf-8 -*-
"""
극한로봇 경진대회 - 미션 계약(Contract)의 단일 진실 공급원(Single Source of Truth).

이 파일에 정의된 값만이 최종 Mission JSON의 action / target_refs 를 결정한다.
LLM 은 오직 mission_name 만 "분류"할 뿐이며, action / target_refs / goal_pose 를
직접 생성하지 못한다. (canonicalizer 가 여기 값을 강제로 덮어쓴다.)

이 모듈은 표준 라이브러리만 사용하며 외부 의존성이 없다.
"""
from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# 스키마 / 상수
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0"

# 최종 출력 JSON 의 최상위 key. 순서/집합 모두 항상 동일하게 유지된다.
# validator 와 canonicalizer 는 반드시 이 목록을 기준으로 동작한다.
OUTPUT_KEYS: List[str] = [
    "schema_version",
    "request_id",
    "status",
    "mission_name",
    "action",
    "target_refs",
    "constraints",
    "room_order",
    "goal_pose",
    "error",
]

# status 허용 값
ALLOWED_STATUSES = ("ok", "partial", "error")

# 오류 코드. status 가 error 이거나 partial 일 때 error.code 로 사용된다.
class ErrorCode:
    # partial: 미션 해석은 성공했으나 지도 좌표가 아직 없음
    GOAL_POSE_UNRESOLVED = "GOAL_POSE_UNRESOLVED"
    # error: 허용 미션으로 해석 불가 / 모호함 (대회 명세의 기본 오류)
    UNSUPPORTED_OR_AMBIGUOUS_COMMAND = "UNSUPPORTED_OR_AMBIGUOUS_COMMAND"
    # error: LLM 응답에서 mission_name 을 파싱할 수 없음 (JSON 아님 등)
    LLM_OUTPUT_UNPARSEABLE = "LLM_OUTPUT_UNPARSEABLE"
    # error: llama 실행/호출 자체가 실패 (바이너리 없음, 타임아웃, 서버 다운 등)
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    # error: 잘못된 요청 (text 필드 없음 등, api_server 에서 사용)
    INVALID_REQUEST = "INVALID_REQUEST"
    # error: 예기치 못한 내부 예외 / 최종 검증 실패 (방어용)
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 사람이 읽을 수 있는 기본 메시지 (한국어)
ERROR_MESSAGES = {
    ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND: "허용된 극한로봇 미션으로 해석할 수 없습니다.",
    ErrorCode.LLM_OUTPUT_UNPARSEABLE: "LLM 응답에서 미션명을 파싱하지 못했습니다.",
    ErrorCode.LLM_CALL_FAILED: "LLM(Llama) 호출에 실패했습니다.",
    ErrorCode.INVALID_REQUEST: "요청 본문에 유효한 text 필드가 없습니다.",
    ErrorCode.INTERNAL_ERROR: "내부 처리 중 오류가 발생했습니다.",
}


# ---------------------------------------------------------------------------
# 미션 계약 (action / target_refs 는 코드가 고정)
# ---------------------------------------------------------------------------
# 대회 구간 매핑:
#   ③ 화재 진압 지원 구간
#       press_purifier_button   → ③-2 공기 정화 시스템 작동
#       count_fire_targets      → ③-3 돌발 미션(화재 규모 알림, 화재 타겟 1~4개 카운트)
#       open_door               → ③-4 출입문 개방
#       fire_response_sequence  → ③ 구간 전체 시퀀스
#   ④ 재난 구조 탐색 구간
#       deliver_relief_item     → ④-1 구호 물품 식별 및 운반
#       inspect_rescue_rooms    → ④-2 방 1,2,3 구조 요구자 인식
#       go_to_finish            → ④-4 구호물품 FINISH 라인 도착
#       rescue_sequence         → ④ 구간 전체 시퀀스

MISSION_SPEC: Dict[str, Dict[str, object]] = {
    "press_purifier_button": {
        "action": "press",
        "target_refs": ["purifier_button"],
    },
    "count_fire_targets": {
        "action": "count",
        "target_refs": ["fire_target"],
    },
    "open_door": {
        "action": "open",
        "target_refs": ["door"],
    },
    "fire_response_sequence": {
        "action": "execute_sequence",
        "target_refs": [
            "purifier_button",
            "fire_target",
            "door",
        ],
    },
    "deliver_relief_item": {
        "action": "deliver",
        "target_refs": ["relief_item"],
    },
    "inspect_rescue_rooms": {
        "action": "inspect",
        "target_refs": [
            "room",
            "victim",
        ],
    },
    "go_to_finish": {
        "action": "move",
        "target_refs": ["finish_line"],
    },
    "rescue_sequence": {
        "action": "execute_sequence",
        "target_refs": [
            "relief_item",
            "room",
            "victim",
            "finish_line",
        ],
    },
}

# 허용 미션 이름 집합
ALLOWED_MISSIONS = tuple(MISSION_SPEC.keys())


# ---------------------------------------------------------------------------
# 고정 부가 필드: constraints / room_order
#   노션 문서의 "기대 방향" JSON 에 있던 필드를 코드가 결정하는 값으로 흡수한다.
#   LLM 이 만들지 않고 미션별로 코드가 채우므로 출력 안정성에는 영향이 없다.
# ---------------------------------------------------------------------------

# 모든 미션에 공통 적용되는 안전 제약: DB 에 존재하는 객체만 사용(환각 금지).
DEFAULT_CONSTRAINTS: Dict[str, bool] = {"use_existing_object_only": True}

# 기본값과 다른 제약을 갖는 미션만 명시(override).
MISSION_CONSTRAINTS: Dict[str, Dict[str, bool]] = {
    "rescue_sequence": {
        "use_existing_object_only": True,
        "keep_relief_item_until_finish": True,  # 구호물품을 FINISH 까지 보유
    },
}

# 방 순회 순서가 있는 미션만 명시(그 외는 빈 배열).
MISSION_ROOM_ORDER: Dict[str, List[str]] = {
    "inspect_rescue_rooms": ["room_1", "room_2", "room_3"],
    "rescue_sequence": ["room_1", "room_2", "room_3"],
}


# ---------------------------------------------------------------------------
# 프롬프트 / mock 분류기용 부가 메타데이터
#   - description : 프롬프트에 넣을 한국어 설명
#   - examples    : 대표 표현(동의어) 예시 → few-shot / mock 키워드 근거
#   이 정보는 계약(action/target_refs)에 영향을 주지 않는다. 오직 "분류"를 돕는다.
# ---------------------------------------------------------------------------

MISSION_METADATA: Dict[str, Dict[str, object]] = {
    "press_purifier_button": {
        "description": "공기 정화 시스템(환풍 후드) 버튼을 눌러 연기/유독가스를 배출한다.",
        "examples": [
            "공기정화 버튼을 눌러.",
            "환풍 장치를 켜.",
            "연기 제거 장치를 작동시켜.",
            "공기를 정화해.",
            "정화 시스템 버튼 눌러줘.",
        ],
    },
    "count_fire_targets": {
        "description": "화재 타겟(화로 램프)의 개수를 세어 화재 규모를 본부에 알린다.",
        "examples": [
            "화재가 몇 개인지 세.",
            "불이 몇 군데 났는지 확인해.",
            "화재 규모를 알려줘.",
            "화재 타겟 개수를 파악해.",
        ],
    },
    "open_door": {
        "description": "출입문(좌측 PUSH 도어)을 열어 산소를 확보한다.",
        "examples": [
            "문을 열어.",
            "출입문 개방해.",
            "도어를 열어줘.",
            "문 밀어서 열어.",
        ],
    },
    "fire_response_sequence": {
        "description": "화재 진압 지원 구간 전체를 순서대로 수행한다 "
                       "(공기정화 → 화재 규모 확인 → 출입문 개방).",
        "examples": [
            "화재 대응 시퀀스를 실행해.",
            "화재 진압 전체 미션 수행해.",
            "화재 구간 처음부터 끝까지 해.",
        ],
    },
    "deliver_relief_item": {
        "description": "구호 물품을 식별하고 파지하여 운반한다.",
        "examples": [
            "구호 물품을 가져다줘.",
            "구호물자를 운반해.",
            "구조 물품을 배달해.",
            "구호품을 옮겨.",
        ],
    },
    "inspect_rescue_rooms": {
        "description": "방 1,2,3 을 탐색하여 구조 요구자(생존/사망)를 인식한다.",
        "examples": [
            "방들을 점검해서 구조 요구자를 찾아.",
            "각 방에 사람이 있는지 확인해.",
            "인명 구조 순찰해.",
            "생존자 있는지 방을 조사해.",
        ],
    },
    "go_to_finish": {
        "description": "FINISH 라인까지 이동한다.",
        "examples": [
            "피니시 라인으로 가.",
            "결승선까지 이동해.",
            "종료 지점으로 가.",
            "FINISH 로 이동해.",
        ],
    },
    "rescue_sequence": {
        "description": "재난 구조 탐색 구간 전체를 순서대로 수행한다 "
                       "(구호물품 운반 → 방 탐색/구조자 인식 → FINISH 도착).",
        "examples": [
            "재난 구조 시퀀스를 실행해.",
            "구조 구간 전체 미션 수행해.",
            "인명 구조 전체 과정을 해.",
        ],
    },
}


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def is_supported(mission_name: object) -> bool:
    """mission_name 이 허용 목록에 있는 유효 문자열인지."""
    return isinstance(mission_name, str) and mission_name in MISSION_SPEC


def get_action(mission_name: str) -> str:
    """미션의 고정 action. (호출 전 is_supported 로 확인할 것)"""
    return str(MISSION_SPEC[mission_name]["action"])


def get_target_refs(mission_name: str) -> List[str]:
    """미션의 고정 target_refs 사본(복사본)을 반환한다."""
    return list(MISSION_SPEC[mission_name]["target_refs"])  # type: ignore[arg-type]


def get_constraints(mission_name: str) -> Dict[str, bool]:
    """미션의 고정 constraints 사본을 반환한다."""
    return dict(MISSION_CONSTRAINTS.get(mission_name, DEFAULT_CONSTRAINTS))


def get_room_order(mission_name: str) -> List[str]:
    """미션의 고정 room_order 사본을 반환한다(없으면 빈 배열)."""
    return list(MISSION_ROOM_ORDER.get(mission_name, []))
