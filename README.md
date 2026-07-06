# llm_mission_interface

극한로봇 경진대회용 **LLM 미션 분류 → 계약형 Mission JSON** 소프트웨어 모듈.

자연어 명령이 **어떤 표현으로 들어와도**, 그리고 로컬 Llama 의 출력이 **흔들려도**,
최종 결과는 **항상 동일한 스키마의 Mission JSON** 을 지킨다.

```
사용자 자연어  →  [LLM: mission_name 분류]  →  [코드: action/target_refs 고정]
              →  [goal_pose 주입 시도]      →  [Validator: 계약 검증]
              →  항상 동일한 Mission JSON
```

핵심 설계 원칙 한 줄: **LLM 은 "무슨 미션인지"만 고르고, 나머지는 전부 코드가 강제한다.**

---

## 1. 왜 이렇게 만드는가

소형 로컬 모델(Llama 1B/3B)은 JSON 형식을 자주 어기고, 없는 필드를 지어내고,
엉뚱한 `action` 을 만든다. 그래서 LLM 에게는 **분류(mission_name)만** 시키고,
`action` · `target_refs` · `goal_pose` 는 신뢰하지 않고 코드가 결정한다.

예) LLM 이 이렇게 이상하게 내보내도 —
```json
{ "mission_name": "press_purifier_button", "action": "turn_on", "target_refs": ["ventilator"] }
```
최종 출력은 반드시 이렇게 교정된다 —
```json
{
  "schema_version": "1.0",
  "request_id": "…uuid…",
  "status": "partial",
  "mission_name": "press_purifier_button",
  "action": "press",
  "target_refs": ["purifier_button"],
  "goal_pose": null,
  "error": { "code": "GOAL_POSE_UNRESOLVED", "message": "purifier_button의 지도 좌표가 아직 조회되지 않았습니다." }
}
```

---

## 2. 폴더 구조

```
llm_mission_interface/
├── README.md
├── requirements.txt              # 런타임 stdlib 전용 (의존성 0)
├── config/
│   ├── mission_spec.py           # 단일 진실 공급원: 미션↔action/target_refs, 오류코드, 스키마상수
│   ├── prompt.txt                # LLM 분류 프롬프트(템플릿)
│   └── mission_schema.json       # 출력 JSON Schema (계약 문서)
├── src/
│   ├── llama_runner.py           # Llama 호출 래퍼 (mock | cli | server)
│   ├── classifier.py             # 프롬프트 구성 + 호출 + 관대한 mission_name 파싱
│   ├── canonicalizer.py          # mission_name → 계약형 JSON (action/target_refs 고정)
│   ├── validator.py              # 최종 JSON 계약 검증 (마지막 방어선)
│   ├── goal_pose_resolver.py     # 좌표 조회 인터페이스 (semantic map 연결 지점)
│   ├── mission_service.py        # 오케스트레이터 + CLI/FIFO 진입점
│   └── api_server.py             # HTTP 서버 (POST /mission, GET /health)
├── tests/
│   ├── test_canonicalizer.py
│   ├── test_validator.py
│   ├── test_service_e2e.py       # 동의어/오류/계약 강제 엔드투엔드
│   └── test_commands.json        # 표현 변형 ↔ 기대 mission_name 데이터셋
└── docs/
    └── ros2_bridge_contract.md   # ROS2 bridge 가 읽는 출력 계약
```

---

## 3. 빠른 시작 (로컬, 모델 없이)

기본 백엔드는 `mock` (모델 없이 도는 결정론적 키워드 분류기)이라 **설치가 필요 없다.**

```bash
cd llm_mission_interface

# (1) 단일 명령
python3 -m src.mission_service "공기정화 버튼을 눌러."

# (2) FIFO/stdin 스트림 (한 줄에 하나)
printf '문 열어\n화재가 몇 개인지 세\n' | python3 -m src.mission_service

# (3) HTTP 서버
python3 -m src.api_server            # http://0.0.0.0:8000
curl -s -X POST http://127.0.0.1:8000/mission \
     -H 'Content-Type: application/json' \
     -d '{"text":"연기 제거 장치를 작동시켜."}'

# (4) 테스트 전체
python3 -m unittest discover -s tests
```

> `mock` 은 **오프라인 개발/회귀 테스트용 근사치**다. 실제 의미 이해는
> AGX 에서 진짜 Llama(`cli`/`server`)로 수행한다.

---

## 4. AGX 에서 실제 Llama 로 실행

백엔드는 환경변수 `LLM_BACKEND` 로 고른다: `mock`(기본) · `cli` · `server`.

### (A) llama-cli (서브프로세스) — 권장
```bash
export LLM_BACKEND=cli
export LLAMA_CLI_PATH=/opt/llama.cpp/build/bin/llama-cli
export LLAMA_MODEL_PATH=/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
export LLAMA_N_GPU_LAYERS=999          # GPU 오프로딩(-ngl)
python3 -m src.api_server
```

### (B) llama-server (HTTP)
```bash
# 먼저 서버 구동:  llama-server -m /models/xxx.gguf --port 8080 -ngl 999
export LLM_BACKEND=server
export LLAMA_SERVER_URL=http://127.0.0.1:8080
export LLAMA_SERVER_MODE=native        # native(/completion) | openai(/v1/chat/completions)
python3 -m src.api_server
```

### 환경변수 정리

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BACKEND` | `mock` | `mock` \| `cli` \| `server` |
| `LLAMA_CLI_PATH` | `llama-cli` | (cli) 실행 파일 경로 |
| `LLAMA_MODEL_PATH` | — | (cli) gguf 모델 경로 (**필수**) |
| `LLAMA_CLI_EXTRA_ARGS` | — | (cli) 추가 인자(공백 구분) |
| `LLAMA_SERVER_URL` | `http://127.0.0.1:8080` | (server) 베이스 URL |
| `LLAMA_SERVER_MODE` | `native` | (server) `native` \| `openai` |
| `LLAMA_N_PREDICT` | `64` | 생성 토큰 수 |
| `LLAMA_TEMPERATURE` | `0.0` | 온도(분류라 0 권장) |
| `LLAMA_N_GPU_LAYERS` | `999` | (cli) GPU 오프로딩 레이어 수 |
| `LLAMA_TIMEOUT_S` | `60` | 호출 타임아웃(초) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | (api_server) 바인드 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |

> 팀원은 **패키지를 수정하지 않고** 위 환경변수만 설정해 AGX 에서 실행하면 된다.

---

## 5. 출력 계약 (요약)

최상위 key 10개는 **항상** 존재하고 집합이 동일하다. 상세는
[`docs/ros2_bridge_contract.md`](docs/ros2_bridge_contract.md),
[`config/mission_schema.json`](config/mission_schema.json) 참고.

`schema_version` · `request_id` · `status` · `mission_name` · `action` · `target_refs` · `constraints` · `room_order` · `goal_pose` · `error`

- `status`: `ok`(좌표 있음) / `partial`(미션 확정·좌표 없음) / `error`(해석 불가)
- `action`, `target_refs`, `constraints`, `room_order`: **코드가 고정** (LLM 값 무시)
  - `constraints`: 미션별 실행 제약(예: `use_existing_object_only`, `keep_relief_item_until_finish`)
  - `room_order`: 방 순회 순서(예: `[room_1, room_2, room_3]`), 해당 없으면 `[]`
- `goal_pose`: 없으면 `null`(→ partial), 맵 연결 시 `{frame_id,x,y,yaw}`(→ ok)

허용 미션 8종과 고정값 매핑 표는
[`docs/ros2_bridge_contract.md` §4](docs/ros2_bridge_contract.md) 에 있다.

---

## 6. 로컬 자체검증 항목 (모두 자동화됨)

`python3 -m unittest discover -s tests` 로 아래가 전부 검증된다.

| 검증 항목 | 담당 테스트 |
|-----------|-------------|
| 동의어 명령이 같은 `mission_name` 으로 변환 | `test_service_e2e` (test_commands.json) |
| LLM 이 잘못된 `action` 을 내도 고정값으로 교정 | `test_llm_bogus_action_is_overridden` |
| LLM 이 JSON 이 아닌 텍스트를 내도 error JSON | `test_non_json_output_yields_error` |
| 허용되지 않은 명령에서 error JSON | `test_unknown_mission_name_yields_unsupported` |
| goal_pose 없어도 필드가 `null` 유지 / 주입 시 ok 승격 | `TestGoalPoseInjection` |
| 모든 최종 출력이 JSON 파싱 가능 | `test_all_cases`, `test_all_outputs_json_serializable` |
| 모든 최종 출력 key 가 항상 동일 | `test_keys_are_always_identical`, `test_all_cases` |
| action/target_refs 불일치·키 누락 차단 | `tests/test_validator.py` |

---

## 7. 팀원(AGX 담당)이 보고할 항목

이 패키지를 AGX 에서 실행하며 아래를 측정·회신:

- 1B / 3B 모델별 로딩 성공 여부
- 명령별 실제 출력 JSON
- 응답 시간 / GPU 사용량 / RAM 사용량
- JSON 파싱 성공 여부
- 명령 표현 변형별 분류 정확도 (`tests/test_commands.json` 활용)
- HTTP 또는 FIFO 출력 수신 여부 / ROS2 bridge 수신 여부
- 오류 로그 (`LOG_LEVEL=DEBUG` 로 재현)

분류 정확도 측정 예:
```bash
# AGX 에서 실제 모델로 test_commands.json 전체를 돌려 정확도 확인
LLM_BACKEND=cli LLAMA_MODEL_PATH=/models/xxx.gguf \
python3 - <<'PY'
import json
from src.mission_service import MissionService
svc = MissionService()
cases = json.load(open("tests/test_commands.json"))["cases"]
ok = sum(svc.handle(c["text"])["mission_name"] == c["expected_mission"] for c in cases)
print(f"정확도: {ok}/{len(cases)} = {ok/len(cases):.1%}")
PY
```

---

## 8. 담당 범위

이 모듈이 책임지는 것: **Llama 실행 + 프롬프트 + JSON Schema + Canonicalizer +
Validator + 출력 인터페이스 계약 + 테스트.**

이 모듈 밖(다른 파트/후속 작업): 실제 AGX 하드웨어 측정, semantic map/object DB
데이터 구축, 실제 ROS2 주행·조작 실행. — 이들은 본 모듈의 출력 계약(§5)에
맞춰 연결된다.
