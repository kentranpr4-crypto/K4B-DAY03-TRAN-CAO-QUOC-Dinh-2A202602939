"""
🔌 MULTI-PROVIDER LLM ADAPTER (Google Gemini, OpenAI, Anthropic Claude & Offline Mock)
Hỗ trợ Native Tool Calling nhiều lượt (ReAct history) và chuyển đổi linh hoạt qua biến môi trường LLM_PROVIDER.

Định dạng history chung (provider-neutral) truyền vào generate_with_tools(..., history=...):
[
  {
    "thought": "...",
    "raw": {"provider": "anthropic" | "gemini", "content": <nội dung gốc model trả về>} hoặc None,
    "tool_calls": [{"id": "...", "name": "...", "arguments": {...}, "observation": {...}}]
  },
  ...
]

Kết quả trả về của generate_with_tools():
  {"type": "text", "content": "...", "thought": "..."}
  {"type": "tool_call", "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}], "thought": "...", "raw": {...} | None}
"""

import os
import re
import sys
import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

class BaseLLMProvider:
    """Interface cơ sở cho các LLM Provider hỗ trợ Native Tool Calling"""
    def generate(self, prompt: str, system_prompt: str = "") -> str:
        raise NotImplementedError

    def generate_with_tools(self, prompt: str, tools_schema: List[Dict[str, Any]], system_prompt: str = "",
                            history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        raise NotImplementedError


def _observation_json(call: Dict[str, Any]) -> str:
    return json.dumps(call.get("observation", {}), ensure_ascii=False)


class MockOfflineProvider(BaseLLMProvider):
    """Offline Mock Provider dùng để chạy thử mà không tốn API Key"""
    def __init__(self):
        self.model_name = "Offline-Mock-Model-2026"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return f"[Mock Chatbot Response]: Xin chào! Tôi đã nhận được câu hỏi '{prompt}'. (Chế độ Chatbot không có Tool tra cứu dữ liệu thời gian thực)."

    @staticmethod
    def _tool_call(name: str, args: Dict[str, Any], thought: str, step: int) -> Dict[str, Any]:
        return {
            "type": "tool_call",
            "tool_calls": [{"id": f"mock_{name}_{step}", "name": name, "arguments": args}],
            "thought": thought,
            "raw": None
        }

    def generate_with_tools(self, prompt: str, tools_schema: List[Dict[str, Any]], system_prompt: str = "",
                            history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        history = history or []
        if history:
            return self._next_step(prompt, history)

        prompt_lower = prompt.lower()
        patient = re.search(r"BN\d+", prompt, re.IGNORECASE)
        slot = re.search(r"\d{1,2}:\d{2}\s+(?:ngày\s+)?\d{1,2}/\d{1,2}/\d{4}", prompt)
        doctor = re.search(r"((?:TS|ThS|BS)[\w.]*\s+[A-ZÀ-Ỹ][^\s,.]*(?:\s+[A-ZÀ-Ỹ][^\s,.]*)+)", prompt)
        date = re.search(r"\d{1,2}/\d{1,2}/\d{4}", prompt)
        specialty = re.search(r"khoa\s+([^\s,.]+(?:\s+[^\s,.\d]+)*?)(?=\s+(?:ngày|còn|vào|lúc|và)|[,.]|$)", prompt, re.IGNORECASE)

        # Mô phỏng nhận diện intent gọi Tool
        if "đặt lịch" in prompt_lower and patient and doctor and slot:
            args = {"patient_id": patient.group(0).upper(), "doctor_name": doctor.group(1), "datetime_str": slot.group(0)}
            return self._tool_call("schedule_appointment", args,
                                   f"Khách hàng yêu cầu đặt lịch khám cho bệnh nhân {args['patient_id']}. Tôi sẽ gọi tool schedule_appointment.", 0)
        elif specialty:
            args = {"specialty": specialty.group(1).strip(), "date": date.group(0) if date else ""}
            return self._tool_call("doctor_schedule_query", args,
                                   f"Khách hàng cần thông tin lịch bác sĩ khoa {args['specialty']}. Tôi sẽ gọi tool doctor_schedule_query.", 0)
        else:
            return {
                "type": "text",
                "content": "[Mock Agent Response]: Xin chào! Trước khi khám sức khỏe tổng quát, bạn nên nhịn ăn 6–8 tiếng, mang theo CCCD/thẻ bảo hiểm và các kết quả khám trước đây (nếu có).",
                "thought": "Câu hỏi chung về chuẩn bị đi khám, trả lời trực tiếp không cần gọi Tool."
            }

    def _next_step(self, prompt: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Mô phỏng lượt suy luận tiếp theo dựa trên Observation gần nhất"""
        last_call = history[-1]["tool_calls"][-1]
        obs = last_call.get("observation", {})
        patient = re.search(r"BN\d+", prompt, re.IGNORECASE)

        if (last_call["name"] == "doctor_schedule_query" and obs.get("status") == "SUCCESS"
                and "đặt lịch" in prompt.lower() and patient):
            slots = [(s, d["doctor_name"]) for d in obs.get("doctors", []) for s in d["available_slots"]]
            slot, doctor_name = min(slots, key=lambda x: datetime.strptime(x[0], "%H:%M %d/%m/%Y"))
            args = {"patient_id": patient.group(0).upper(), "doctor_name": doctor_name, "datetime_str": slot}
            return self._tool_call("schedule_appointment", args,
                                   f"Khung giờ sớm nhất là {slot} với {doctor_name}. Tôi sẽ đặt lịch cho {args['patient_id']}.", len(history))

        return {
            "type": "text",
            "content": f"[Mock Agent Response]: {obs.get('message') or json.dumps(obs, ensure_ascii=False)}",
            "thought": "Đã có đủ dữ liệu từ Observation, tổng hợp câu trả lời cuối cùng."
        }


class GeminiProvider(BaseLLMProvider):
    """Google Gemini Provider (Native Tool Calling với Google GenAI SDK)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "gemini-2.5-flash"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            return "[Gemini Error]: Chưa cấu hình GEMINI_API_KEY trong file .env! Đang sử dụng chế độ Mock."
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            contents = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            response = client.models.generate_content(model=self.model_name, contents=contents)
            return response.text
        except Exception as e:
            return f"[Gemini Exception]: {str(e)}"

    def generate_with_tools(self, prompt: str, tools_schema: List[Dict[str, Any]], system_prompt: str = "",
                            history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            print("ℹ️ [Gemini Provider]: Chưa tìm thấy GEMINI_API_KEY hợp lệ. Tự động chuyển sang Mock Offline.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)

            # Chuẩn hóa function declarations cho Gemini SDK
            function_declarations = []
            for tool in tools_schema:
                # Bỏ qua các tool schema chưa được định nghĩa hoàn chỉnh
                if not tool.get("name") or not tool.get("parameters"):
                    continue
                function_declarations.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {})
                })

            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
                tools=[{"function_declarations": function_declarations}] if function_declarations else None,
                temperature=0.2
            )

            # Dựng lại hội thoại: câu hỏi -> (function_call -> function_response) x N
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
            for turn in history or []:
                raw = turn.get("raw") or {}
                if raw.get("provider") == "gemini":
                    contents.append(raw["content"])
                else:
                    contents.append(types.Content(role="model", parts=[
                        types.Part.from_function_call(name=c["name"], args=c["arguments"]) for c in turn["tool_calls"]
                    ]))
                contents.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=c["name"], response=c.get("observation", {}))
                    for c in turn["tool_calls"]
                ]))

            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )

            # Kiểm tra xem Gemini có trả về Tool Call không
            if response.function_calls:
                calls = [{
                    "id": call.id or f"gemini_{call.name}_{i}",
                    "name": call.name,
                    "arguments": dict(call.args) if call.args else {}
                } for i, call in enumerate(response.function_calls)]
                summary = ", ".join(f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in calls)
                return {
                    "type": "tool_call",
                    "tool_calls": calls,
                    "thought": f"Gemini quyết định gọi công cụ: {summary}",
                    "raw": {"provider": "gemini", "content": response.candidates[0].content}
                }
            else:
                return {
                    "type": "text",
                    "content": response.text or "",
                    "thought": "Gemini phản hồi trực tiếp bằng văn bản (không cần gọi công cụ)."
                }

        except Exception as e:
            print(f"⚠️ [Gemini API Warning]: Không thể kết nối live API ({str(e)}). Tự động fallback về Mock.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)


class OpenAIProvider(BaseLLMProvider):
    """OpenAI Provider (Native Tool Calling với OpenAI SDK)"""
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model_name = model or os.getenv("LLM_MODEL") or "gpt-4o-mini"

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self.api_key or self.api_key == "your_openai_api_key_here":
            return "[OpenAI Error]: Chưa cấu hình OPENAI_API_KEY trong file .env! Đang sử dụng chế độ Mock."
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            response = client.chat.completions.create(model=self.model_name, messages=messages)
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"[OpenAI Exception]: {str(e)}"

    def generate_with_tools(self, prompt: str, tools_schema: List[Dict[str, Any]], system_prompt: str = "",
                            history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not self.api_key or self.api_key == "your_openai_api_key_here":
            print("ℹ️ [OpenAI Provider]: Chưa tìm thấy OPENAI_API_KEY hợp lệ. Tự động chuyển sang Mock Offline.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)

            tools = []
            for tool in tools_schema:
                if not tool.get("name"):
                    continue
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {})
                    }
                })

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Dựng lại hội thoại: assistant tool_calls -> tool results
            for turn in history or []:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}
                    } for c in turn["tool_calls"]]
                })
                for c in turn["tool_calls"]:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": _observation_json(c)})

            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None
            )

            msg = response.choices[0].message
            if msg.tool_calls:
                calls = [{
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": json.loads(call.function.arguments) if call.function.arguments else {}
                } for call in msg.tool_calls]
                summary = ", ".join(f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in calls)
                return {
                    "type": "tool_call",
                    "tool_calls": calls,
                    "thought": msg.content or f"OpenAI quyết định gọi công cụ: {summary}",
                    "raw": None
                }
            else:
                return {
                    "type": "text",
                    "content": msg.content or "",
                    "thought": "OpenAI phản hồi trực tiếp bằng văn bản (không cần gọi công cụ)."
                }
        except Exception as e:
            print(f"⚠️ [OpenAI API Warning]: Không thể kết nối live API ({str(e)}). Tự động fallback về Mock.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude Provider (Native Tool Calling với Anthropic SDK)"""
    # Khi Claude từ chối vì bộ lọc an toàn, server tự chạy lại request trên model dự phòng phù hợp
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model_name = model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5"

    def _has_valid_key(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_anthropic_api_key_here"

    def _client(self):
        import anthropic
        # Chỉ nhận gzip: tránh lỗi giải nén Brotli khi PYTHONPATH (vd. ROS) nạp gói brotli cũ của hệ thống
        return anthropic.Anthropic(api_key=self.api_key, default_headers={"Accept-Encoding": "gzip, deflate"})

    def _create(self, client, system_prompt: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None):
        params = {
            "model": self.model_name,
            "max_tokens": 16000,
            "messages": messages,
            "betas": [self.FALLBACK_BETA],
            "fallbacks": "default",
        }
        # Haiku 4.5 không hỗ trợ adaptive thinking; các model còn lại trả về tóm tắt suy luận làm "Thought"
        if "haiku" not in self.model_name:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
        if system_prompt:
            params["system"] = system_prompt
        if tools:
            params["tools"] = tools
        return client.beta.messages.create(**params)

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        if not self._has_valid_key():
            return "[Anthropic Error]: Chưa cấu hình ANTHROPIC_API_KEY trong file .env! Đang sử dụng chế độ Mock."
        try:
            response = self._create(self._client(), system_prompt, [{"role": "user", "content": prompt}])
            if response.stop_reason == "refusal":
                return "[Anthropic Refusal]: Claude từ chối trả lời yêu cầu này."
            return "".join(b.text for b in response.content if b.type == "text")
        except Exception as e:
            return f"[Anthropic Exception]: {str(e)}"

    def generate_with_tools(self, prompt: str, tools_schema: List[Dict[str, Any]], system_prompt: str = "",
                            history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not self._has_valid_key():
            print("ℹ️ [Anthropic Provider]: Chưa tìm thấy ANTHROPIC_API_KEY hợp lệ. Tự động chuyển sang Mock Offline.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)

        try:
            # Chuẩn hóa tool schema sang định dạng Anthropic (parameters -> input_schema)
            tools = []
            for tool in tools_schema:
                if not tool.get("name") or not tool.get("parameters"):
                    continue
                tools.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["parameters"]
                })

            # Dựng lại hội thoại: assistant (thinking + tool_use) -> user (tool_result) x N
            messages = [{"role": "user", "content": prompt}]
            for turn in history or []:
                raw = turn.get("raw") or {}
                if raw.get("provider") == "anthropic":
                    # Gửi lại nguyên văn nội dung gốc (kể cả thinking blocks) theo yêu cầu của API
                    assistant_content = raw["content"]
                else:
                    assistant_content = [
                        {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                        for c in turn["tool_calls"]
                    ]
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": c["id"], "content": _observation_json(c)}
                    for c in turn["tool_calls"]
                ]})

            response = self._create(self._client(), system_prompt, messages, tools)

            if response.stop_reason == "refusal":
                return {
                    "type": "text",
                    "content": "Xin lỗi, tôi không thể hỗ trợ yêu cầu này.",
                    "thought": "Claude từ chối yêu cầu (stop_reason = refusal)."
                }

            thinking = " ".join(b.thinking for b in response.content if b.type == "thinking" and b.thinking).strip()
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if tool_uses:
                calls = [{"id": b.id, "name": b.name, "arguments": dict(b.input) if b.input else {}} for b in tool_uses]
                summary = ", ".join(f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in calls)
                return {
                    "type": "tool_call",
                    "tool_calls": calls,
                    "thought": thinking or text or f"Claude quyết định gọi công cụ: {summary}",
                    "raw": {"provider": "anthropic", "content": response.content}
                }
            return {
                "type": "text",
                "content": text,
                "thought": thinking or "Claude đã có đủ thông tin và phản hồi trực tiếp bằng văn bản."
            }
        except Exception as e:
            print(f"⚠️ [Anthropic API Warning]: Không thể kết nối live API ({str(e)}). Tự động fallback về Mock.")
            return MockOfflineProvider().generate_with_tools(prompt, tools_schema, system_prompt, history)


def get_llm_provider() -> BaseLLMProvider:
    """Factory function khởi tạo Provider theo LLM_PROVIDER env variable"""
    provider_type = os.getenv("LLM_PROVIDER", "gemini").lower()

    if provider_type == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if key and key != "your_gemini_api_key_here":
            return GeminiProvider()
        else:
            return MockOfflineProvider()
    elif provider_type == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if key and key != "your_openai_api_key_here":
            return OpenAIProvider()
        else:
            return MockOfflineProvider()
    elif provider_type == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if key and key != "your_anthropic_api_key_here":
            return AnthropicProvider()
        else:
            return MockOfflineProvider()
    elif provider_type == "mock":
        return MockOfflineProvider()
    else:
        return MockOfflineProvider()
