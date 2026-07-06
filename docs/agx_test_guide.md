# AGX Orin — LLM 미션 인터페이스 테스트 가이드

> 목적: Jetson AGX Orin에서 Llama(GGUF)를 llama.cpp로 실행하고, **자연어 미션 명령 → 고정 형식 Mission JSON** 변환이 정상 동작하는지 확인한다.
> 오늘 범위: LLM 단독 + 미션 인터페이스 모듈 실행까지. ROS2·Nav2·BT·로봇팔 연동은 제외.
> 담당: 팀원(AGX 실행/측정). 패키지 수정 없이 **환경변수만** 설정해 실행하면 된다.

## 권장 구성
| 항목 | 권장값 |
| --- | --- |
| 보드 | Jetson AGX Orin 64GB |
| 런타임 | llama.cpp (CUDA 빌드) |
| 모델 형식 | GGUF |
| 1차 모델 | Llama 3.2 1B Instruct Q4_K_M |
| 2차 모델 | Llama 3.2 3B Instruct Q4_K_M |
| 실행 목표 | 자연어 명령 → Mission JSON 출력 |

---

## 0. 핵심 개념 (먼저 읽기)
- **LLM은 `mission_name`만 분류**한다. `action`·`target_refs`·`constraints`·`room_order`·`goal_pose`는 **코드(모듈)가 고정**으로 채운다.
- 따라서 LLM 출력이 흔들려도 **최종 JSON 형식은 항상 동일**하다. 우리가 볼 것은 "형식이 깨지나"가 아니라 "**의미 분류가 맞나 / 속도·메모리가 괜찮나**"이다.
- 이 가이드의 명령은 mock이 아니라 **실제 모델(`LLM_BACKEND=cli`)** 로 돌린다.

---

## 1. AGX 성능 모드
```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```
별도 터미널에서 모니터링(응답 중 GPU/RAM/온도 관찰):
```bash
tegrastats
```
관찰 포인트: `GR3D_FREQ`(GPU 사용률), RAM 사용량, 온도, OOM/강제종료 여부.

## 2. llama.cpp CUDA 빌드 (이미 했으면 건너뜀)
```bash
sudo apt update && sudo apt install -y git cmake build-essential
nvcc --version   # CUDA 없으면 JetPack/CUDA 먼저 확인

git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j$(nproc)   # OOM 시 -j4
ls ./build/bin/llama-cli    # 실행 파일 확인
```

## 3. GGUF 모델 배치
```bash
mkdir -p ~/llama.cpp/models
# 아래 두 파일을 배치 (실제 다운로드한 파일명에 맞춤)
#   Llama-3.2-1B-Instruct-Q4_K_M.gguf
#   Llama-3.2-3B-Instruct-Q4_K_M.gguf
```

## 4. 미션 인터페이스 패키지 배치
`llm_mission_interface/` 폴더를 AGX로 복사한다(예: `scp -r` 또는 git). 파이썬 설치는 필요 없다(표준 라이브러리만 사용).
```bash
cd ~/llm_mission_interface
python3 -c "import sys; print(sys.version)"   # python3 존재 확인
```

## 5. 환경변수 설정 (모델 연결)
```bash
export LLM_BACKEND=cli
export LLAMA_CLI_PATH=$HOME/llama.cpp/build/bin/llama-cli
export LLAMA_MODEL_PATH=$HOME/llama.cpp/models/Llama-3.2-1B-Instruct-Q4_K_M.gguf
export LLAMA_N_GPU_LAYERS=99
export LLAMA_N_PREDICT=64
export LLAMA_TEMPERATURE=0.0     # 분류라 0 권장
```
> 3B 테스트 시 `LLAMA_MODEL_PATH`만 3B 파일로 바꾸면 된다.

---

## 6. 테스트 실행

### 6-1. 스모크: 명령 한 줄 → JSON
```bash
python3 -m src.mission_service "공기정화 버튼을 눌러."
```
기대: `mission_name: press_purifier_button`, `action: press`, 형식이 온전한 JSON.

### 6-2. 동의어/복합 표현 확인
```bash
python3 -m src.mission_service "환풍 장치를 켜."
python3 -m src.mission_service "불이 몇 군데 났는지 확인해."
python3 -m src.mission_service "공기정화 버튼을 누르고 화재 타겟 개수를 확인한 뒤 문을 열어."
python3 -m src.mission_service "구호물품을 들고 방 1,2,3을 탐색한 뒤 finish로 이동해."
```
기대: 순서대로 press_purifier_button / count_fire_targets / fire_response_sequence / rescue_sequence.

### 6-3. 분류 정확도 (표현 변형 세트)
```bash
python3 - <<'PY'
import json, time
from src.mission_service import MissionService
svc = MissionService()
cases = json.load(open("tests/test_commands.json"))["cases"]
ok = 0; t0 = time.time()
for c in cases:
    got = svc.handle(c["text"])["mission_name"]
    hit = (got == c["expected_mission"])
    ok += hit
    print(("O" if hit else "X"), c["text"], "->", got)
print(f"\n정확도: {ok}/{len(cases)} = {ok/len(cases):.0%},  평균 {1000*(time.time()-t0)/len(cases):.0f} ms/건")
PY
```

### 6-4. 형식 안정성 데모 (LLM이 흔들려도 형식 고정)
```bash
python3 -m tests.demo_stability
```

### 6-5. 자동 테스트 스위트(계약 회귀)
```bash
python3 -m unittest discover -s tests
```
기대: `Ran 39 tests ... OK`.

### 6-6. HTTP 경로 (ROS2 bridge 수신용) — 터미널 2개
```bash
# 터미널 A
python3 -m src.api_server            # http://0.0.0.0:8000
# 터미널 B
curl -s -X POST http://127.0.0.1:8000/mission \
     -H 'Content-Type: application/json' \
     -d '{"text":"연기 제거 장치를 작동시켜."}'
curl -s http://127.0.0.1:8000/health
```

### 6-7. 응답 시간 단독 측정
```bash
time python3 -m src.mission_service "출입문 개방해."
```

---

## 7. 결과 기록 양식
| 모델 | 양자화 | 명령 | 응답 시간 | JSON 유효성 | 분류 결과(mission_name) | GPU/RAM/온도 | 비고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Llama 3.2 1B | Q4_K_M | 공기정화 버튼을 눌러 | | | | | |
| Llama 3.2 1B | Q4_K_M | 불이 몇 군데 났는지 확인해 | | | | | |
| Llama 3.2 1B | Q4_K_M | (복합) 버튼 누르고…문 열어 | | | | | |
| Llama 3.2 1B | Q4_K_M | (복합) 구호물품…finish 이동 | | | | | |
| Llama 3.2 3B | Q4_K_M | 공기정화 버튼을 눌러 | | | | | |
| Llama 3.2 3B | Q4_K_M | (복합) 구호물품…finish 이동 | | | | | |

추가 회신 항목:
- 1B / 3B 모델별 로딩 성공 여부
- `test_commands.json` 전체 분류 정확도(6-3 결과)
- HTTP/FIFO 출력 수신 여부(6-6)
- 오류 로그 (`LOG_LEVEL=DEBUG` 로 재현)

---

## 8. 성공 기준
- [ ] `llama-cli` 실행 + GGUF 로딩 성공 (1B, 3B)
- [ ] 한국어 명령 입력 → Mission JSON 출력
- [ ] 6-5 자동 스위트 `OK` (39 tests)
- [ ] 6-3 분류 정확도 기록 (1B/3B 각각)
- [ ] 1B 응답 시간 기록, 3B 실행 가능 여부 확인
- [ ] 실행 중 OOM/발열 이슈 없음
- [ ] HTTP `POST /mission` 정상 수신

---

## 9. 트러블슈팅
| 증상 | 확인 | 대응 |
| --- | --- | --- |
| `nvcc: command not found` | CUDA Toolkit 인식 | JetPack/CUDA 설치·PATH 확인 |
| 빌드 중 OOM/멈춤 | 병렬 빌드 수 | `-j4` 또는 `-j2`로 재빌드 |
| `LLM_BACKEND=cli 인데 LLAMA_MODEL_PATH 미설정` | 5절 env | `export LLAMA_MODEL_PATH=...` |
| 출력 status가 `LLM_CALL_FAILED` | llama-cli 경로/모델 경로 | `LLAMA_CLI_PATH`·`LLAMA_MODEL_PATH` 실제 파일 확인 |
| 출력 status가 `LLM_OUTPUT_UNPARSEABLE`/오분류 | 모델이 미션명을 못 냄 | 1B→3B 상향, `LLAMA_TEMPERATURE=0`, `config/prompt.txt` 확인 |
| CPU만 쓰는 듯 느림 | CUDA 빌드/offload | `GGML_CUDA=ON` 빌드 여부, `LLAMA_N_GPU_LAYERS=99` |
| 응답이 너무 느림 | 모델 크기 | 1B Q4로 낮춰 측정, `LLAMA_N_PREDICT` 축소 |

> 참고: 출력 형식(JSON + mission_name/action/goal_pose 등 태그)은 코드가 보장하므로 절대 깨지지 않는다. 문제가 생기면 형식이 아니라 **분류 결과(mission_name)**나 **속도/메모리**를 본다.

## 참고 링크
- llama.cpp build: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
- Jetson power/performance: https://docs.nvidia.com/jetson/archives/r36.4.4/DeveloperGuide/SD/PlatformPowerAndPerformance.html
