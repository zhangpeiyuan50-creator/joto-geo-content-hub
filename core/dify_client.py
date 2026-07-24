from __future__ import annotations

import json
import os
import re
from typing import Any

import requests


class DifyClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        workflow_path: str,
        response_mode: str,
        user: str,
        topic_input_key: str,
        timeout: int = 300,
        use_env_proxy: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
        self.response_mode = response_mode
        self.user = user
        self.topic_input_key = topic_input_key
        self.timeout = timeout

        self.session = requests.Session()
        self.session.trust_env = use_env_proxy

        dify_proxy = os.getenv("DIFY_PROXY")
        if dify_proxy:
            self.session.proxies.update({"http": dify_proxy, "https": dify_proxy})

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DifyClient":
        api_key_env = str(config.get("api_key_env") or "DIFY_API_KEY")
        api_key = os.getenv(api_key_env)
        if not api_key and api_key_env == "DIFY_API_KEY_FASIUM":
            api_key = os.getenv("DIFY_API_KEY")
        if not api_key:
            raise RuntimeError(f"{api_key_env} is missing. Please set it in .env")

        return cls(
            api_key=api_key,
            base_url=config.get("base_url", "https://api.dify.ai/v1"),
            workflow_path=config.get("workflow_path", "/workflows/run"),
            response_mode=config.get("response_mode", "streaming"),
            user=config.get("user", "fasium_geo_auto"),
            topic_input_key=config.get("topic_input_key", "topic"),
            timeout=config.get("timeout_seconds", 300),
            use_env_proxy=config.get("use_env_proxy", False),
        )

    def run_workflow(self, topic: str) -> dict[str, Any]:
        if self.response_mode == "streaming":
            return self._run_workflow_streaming(topic)
        return self._run_workflow_blocking(topic)

    def _build_request(self, topic: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        url = f"{self.base_url}{self.workflow_path}"
        payload = {
            "inputs": {self.topic_input_key: topic},
            "response_mode": self.response_mode,
            "user": self.user,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return url, payload, headers

    def _run_workflow_blocking(self, topic: str) -> dict[str, Any]:
        url, payload, headers = self._build_request(topic)
        response = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return self._normalize_response(response.json())

    def _run_workflow_streaming(self, topic: str) -> dict[str, Any]:
        url, payload, headers = self._build_request(topic)
        response = self.session.post(
            url,
            json=payload,
            headers=headers,
            timeout=(30, self.timeout),
            stream=True,
        )
        response.raise_for_status()

        final_payload: dict[str, Any] | None = None
        streamed_text: list[str] = []

        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue

            raw_data = line.removeprefix("data:").strip()
            if raw_data == "[DONE]":
                break

            try:
                event_payload = json.loads(raw_data)
            except json.JSONDecodeError:
                continue

            event = event_payload.get("event")
            if event in {"text_chunk", "agent_message", "message"}:
                text = event_payload.get("data", {}).get("text") or event_payload.get("answer")
                if text:
                    streamed_text.append(str(text))

            if event == "workflow_finished":
                final_payload = event_payload
                break

            if event == "error":
                message = event_payload.get("message") or event_payload.get("data", {}).get("message")
                raise RuntimeError(f"Dify streaming error: {message or event_payload}")

        if final_payload:
            return self._normalize_response(final_payload)

        if not streamed_text:
            raise RuntimeError("Dify workflow ended without a final result or text output")
        return self._normalize_response({"outputs": {"text": "".join(streamed_text)}})

    def _normalize_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data", {})
        status = str(data.get("status") or payload.get("status") or "").lower()
        if status in {"failed", "stopped", "cancelled", "canceled"}:
            error = data.get("error") or payload.get("error") or "unknown workflow error"
            raise RuntimeError(f"Dify workflow {status}: {error}")

        outputs = data.get("outputs")
        if outputs is None:
            outputs = payload.get("outputs", payload)

        fallback_text = clean_model_text(self._pick(outputs, ["text", "output", "result", "answer"]))
        parsed_outputs = parse_outputs_dict(outputs)
        if not parsed_outputs:
            parsed_outputs = parse_text_outputs(fallback_text)

        return {
            "zhihu": clean_model_text(
                parsed_outputs.get("zhihu", "")
                or self._pick(outputs, ["zhihu", "zhihu_md", "zhihu_markdown", "知乎版本", "text"])
                or fallback_text
            ),
            "csdn": clean_model_text(
                parsed_outputs.get("csdn", "")
                or self._pick(outputs, ["csdn", "csdn_md", "csdn_markdown", "CSDN版本", "text_2"])
            ),
            "sohu": clean_model_text(
                parsed_outputs.get("sohu", "")
                or self._pick(
                    outputs,
                    ["sohu", "souhu", "SOUHU", "sohu_md", "sohu_markdown", "搜狐", "搜狐号", "搜狐版本", "text_1"],
                )
            ),
            "cover_prompt": clean_model_text(
                parsed_outputs.get("cover_prompt", "")
                or self._pick(outputs, ["cover_prompt", "cover", "image_prompt", "封面图Prompt", "text_3"])
            ),
            "raw_response": payload,
        }

    @staticmethod
    def _pick(outputs: Any, keys: list[str]) -> str:
        if isinstance(outputs, str):
            return outputs
        if not isinstance(outputs, dict):
            return ""

        for key in keys:
            value = outputs.get(key)
            if value is not None:
                return str(value)
        return ""


def clean_model_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^\s*<think>.*", "", text, flags=re.S | re.I)
    return text.strip()


def parse_text_outputs(text: str) -> dict[str, str]:
    text = clean_model_text(text)
    if not text:
        return {}

    parsed_json = parse_json_text(text)
    if parsed_json:
        return parsed_json

    return parse_section_text(text)


def parse_outputs_dict(outputs: Any) -> dict[str, str]:
    if not isinstance(outputs, dict):
        return {}

    parsed: dict[str, str] = {}
    for value in outputs.values():
        if not isinstance(value, str):
            continue
        value_outputs = parse_text_outputs(value)
        for key, text in value_outputs.items():
            if text and len(text) > len(parsed.get(key, "")):
                parsed[key] = text
    return parsed


def parse_json_text(text: str) -> dict[str, str]:
    candidates = [text]
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S | re.I)
    if match:
        candidates.insert(0, match.group(1))
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.insert(0, text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return {
                "zhihu": str(payload.get("zhihu") or payload.get("知乎版本") or ""),
                "csdn": str(payload.get("csdn") or payload.get("CSDN版本") or ""),
                "sohu": str(
                    payload.get("sohu")
                    or payload.get("souhu")
                    or payload.get("SOUHU")
                    or payload.get("搜狐")
                    or payload.get("搜狐号")
                    or payload.get("搜狐版本")
                    or ""
                ),
                "cover_prompt": str(payload.get("cover_prompt") or payload.get("封面图Prompt") or ""),
            }
    return {}


def parse_section_text(text: str) -> dict[str, str]:
    section_patterns = {
        "zhihu": r"(?:^|\n)\s*#{0,3}\s*(?:知乎版本|zhihu)\s*[:：]?\s*\n",
        "csdn": r"(?:^|\n)\s*#{0,3}\s*(?:CSDN版本|csdn)\s*[:：]?\s*\n",
        "sohu": r"(?:^|\n)\s*#{0,3}\s*(?:搜狐版本|搜狐号|搜狐|SOUHU|sohu)\s*[:：]?\s*\n",
        "cover_prompt": r"(?:^|\n)\s*#{0,3}\s*(?:封面图Prompt|cover_prompt|封面Prompt)\s*[:：]?\s*\n",
    }
    matches: list[tuple[int, str, re.Match[str]]] = []
    for key, pattern in section_patterns.items():
        match = re.search(pattern, text, flags=re.I)
        if match:
            matches.append((match.start(), key, match))

    if not matches:
        return {}

    matches.sort(key=lambda item: item[0])
    result: dict[str, str] = {}
    for index, (_, key, match) in enumerate(matches):
        start = match.end()
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        result[key] = text[start:end].strip()
    return result
