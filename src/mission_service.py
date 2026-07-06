# -*- coding: utf-8 -*-
"""
MissionService: 자연어 → 최종 Mission JSON 파이프라인의 오케스트레이터.

흐름:
    text
      → Classifier(LLM) : mission_name 분류
      → Canonicalizer   : action/target_refs 고정
      → GoalPoseResolver: 좌표 주입 시도 (없으면 partial)
      → Validator       : 계약 최종 검증
      → 최종 JSON

불변식(가장 중요):
    handle() 은 어떤 입력에도 예외를 밖으로 던지지 않는다.
    항상 계약을 지키는 Mission JSON(dict) 을 돌려준다.

FIFO/CLI:
    python3 -m src.mission_service "공기정화 버튼을 눌러."   # 인자 1개 처리
    echo "문 열어" | python3 -m src.mission_service          # stdin/FIFO 스트림 처리
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import ErrorCode, get_target_refs, is_supported  # noqa: E402
from src.canonicalizer import canonicalize, make_error, new_request_id  # noqa: E402
from src.classifier import Classifier  # noqa: E402
from src.goal_pose_resolver import GoalPoseResolver, NullGoalPoseResolver  # noqa: E402
from src.llama_runner import LlamaCallError, build_runner_from_env  # noqa: E402
from src.validator import validate  # noqa: E402

logger = logging.getLogger("mission_service")


class MissionService:
    def __init__(
        self,
        classifier: Optional[Classifier] = None,
        resolver: Optional[GoalPoseResolver] = None,
    ):
        # classifier 가 없으면 환경변수 기반으로 Llama 러너를 구성한다 (기본 mock).
        self.classifier = classifier or Classifier(build_runner_from_env())
        self.resolver = resolver or NullGoalPoseResolver()

    def handle(self, text: object) -> Dict[str, object]:
        """자연어 text 를 받아 최종 Mission JSON(dict) 을 반환. 절대 예외를 던지지 않는다."""
        request_id = new_request_id()
        t0 = time.time()
        out: Dict[str, object]
        try:
            # 0) 입력 검증
            if not isinstance(text, str) or not text.strip():
                out = make_error(ErrorCode.INVALID_REQUEST, request_id)
                return self._finalize(request_id, text, out, t0)

            # 1) LLM 분류
            try:
                result = self.classifier.classify(text)
            except LlamaCallError as exc:
                out = make_error(ErrorCode.LLM_CALL_FAILED, request_id, message=str(exc))
                return self._finalize(request_id, text, out, t0)

            # 2) 파싱 실패 → LLM_OUTPUT_UNPARSEABLE
            if not result.parse_ok:
                out = make_error(ErrorCode.LLM_OUTPUT_UNPARSEABLE, request_id)
                logger.debug("unparseable raw=%r", result.raw)
                return self._finalize(request_id, text, out, t0)

            # 3) 미지원/모호 → UNSUPPORTED_OR_AMBIGUOUS_COMMAND
            if not is_supported(result.mission_name):
                out = make_error(ErrorCode.UNSUPPORTED_OR_AMBIGUOUS_COMMAND, request_id)
                return self._finalize(request_id, text, out, t0)

            mission_name = str(result.mission_name)

            # 4) goal_pose 조회 (실패/오류는 partial 로 강등하여 계약 안정성 유지)
            pose = None
            try:
                pose = self.resolver.resolve(mission_name, get_target_refs(mission_name))
            except Exception as exc:  # noqa: BLE001
                logger.warning("goal_pose resolve 실패(partial 로 강등): %s", exc)
                pose = None

            # 5) 정규화(계약 강제)
            try:
                out = canonicalize(mission_name, request_id, goal_pose=pose)
            except ValueError as exc:
                # 좌표 형식이 잘못된 경우: 좌표 없이 partial 로 처리
                logger.warning("goal_pose 형식 오류(partial 로 강등): %s", exc)
                out = canonicalize(mission_name, request_id, goal_pose=None)

            # 6) 최종 검증 (마지막 방어선)
            ok, errors = validate(out)
            if not ok:
                logger.error("최종 검증 실패, 오류 JSON 으로 교체: %s", errors)
                out = make_error(
                    ErrorCode.INTERNAL_ERROR, request_id, message="; ".join(errors)
                )
            return self._finalize(request_id, text, out, t0)

        except Exception as exc:  # noqa: BLE001  (최후의 안전망)
            logger.exception("예상치 못한 내부 오류")
            out = make_error(ErrorCode.INTERNAL_ERROR, request_id, message=str(exc))
            return self._finalize(request_id, text, out, t0)

    def handle_json(self, text: object) -> str:
        """handle 결과를 JSON 문자열로 직렬화."""
        return json.dumps(self.handle(text), ensure_ascii=False)

    def _finalize(self, request_id, text, out, t0) -> Dict[str, object]:
        latency_ms = int((time.time() - t0) * 1000)
        err = out.get("error")
        code = err.get("code") if isinstance(err, dict) else None
        logger.info(
            "req=%s status=%s mission=%s code=%s latency_ms=%d text=%r",
            request_id, out.get("status"), out.get("mission_name"), code, latency_ms,
            (text if isinstance(text, str) else str(text))[:80],
        )
        return out


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )


def _main() -> None:
    _configure_logging()
    service = MissionService()

    if len(sys.argv) > 1:
        # 인자로 준 한 문장 처리
        text = " ".join(sys.argv[1:])
        print(service.handle_json(text))
        return

    # stdin / FIFO 스트림: 한 줄에 명령 하나, 결과 JSON 을 한 줄로 출력
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        print(service.handle_json(line), flush=True)


if __name__ == "__main__":
    _main()
