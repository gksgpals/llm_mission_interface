# -*- coding: utf-8 -*-
"""
Llama 호출 래퍼.

역할: 완성된 프롬프트 문자열을 받아 모델의 "원본(raw) 응답 텍스트"를 돌려준다.
      JSON 파싱이나 미션 검증은 하지 않는다. (그건 classifier 의 몫)

백엔드 3종:
  - MockLlamaRunner   : 모델 없이 도는 결정론적 키워드 분류기 (로컬 개발/테스트 기본값)
  - LlamaCliRunner    : llama.cpp 의 llama-cli 서브프로세스 호출 (AGX 권장)
  - LlamaServerRunner : llama-server HTTP 호출 (native /completion 또는 OpenAI 호환)

환경변수로 선택: LLM_BACKEND = mock | cli | server   (기본 mock)

표준 라이브러리만 사용한다 (urllib, subprocess). 외부 패키지 불필요.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

# 프로젝트 루트를 import 경로에 추가 (스크립트로 직접 실행되는 경우 대비)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import ALLOWED_MISSIONS  # noqa: E402


class LlamaCallError(RuntimeError):
    """llama 실행/호출 자체가 실패했을 때 (바이너리 없음, 타임아웃, 서버 다운 등)."""


# ---------------------------------------------------------------------------
# 인터페이스
# ---------------------------------------------------------------------------
class LlamaRunner(ABC):
    """모든 백엔드 공통 인터페이스."""

    name = "base"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """프롬프트를 받아 모델 원본 응답 텍스트를 반환. 실패 시 LlamaCallError."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock 백엔드 (모델 없이 결정론적으로 도는 오프라인 개발/테스트용)
# ---------------------------------------------------------------------------
# 주의: 이것은 실제 의미 이해가 아니라 키워드 점수 기반의 근사치다.
#       실제 대회/AGX 에서는 LLM_BACKEND=cli 또는 server 로 진짜 Llama 를 쓴다.
_MOCK_KEYWORDS: Dict[str, List[Tuple[str, int]]] = {
    # 시퀀스(전체 구간)는 강한 가중치로 먼저 잡는다.
    "fire_response_sequence": [
        ("화재 시퀀스", 6), ("화재 대응", 5), ("화재 진압", 5),
        ("화재 전체", 5), ("화재 구간", 4), ("전체 미션", 2),
    ],
    "rescue_sequence": [
        ("구조 시퀀스", 6), ("인명 구조 전체", 6), ("재난 구조", 5),
        ("구조 전체", 5), ("구조 구간", 4), ("구조 시퀀", 5),
    ],
    "press_purifier_button": [
        ("정화 버튼", 5), ("공기정화", 4), ("공기 정화", 4),
        ("연기 제거", 4), ("환풍", 4), ("정화 시스템", 4),
        ("후드", 3), ("정화", 3), ("연기", 2),
    ],
    "count_fire_targets": [
        ("타겟 개수", 5), ("화재 규모", 5), ("몇 개", 4), ("몇 군데", 4),
        ("개수", 4), ("카운트", 4), ("규모", 2), ("몇", 2), ("세", 1),
    ],
    "open_door": [
        ("출입문", 5), ("문을 열", 5), ("문 열", 4), ("문 밀", 4),
        ("도어", 4), ("개방", 4), ("문", 2),
    ],
    "deliver_relief_item": [
        ("구호 물품", 5), ("구호물자", 5), ("구호품", 5), ("구조 물품", 4),
        ("구호", 3), ("가져다", 3), ("운반", 3), ("배달", 3),
        ("물자", 3), ("물품", 2), ("옮", 2),
    ],
    "inspect_rescue_rooms": [
        ("인명 구조 순찰", 6), ("구조 요구자", 5), ("요구조자", 5),
        ("사람이 있는지", 4), ("방을 조사", 4), ("생존자", 4),
        ("순찰", 3), ("방", 3), ("점검", 2),
    ],
    "go_to_finish": [
        ("finish 라인", 6), ("피니시", 5), ("결승", 5), ("종료 지점", 5),
        ("finish", 5), ("도착", 2), ("이동", 1),
    ],
}

# 구간별 하위 미션(비-시퀀스). 복합 명령 감지에 사용.
_FIRE_SUBS = ("press_purifier_button", "count_fire_targets", "open_door")
_RESCUE_SUBS = ("deliver_relief_item", "inspect_rescue_rooms", "go_to_finish")


class MockLlamaRunner(LlamaRunner):
    """모델 없이 키워드 점수로 mission_name 을 추정해 JSON 문자열로 반환."""

    name = "mock"

    def generate(self, prompt: str) -> str:
        # 프롬프트의 마지막 "Command:" 줄에서 사용자 명령을 추출한다.
        command = self._extract_command(prompt)
        mission = self._classify(command)
        return json.dumps({"mission_name": mission}, ensure_ascii=False)

    @staticmethod
    def _extract_command(prompt: str) -> str:
        marker = "Command:"
        idx = prompt.rfind(marker)
        if idx == -1:
            return prompt.strip()
        return prompt[idx + len(marker):].strip()

    @staticmethod
    def _classify(command: str) -> Optional[str]:
        text = command.lower()
        scores: Dict[str, int] = {}
        for mission, kws in _MOCK_KEYWORDS.items():
            score = 0
            for kw, weight in kws:
                if kw.lower() in text:
                    score += weight
            if score > 0:
                scores[mission] = score
        if not scores:
            return None

        # 복합(시퀀스) 감지: 같은 구간의 하위 미션이 2개 이상 언급되면 *_sequence 로.
        # (예: "공기정화 버튼 누르고 화재 개수 확인한 뒤 문 열어" → fire_response_sequence)
        fire_hits = [m for m in _FIRE_SUBS if m in scores]
        rescue_hits = [m for m in _RESCUE_SUBS if m in scores]
        fire_compound = len(fire_hits) >= 2
        rescue_compound = len(rescue_hits) >= 2
        if fire_compound and rescue_compound:
            return None  # 두 구간이 섞임 → 모호
        if fire_compound:
            return "fire_response_sequence"
        if rescue_compound:
            return "rescue_sequence"

        # 단일 명령: 최고 점수의 유일값
        best = max(scores.values())
        winners = [m for m, s in scores.items() if s == best]
        if len(winners) != 1:
            return None  # 동점 → 모호 → null
        winner = winners[0]
        return winner if winner in ALLOWED_MISSIONS else None


class ScriptedRunner(LlamaRunner):
    """테스트 전용: 미리 정해둔 원본 응답을 그대로 반환한다.

    responses 가 문자열이면 항상 그 값을, 리스트면 호출 순서대로 소비한다.
    'RAISE' 로 지정하면 LlamaCallError 를 던진다.
    """

    name = "scripted"

    def __init__(self, responses):
        self._responses = responses
        self._i = 0

    def generate(self, prompt: str) -> str:
        if isinstance(self._responses, str):
            resp = self._responses
        else:
            resp = self._responses[min(self._i, len(self._responses) - 1)]
            self._i += 1
        if resp == "RAISE":
            raise LlamaCallError("scripted failure")
        return resp


# ---------------------------------------------------------------------------
# llama-cli 백엔드 (llama.cpp 서브프로세스)
# ---------------------------------------------------------------------------
class LlamaCliRunner(LlamaRunner):
    name = "cli"

    def __init__(
        self,
        cli_path: str,
        model_path: str,
        n_predict: int = 64,
        temperature: float = 0.0,
        n_gpu_layers: int = 999,
        timeout_s: float = 60.0,
        extra_args: Optional[List[str]] = None,
    ):
        self.cli_path = cli_path
        self.model_path = model_path
        self.n_predict = n_predict
        self.temperature = temperature
        self.n_gpu_layers = n_gpu_layers
        self.timeout_s = timeout_s
        self.extra_args = extra_args or []

    def _build_cmd(self, prompt: str) -> List[str]:
        cmd = [
            self.cli_path,
            "-m", self.model_path,
            "-p", prompt,
            "-n", str(self.n_predict),
            "--temp", str(self.temperature),
            "-ngl", str(self.n_gpu_layers),
            "-no-cnv",              # 대화(conversation) 모드 비활성화: 입력 대기 방지
            "--no-display-prompt",  # 출력에서 프롬프트 에코 제거
        ]
        cmd.extend(self.extra_args)
        return cmd

    def generate(self, prompt: str) -> str:
        cmd = self._build_cmd(prompt)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as exc:
            raise LlamaCallError(f"llama-cli 실행 파일을 찾을 수 없습니다: {self.cli_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LlamaCallError(f"llama-cli 타임아웃({self.timeout_s}s)") from exc
        except Exception as exc:  # noqa: BLE001
            raise LlamaCallError(f"llama-cli 실행 오류: {exc}") from exc

        if proc.returncode != 0:
            raise LlamaCallError(
                f"llama-cli 비정상 종료(code={proc.returncode}): {proc.stderr.strip()[:400]}"
            )
        return proc.stdout


# ---------------------------------------------------------------------------
# llama-server 백엔드 (HTTP)
# ---------------------------------------------------------------------------
class LlamaServerRunner(LlamaRunner):
    name = "server"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        mode: str = "native",   # "native" (/completion) | "openai" (/v1/chat/completions)
        n_predict: int = 64,
        temperature: float = 0.0,
        timeout_s: float = 60.0,
        model: str = "local",
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.n_predict = n_predict
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.model = model
        self.api_key = api_key

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise LlamaCallError(f"llama-server 연결 실패({url}): {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LlamaCallError(f"llama-server 호출 오류: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise LlamaCallError(f"llama-server 응답이 JSON 이 아님: {body[:400]}") from exc

    def generate(self, prompt: str) -> str:
        if self.mode == "openai":
            url = f"{self.base_url}/v1/chat/completions"
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.n_predict,
            }
            result = self._post(url, payload)
            try:
                return result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LlamaCallError(f"예상치 못한 OpenAI 응답 형식: {result}") from exc

        # native /completion
        url = f"{self.base_url}/completion"
        payload = {
            "prompt": prompt,
            "n_predict": self.n_predict,
            "temperature": self.temperature,
            "cache_prompt": True,
            "stop": ["\n\n", "Command:"],
        }
        result = self._post(url, payload)
        content = result.get("content")
        if content is None:
            raise LlamaCallError(f"예상치 못한 llama-server 응답 형식: {result}")
        return content


# ---------------------------------------------------------------------------
# 팩토리 (환경변수 기반)
# ---------------------------------------------------------------------------
def build_runner_from_env() -> LlamaRunner:
    """LLM_BACKEND 환경변수에 따라 러너를 생성한다. 기본값은 mock."""
    backend = os.environ.get("LLM_BACKEND", "mock").strip().lower()

    if backend == "mock":
        return MockLlamaRunner()

    if backend == "cli":
        cli_path = os.environ.get("LLAMA_CLI_PATH", "llama-cli")
        model_path = os.environ.get("LLAMA_MODEL_PATH")
        if not model_path:
            raise LlamaCallError("LLM_BACKEND=cli 인데 LLAMA_MODEL_PATH 가 설정되지 않았습니다.")
        extra = os.environ.get("LLAMA_CLI_EXTRA_ARGS", "").split()
        return LlamaCliRunner(
            cli_path=cli_path,
            model_path=model_path,
            n_predict=int(os.environ.get("LLAMA_N_PREDICT", "64")),
            temperature=float(os.environ.get("LLAMA_TEMPERATURE", "0.0")),
            n_gpu_layers=int(os.environ.get("LLAMA_N_GPU_LAYERS", "999")),
            timeout_s=float(os.environ.get("LLAMA_TIMEOUT_S", "60")),
            extra_args=extra or None,
        )

    if backend == "server":
        return LlamaServerRunner(
            base_url=os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8080"),
            mode=os.environ.get("LLAMA_SERVER_MODE", "native"),
            n_predict=int(os.environ.get("LLAMA_N_PREDICT", "64")),
            temperature=float(os.environ.get("LLAMA_TEMPERATURE", "0.0")),
            timeout_s=float(os.environ.get("LLAMA_TIMEOUT_S", "60")),
            model=os.environ.get("LLAMA_MODEL", "local"),
            api_key=os.environ.get("LLAMA_API_KEY") or None,
        )

    raise LlamaCallError(f"알 수 없는 LLM_BACKEND: {backend!r} (mock|cli|server 중 하나)")


if __name__ == "__main__":
    # 간단 수동 확인: python3 src/llama_runner.py "공기정화 버튼 눌러"
    runner = build_runner_from_env()
    text = sys.argv[1] if len(sys.argv) > 1 else "공기정화 버튼을 눌러."
    print(f"[backend={runner.name}]")
    print(runner.generate(f"Command: {text}"))
