# -*- coding: utf-8 -*-
"""
HTTP API 서버 (ROS2 bridge 가 읽는 출력 계약).

엔드포인트:
    POST /mission
        요청 : {"text": "공기정화 버튼을 눌러."}
        응답 : 최종 Mission JSON (항상 동일 스키마)
        HTTP : 항상 200 을 반환한다. 성공/실패 의미는 본문의 "status" 로 전달한다.
               (ROS2 bridge 는 HTTP 코드가 아니라 status 필드를 읽으면 된다.)

    GET /health
        응답 : {"status":"ok","backend":"mock|cli|server"}

표준 라이브러리(http.server)만 사용 → AGX 에서 pip 설치 없이 바로 구동 가능.
Flask/FastAPI 가 설치돼 있으면 그걸 써도 되지만, 기본 구동은 의존성 0 이다.

실행:
    python3 -m src.api_server                # 0.0.0.0:8000
    HOST=127.0.0.1 PORT=9000 python3 -m src.api_server
"""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import ErrorCode  # noqa: E402
from src.canonicalizer import make_error, new_request_id  # noqa: E402
from src.mission_service import MissionService, _configure_logging  # noqa: E402

logger = logging.getLogger("api_server")

# 프로세스당 하나의 서비스 인스턴스 (모델 로딩 비용 절감)
_SERVICE = None


def get_service() -> MissionService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = MissionService()
    return _SERVICE


class MissionHandler(BaseHTTPRequestHandler):
    server_version = "MissionLLM/1.0"

    # 기본 로깅을 우리 logger 로 라우팅
    def log_message(self, fmt, *args):  # noqa: A003
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/health":
            backend = os.environ.get("LLM_BACKEND", "mock")
            self._send_json({"status": "ok", "backend": backend})
        else:
            self._send_json({"error": "not_found", "path": self.path}, code=404)

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/mission":
            self._send_json({"error": "not_found", "path": self.path}, code=404)
            return

        # 본문 읽기
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""

        # JSON 파싱 실패 → 계약 유지한 채 INVALID_REQUEST
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(
                make_error(
                    ErrorCode.INVALID_REQUEST,
                    new_request_id(),
                    message="요청 본문이 유효한 JSON 이 아닙니다.",
                )
            )
            return

        text = payload.get("text") if isinstance(payload, dict) else None
        result = get_service().handle(text)  # text 가 없거나 이상해도 handle 이 처리
        self._send_json(result)


def run() -> None:
    _configure_logging()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    # 모델을 미리 로딩(첫 요청 지연 방지) — 실패해도 서버는 뜬다.
    try:
        get_service()
    except Exception as exc:  # noqa: BLE001
        logger.warning("서비스 초기화 경고: %s", exc)

    httpd = ThreadingHTTPServer((host, port), MissionHandler)
    logger.info("Mission API 서버 시작: http://%s:%d  (POST /mission, GET /health)", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("서버 종료")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
