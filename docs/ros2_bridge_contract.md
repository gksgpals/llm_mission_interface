# ROS2 Bridge 출력 계약 (Mission JSON Contract)

이 문서는 **LLM 미션 인터페이스가 내보내는 출력 형식**을 고정한다.
ROS2 bridge 구현(누가 맡든)은 이 계약만 신뢰하면 되고, 계약은 바뀌지 않는다.

> 요약: bridge 는 이 서비스에 자연어 `text` 를 주고, **항상 동일한 10개 key 를 가진
> Mission JSON** 을 돌려받는다. bridge 는 HTTP 상태코드가 아니라 JSON 의 `status`
> 필드를 읽어 분기한다.

---

## 1. 전송 방식

두 가지 중 하나를 선택(둘 다 지원). bridge 는 하나만 쓰면 된다.

### (A) HTTP (기본, 권장)
```
POST /mission
Content-Type: application/json
```
요청:
```json
{ "text": "공기정화 버튼을 눌러." }
```
응답: 아래 §2 의 Mission JSON. **HTTP status 는 항상 200** (성공/실패는 본문 `status`).

헬스체크:
```
GET /health  →  {"status":"ok","backend":"mock|cli|server"}
```

### (B) FIFO / stdin 스트림
- 입력: 한 줄에 명령 하나(개행 구분)
- 출력: 한 줄에 Mission JSON 하나(개행 구분, `ensure_ascii=false`)
```bash
python3 -m src.mission_service < /tmp/mission_in.fifo > /tmp/mission_out.fifo
```

---

## 2. Mission JSON 스키마 (최종 출력)

**최상위 key 10개는 어떤 경우에도 항상 존재하고, 집합이 동일하다.**
정식 스키마 파일: [`config/mission_schema.json`](../config/mission_schema.json)

| key | 타입 | 설명 |
|-----|------|------|
| `schema_version` | string | 항상 `"1.0"` |
| `request_id` | string | 요청마다 새로 발급되는 UUID |
| `status` | string | `"ok"` \| `"partial"` \| `"error"` |
| `mission_name` | string \| null | 허용 미션명 또는 null |
| `action` | string \| null | **코드가 고정**. LLM 값 무시 |
| `target_refs` | string[] | **코드가 고정**. LLM 값 무시 |
| `constraints` | object | **코드가 고정**. 미션별 실행 제약(불리언). 없으면 `{}` |
| `room_order` | string[] | **코드가 고정**. 방 순회 순서. 없으면 `[]` |
| `goal_pose` | object \| null | `{frame_id,x,y,yaw}` 또는 null |
| `error` | object \| null | `{code,message}` 또는 null |

### status 별 규칙 (bridge 분기 기준)

| status | 의미 | mission_name | goal_pose | error | bridge 동작 |
|--------|------|--------------|-----------|-------|-------------|
| `ok` | 미션 확정 + 좌표 있음 | 있음 | 있음 | null | Nav2/실행 파이프라인에 그대로 전달 |
| `partial` | 미션 확정, 좌표 미해결 | 있음 | null | `GOAL_POSE_UNRESOLVED` | 좌표 조회(semantic map) 후 실행, 또는 대기 |
| `error` | 해석 불가/오류 | null | null | 오류 코드 | 실행 금지, 재입력/로그 처리 |

---

## 3. 오류 코드

| code | status | 발생 조건 |
|------|--------|-----------|
| `GOAL_POSE_UNRESOLVED` | partial | 미션은 해석했으나 지도 좌표가 아직 없음 |
| `UNSUPPORTED_OR_AMBIGUOUS_COMMAND` | error | 허용 미션으로 해석 불가 / 모호 / 미지원 명령 |
| `LLM_OUTPUT_UNPARSEABLE` | error | LLM 응답에서 mission_name 을 못 뽑음(JSON 아님 등) |
| `LLM_CALL_FAILED` | error | llama 실행/호출 실패(바이너리 없음·타임아웃·서버 다운) |
| `INVALID_REQUEST` | error | 요청에 유효한 `text` 없음 / 본문이 JSON 아님 |
| `INTERNAL_ERROR` | error | 예기치 못한 내부 오류(최종 검증 실패 포함, 방어용) |

---

## 4. 허용 미션 ↔ 고정 action / target_refs

이 매핑은 [`config/mission_spec.py`](../config/mission_spec.py) 가 단일 진실 공급원이다.
LLM 은 `mission_name` 만 고르고, 아래 값은 **코드가 강제**한다.

| mission_name | action | target_refs | room_order / constraints | 대회 구간 |
|--------------|--------|-------------|--------------------------|-----------|
| `press_purifier_button` | `press` | `[purifier_button]` | – | ③-2 공기 정화 시스템 작동 |
| `count_fire_targets` | `count` | `[fire_target]` | – | ③-3 화재 규모(타겟 개수) 알림 |
| `open_door` | `open` | `[door]` | – | ③-4 출입문 개방 |
| `fire_response_sequence` | `execute_sequence` | `[purifier_button, fire_target, door]` | – | ③ 화재 진압 전체 |
| `deliver_relief_item` | `deliver` | `[relief_item]` | – | ④-1 구호 물품 식별·운반 |
| `inspect_rescue_rooms` | `inspect` | `[room, victim]` | room_order=[room_1,2,3] | ④-2 방 1,2,3 구조 요구자 인식 |
| `go_to_finish` | `move` | `[finish_line]` | – | ④-4 FINISH 라인 도착 |
| `rescue_sequence` | `execute_sequence` | `[relief_item, room, victim, finish_line]` | room_order=[room_1,2,3], keep_relief_item_until_finish | ④ 재난 구조 전체 |

> 모든 미션에 `constraints.use_existing_object_only = true` 가 기본 적용된다(DB 에 있는 객체만 사용, 환각 금지). `constraints`·`room_order` 는 전부 코드 고정값이라 LLM 출력과 무관하게 항상 동일하다.

---

## 5. goal_pose 주입 (semantic map / object DB 연결 지점)

- **현재**: 지도 데이터가 없으므로 `goal_pose = null`, `status = partial`.
- **나중**: 맵/DB 파트가 `target_ref → 좌표` 를 제공하면
  [`src/goal_pose_resolver.py`](../src/goal_pose_resolver.py) 의 `GoalPoseResolver`
  구현을 주입한다. 그러면 같은 코드가 자동으로 `status = ok` 로 승격한다.

goal_pose 형식(고정):
```json
{ "frame_id": "map", "x": 1.25, "y": -0.80, "yaw": 1.57 }
```

주입 예:
```python
from src.goal_pose_resolver import StaticGoalPoseResolver
from src.mission_service import MissionService

resolver = StaticGoalPoseResolver({
    "purifier_button": {"frame_id": "map", "x": 1.25, "y": -0.80, "yaw": 1.57},
    "door":           {"frame_id": "map", "x": 3.00, "y":  0.40, "yaw": 0.00},
})
service = MissionService(resolver=resolver)   # 이후 응답이 ok 로 나옴
```

---

## 6. bridge 참고 의사코드 (ROS2 측)

```python
resp = http_post("http://<agx-host>:8000/mission", json={"text": user_text})
m = resp.json()                      # 항상 8-key Mission JSON

if m["status"] == "error":
    rospy_log_warn(m["error"]["code"], m["error"]["message"])
    return                            # 실행하지 않음

if m["status"] == "partial":
    # 좌표가 아직 없음 → 맵/DB 로 target_refs 좌표 조회 후 채우거나 대기
    pose = lookup_goal_pose(m["target_refs"])
    if pose is None:
        return
    m["goal_pose"] = pose
    m["status"] = "ok"

# status == ok : action + target_refs + goal_pose 로 실제 로봇 파이프라인 구동
dispatch_to_robot(m["mission_name"], m["action"], m["target_refs"], m["goal_pose"])
```

> 계약이 고정이므로 bridge 는 `mission_name` 별 분기만 관리하면 되고,
> LLM 출력이 흔들려도 `action`/`target_refs`/key 구조는 절대 달라지지 않는다.
