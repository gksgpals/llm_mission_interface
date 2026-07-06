# -*- coding: utf-8 -*-
"""
Classifier: 자연어 명령 → (프롬프트 구성) → Llama 호출 → mission_name 추출.

핵심 원칙:
  - LLM 은 mission_name 만 정한다. action/target_refs/goal_pose 는 절대 신뢰하지 않는다.
  - 소형 모델(1B/3B)은 JSON 을 흔들리게 낼 수 있으므로, 파싱을 최대한 관대하게 한다.
  - 그래도 못 뽑으면 '파싱 실패'로 표시만 하고, 판단(오류 JSON 생성)은 상위 계층에 맡긴다.

이 모듈은 원본 응답을 절대 예외로 흘리지 않고 ClassificationResult 로 정규화한다.
(단, 백엔드 호출 실패 LlamaCallError 는 상위에서 구분 처리하도록 그대로 전파한다.)
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.mission_spec import ALLOWED_MISSIONS, MISSION_METADATA  # noqa: E402
from src.llama_runner import LlamaRunner  # noqa: E402

_PROMPT_PATH = os.path.join(_ROOT, "config", "prompt.txt")


@dataclass
class ClassificationResult:
    """분류 결과.

    mission_name : 추출된 미션명(문자열) 또는 None
    parse_ok     : 응답에서 mission_name 필드/값을 뽑아냈는지 여부
                   (False 면 LLM 출력이 아예 해석 불가 → LLM_OUTPUT_UNPARSEABLE)
    raw          : 로깅/디버깅용 모델 원본 응답
    """
    mission_name: Optional[str]
    parse_ok: bool
    raw: str


class Classifier:
    def __init__(self, runner: LlamaRunner, prompt_path: str = _PROMPT_PATH):
        self.runner = runner
        self._prompt_template = self._load_prompt(prompt_path)
        self._catalog = self._build_catalog()

    # ----- 프롬프트 구성 -------------------------------------------------
    @staticmethod
    def _load_prompt(path: str) -> str:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    @staticmethod
    def _build_catalog() -> str:
        lines = []
        for name in ALLOWED_MISSIONS:
            meta = MISSION_METADATA.get(name, {})
            desc = meta.get("description", "")
            examples = meta.get("examples", []) or []
            ex = " / ".join(str(e) for e in examples[:3])
            lines.append(f'- "{name}": {desc}  (예: {ex})')
        return "\n".join(lines)

    def build_prompt(self, text: str) -> str:
        return (
            self._prompt_template
            .replace("{{MISSION_CATALOG}}", self._catalog)
            .replace("{{USER_COMMAND}}", text.strip())
        )

    # ----- 분류 ----------------------------------------------------------
    def classify(self, text: str) -> ClassificationResult:
        """text 를 분류. 백엔드 실패 시 LlamaCallError 가 그대로 전파된다."""
        prompt = self.build_prompt(text)
        raw = self.runner.generate(prompt)
        mission, parse_ok = self._parse_mission(raw)
        return ClassificationResult(mission_name=mission, parse_ok=parse_ok, raw=raw)

    # ----- 관대한 파싱 ---------------------------------------------------
    @staticmethod
    def _parse_mission(raw: str):
        """모델 원본 응답에서 mission_name 을 최대한 관대하게 뽑는다.

        반환: (mission_name_or_None, parse_ok)
        """
        if raw is None:
            return None, False
        text = raw.strip()
        if not text:
            return None, False

        # 1) 코드펜스 제거
        cleaned = re.sub(r"^```[a-zA-Z]*", "", text.strip())
        cleaned = cleaned.replace("```", "").strip()

        # 2) 전체 문자열 자체가 JSON 인 경우
        obj = _try_json(cleaned)
        if obj is None:
            # 3) 본문에서 첫 번째 {...} 블록을 찾아 파싱
            snippet = _first_json_object(cleaned)
            if snippet is not None:
                obj = _try_json(snippet)

        if isinstance(obj, dict) and "mission_name" in obj:
            value = obj["mission_name"]
            if value is None:
                return None, True  # 명시적 null → 파싱 성공, 미션 없음
            if isinstance(value, str):
                v = value.strip()
                # 유효 미션이면 그대로, 아니면 문자열로 통과(상위에서 UNSUPPORTED 처리)
                return (v or None), True
            # 숫자/불리언 등 이상 타입 → 미션 없음으로 취급하되 파싱은 성공
            return None, True

        # 4) JSON 실패: 원문에 허용 미션명이 '정확히 하나' 등장하면 그것으로 간주
        found = [m for m in ALLOWED_MISSIONS if m in cleaned]
        if len(found) == 1:
            return found[0], True

        # 5) 그래도 못 뽑음 → 파싱 실패
        return None, False


def _try_json(s: str):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _first_json_object(s: str) -> Optional[str]:
    """문자열에서 처음 등장하는 균형 잡힌 {...} 블록을 반환."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


if __name__ == "__main__":
    from src.llama_runner import build_runner_from_env

    clf = Classifier(build_runner_from_env())
    sample = sys.argv[1] if len(sys.argv) > 1 else "환풍 장치를 켜."
    print(clf.classify(sample))
