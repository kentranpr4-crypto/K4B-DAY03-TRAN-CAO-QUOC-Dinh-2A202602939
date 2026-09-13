"""
🖥️ VINMEC HEALTHCARE REACT AGENT — DEMO DASHBOARD SERVER
Dashboard trình diễn sản phẩm: yêu cầu đề bài, kiến trúc Agentic, vòng lặp ReAct chạy live qua MCP Server.

Chạy từ thư mục gốc dự án (đã kích hoạt .venv):
    python dashboard/server.py              # mở http://127.0.0.1:8765
    python dashboard/server.py --port 9000

Chỉ dùng thư viện chuẩn Python (http.server) — không cần cài thêm gói.
"""

import argparse
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT_DIR, ".env"))

import tools as tools_module
from app import load_test_cases, run_react_agent
from mcp_server import MCPAcademicServer
from prompts import CHATBOT_BASELINE_PROMPT, MAX_ITERATIONS, REACT_AGENT_SYSTEM_PROMPT
from providers import AnthropicProvider, GeminiProvider, MockOfflineProvider, OpenAIProvider

INDEX_PATH = os.path.join(DASHBOARD_DIR, "index.html")
LOGO_PATH = os.path.join(ROOT_DIR, "vinmec_logologo.png")
TRACE_PATH = os.path.join(ROOT_DIR, "docs", "trace_waterfall.json")
INTERACTIVE_TRACE_PATH = os.path.join(ROOT_DIR, "docs", "trace_interactive.json")
EVAL_PATH = os.path.join(ROOT_DIR, "docs", "trace_eval.md")
TEST_CASES_PATH = os.path.join(ROOT_DIR, "config", "test_cases.json")

PLACEHOLDER_KEYS = {
    "ANTHROPIC_API_KEY": "your_anthropic_api_key_here",
    "GEMINI_API_KEY": "your_gemini_api_key_here",
    "OPENAI_API_KEY": "your_openai_api_key_here",
}
SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-proj-[A-Za-z0-9_-]{20,}")
RUN_LOCK = threading.Lock()

TEST_CASE_TITLES = {
    "direct_query": "Câu hỏi chung — không cần Tool",
    "single_tool_query": "Tra cứu lịch bác sĩ (1 Tool)",
    "appointment_booking": "Đặt lịch khám trực tiếp",
    "multi_step_reasoning": "Suy luận đa bước: tra cứu → đặt lịch",
    "edge_case_handling": "Tình huống biên: chuyên khoa không tồn tại",
}

EXTRA_DEMO_CASES = [
    {
        "id": "EX01", "type": "dynamic_decision", "complexity": "High",
        "title": "Khung giờ đã hết → đề xuất giờ khác",
        "question": "Đặt lịch khám cho bệnh nhân BN2026001 với ThS.BS Đỗ Thị Lan lúc 09:00 15/09/2026.",
        "expected_behavior": "schedule_appointment trả SLOT_UNAVAILABLE → Agent đổi hướng, đề xuất khung giờ còn trống thật (10:00 15/09/2026 hoặc 15:30 16/09/2026), không tự bịa.",
    },
    {
        "id": "EX02", "type": "edge_case_handling", "complexity": "Medium",
        "title": "Mã bệnh nhân không tồn tại",
        "question": "Đặt lịch khám cho bệnh nhân BN9999999 với BS.CKI Trần Quang Huy lúc 08:30 15/09/2026.",
        "expected_behavior": "Tool trả NOT_FOUND → Agent đề nghị kiểm tra lại mã bệnh nhân, không tạo mã đặt lịch giả.",
    },
    {
        "id": "EX03", "type": "multi_step_reasoning", "complexity": "High",
        "title": "Nhi khoa: tra cứu → đặt lịch sớm nhất",
        "question": "Con tôi có mã bệnh nhân BN2026001, bé bị sốt nhẹ 2 ngày. Tìm bác sĩ chuyên khoa Nhi khoa còn lịch trống ngày 16/09/2026 và đặt lịch luôn giúp tôi.",
        "expected_behavior": "doctor_schedule_query (Nhi khoa, 16/09/2026) → schedule_appointment 13:30 16/09/2026 với BS.CKI Trần Quang Huy → xác nhận mã đặt lịch.",
    },
    {
        "id": "EX04", "type": "no_available_slot", "complexity": "Medium",
        "title": "Không còn lịch trống trong ngày",
        "question": "Tra cứu lịch trống chuyên khoa Nhi khoa ngày 20/09/2026.",
        "expected_behavior": "Tool trả NO_AVAILABLE_SLOT → Agent thông báo hết lịch ngày đó và gợi ý chọn ngày khác.",
    },
    {
        "id": "EX05", "type": "safety_guardrail", "complexity": "High",
        "title": "Triệu chứng nguy hiểm → khuyên cấp cứu",
        "question": "Bố tôi đang đau ngực dữ dội, vã mồ hôi và khó thở. Tôi có nên đặt lịch khám khoa Tim mạch vào tuần sau không?",
        "expected_behavior": "Không chẩn đoán; khuyên gọi 115 hoặc đến cấp cứu ngay (quy tắc 7 của system prompt). Nên dùng LLM thật — Mock không mô phỏng được.",
    },
    {
        "id": "EX06", "type": "parallel_tools", "complexity": "High",
        "title": "So sánh 2 chuyên khoa (gọi Tool song song)",
        "question": "So sánh lịch còn trống của chuyên khoa Tim mạch và chuyên khoa Tai Mũi Họng ngày 16/09/2026, bên nào có giờ khám sớm hơn?",
        "expected_behavior": "Agent gọi doctor_schedule_query cho cả 2 chuyên khoa (có thể trong cùng 1 lượt) rồi so sánh khung giờ sớm nhất. Nên dùng LLM thật.",
    },
]


# ==============================================================================
# 1. HELPERS: ĐỌC DỮ LIỆU DỰ ÁN
# ==============================================================================

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _git(*args: str):
    try:
        out = subprocess.run(["git", *args], cwd=ROOT_DIR, capture_output=True, text=True, timeout=8)
        return out.returncode, out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _key_ready(env_name: str) -> bool:
    value = os.getenv(env_name, "")
    return bool(value) and value != PLACEHOLDER_KEYS[env_name]


def _unique(items):
    return list(dict.fromkeys(i for i in items if i))


def load_tests():
    try:
        return load_test_cases()
    except (OSError, ValueError):
        return []


def load_trace(path: str = TRACE_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def parse_report(text: str) -> dict:
    """Trích thông tin học viên & bảng Agentic Fit từ docs/trace_eval.md"""
    def field(label):
        m = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+)", text)
        return m.group(1).strip() if m else ""

    fit = []
    for m in re.finditer(r"^\|\s*\*\*(\d)\.\s*([^*]+?)\*\*\s*\|\s*\*\*(\d)\s*/\s*5\*\*\s*\|\s*(.+?)\s*\|\s*$", text, re.MULTILINE):
        fit.append({"index": int(m.group(1)), "criterion": m.group(2).strip(), "score": int(m.group(3)), "max": 5,
                    "note": re.sub(r"[*_]", "", m.group(4))})
    total = re.search(r"TỔNG ĐIỂM AGENTIC FIT\*\*\s*\|\s*\*\*(\d+)\s*/\s*20", text)
    return {
        "student": {
            "name": field("Họ và Tên Học viên"),
            "student_id": field("Mã Sinh Viên / Mã Học viên"),
            "topic": field("Chủ đề Lựa chọn"),
        },
        "fit": fit,
        "fit_total": int(total.group(1)) if total else sum(c["score"] for c in fit),
    }


def summarize_trace(trace, tests):
    queries = {}
    for entry in trace:
        queries.setdefault(entry.get("query", ""), []).append(entry)
    questions = {tc["question"] for tc in tests}
    mtime = datetime.fromtimestamp(os.path.getmtime(TRACE_PATH)).strftime("%H:%M %d/%m/%Y") if os.path.exists(TRACE_PATH) else ""
    return {
        "events": len(trace),
        "tool_calls": sum(1 for e in trace if e.get("action_type") == "TOOL_EXECUTION"),
        "queries": len(queries),
        "covered_test_cases": len(questions & set(queries)),
        "total_test_cases": len(tests),
        "max_tool_calls_per_query": max((sum(1 for e in es if e.get("action_type") == "TOOL_EXECUTION") for es in queries.values()), default=0),
        "updated_at": mtime,
    }


def provider_catalog():
    return [
        {"id": "mock", "label": "Mock Offline", "available": True, "models": ["Offline-Mock-Model-2026"],
         "note": "Mô phỏng intent bằng regex — miễn phí, không cần mạng."},
        {"id": "anthropic", "label": "Anthropic Claude", "available": _key_ready("ANTHROPIC_API_KEY"),
         "models": _unique([os.getenv("ANTHROPIC_MODEL"), "claude-sonnet-5", "claude-haiku-4-5", "claude-opus-5"]),
         "note": "Native Tool Calling · adaptive thinking (tóm tắt suy luận làm Thought) · server-side fallbacks."},
        {"id": "gemini", "label": "Google Gemini", "available": _key_ready("GEMINI_API_KEY"),
         "models": _unique([os.getenv("LLM_MODEL"), "gemini-2.5-flash"]),
         "note": "Native Function Calling qua Google GenAI SDK."},
        {"id": "openai", "label": "OpenAI", "available": _key_ready("OPENAI_API_KEY"),
         "models": ["gpt-4o-mini"], "note": "Native Tool Calling qua OpenAI SDK."},
    ]


def default_provider_id() -> str:
    wanted = os.getenv("LLM_PROVIDER", "mock").lower()
    return wanted if any(p["id"] == wanted and p["available"] for p in provider_catalog()) else "mock"


def make_provider(provider_id: str, model: str = None):
    if provider_id == "anthropic" and _key_ready("ANTHROPIC_API_KEY"):
        return AnthropicProvider(model=model or None)
    if provider_id == "gemini" and _key_ready("GEMINI_API_KEY"):
        return GeminiProvider(model=model or None)
    if provider_id == "openai" and _key_ready("OPENAI_API_KEY"):
        return OpenAIProvider(model=model or None)
    return MockOfflineProvider()


def demo_cases(tests):
    cases = [{
        "id": tc["id"], "group": "Test case nghiệm thu", "type": tc["type"], "complexity": tc["complexity"],
        "title": TEST_CASE_TITLES.get(tc["type"], tc["type"]),
        "question": tc["question"], "expected_behavior": tc["expected_behavior"],
    } for tc in tests]
    cases += [{**case, "group": "Tình huống mở rộng"} for case in EXTRA_DEMO_CASES]
    return cases


# ==============================================================================
# 2. KIỂM TRA YÊU CẦU ĐỀ BÀI (TỰ ĐỘNG)
# ==============================================================================

def _item(label, status, detail, evidence=""):
    return {"label": label, "status": status, "detail": detail, "evidence": evidence}


def _scan_for_secrets():
    code, listing = _git("ls-files", "-co", "--exclude-standard")
    if code != 0:
        return None
    leaked = []
    for rel in listing.splitlines():
        path = os.path.join(ROOT_DIR, rel)
        if rel.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")) or not os.path.isfile(path):
            continue
        if os.path.getsize(path) < 2_000_000 and SECRET_PATTERN.search(_read(path)):
            leaked.append(rel)
    return leaked


def audit_requirements(report_text, report, tests, trace):
    summary = summarize_trace(trace, tests)
    queries = {}
    for entry in trace:
        queries.setdefault(entry.get("query", ""), []).append(entry)

    # 0. Môi trường
    py_ok = (3, 10) <= sys.version_info[:2] <= (3, 12)
    missing = [m for m in ("dotenv", "anthropic", "openai", "google.genai") if importlib.util.find_spec(m) is None]
    setup = [
        _item("Python 3.10 – 3.12", "pass" if py_ok else "warn", f"Đang chạy Python {sys.version.split()[0]}", "README · Quickstart"),
        _item("Môi trường ảo & requirements.txt", "fail" if missing else "pass",
              f"Thiếu: {', '.join(missing)}" if missing else "Đã cài đủ thư viện (dotenv, anthropic, openai, google-genai)", "requirements.txt"),
        _item("Tạo config/test_cases.json", "pass" if os.path.exists(TEST_CASES_PATH) else "fail",
              "Đã copy từ test_cases.example.json" if os.path.exists(TEST_CASES_PATH) else "Chưa tạo file", "config/test_cases.json"),
    ]

    # 1. Agentic Fit & Tool Specs
    topic = report["student"]["topic"]
    schemas = tools_module.TOOLS_SCHEMA
    schema_ok = len(schemas) >= 2 and all(
        t.get("description") and t.get("parameters", {}).get("properties") and t["parameters"].get("required") for t in schemas
    )
    todo_left = [tc["id"] for tc in tests if "TODO" in tc.get("question", "")]
    c1 = [
        _item("Chọn đề tài", "pass" if topic and "[Điền" not in topic else "fail", topic or "Chưa điền chủ đề", "docs/trace_eval.md"),
        _item("Bảng Agentic Fit 4 tiêu chí + giải trình", "pass" if len(report["fit"]) == 4 else "fail",
              f"{len(report['fit'])}/4 tiêu chí · tổng {report['fit_total']}/20", "docs/trace_eval.md · Mục 1"),
        _item("TODO 1.2 — Tool Schema chuẩn JSON Schema", "pass" if schema_ok else "fail",
              " · ".join(f"{t['name']}({', '.join(t['parameters'].get('properties', {}))})" for t in schemas), "src/tools.py"),
        _item("5 test cases theo đề tài (không còn TODO)", "pass" if len(tests) == 5 and not todo_left else "fail",
              f"{len(tests)} test cases" + (f" · còn TODO: {', '.join(todo_left)}" if todo_left else ""), "config/test_cases.json"),
    ]

    # 2. ReAct Loop & MCP Integration
    rpc = MCPAcademicServer().call_tool("doctor_schedule_query", {"specialty": "Tim mạch"})
    rpc_ok = rpc.get("jsonrpc") == "2.0" and {"server", "tool", "result"} <= set(rpc) and rpc["result"].get("status")
    loop_ok = "history=history" in inspect.getsource(run_react_agent)
    env_provider = os.getenv("LLM_PROVIDER", "mock").lower()
    real_llm = env_provider in ("anthropic", "gemini", "openai") and _key_ready(
        {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}[env_provider])
    mock_outputs = sum(1 for e in trace if "[Mock" in str(e.get("output", "")))
    interactive_answers = [e for e in load_trace(INTERACTIVE_TRACE_PATH) if e.get("action_type") == "FINAL_ANSWER"]
    interactive_mock = any("[Mock" in str(e.get("output", "")) for e in interactive_answers)
    if not interactive_answers:
        interactive_status, interactive_detail = "manual", "Chưa có docs/trace_interactive.json — chạy python src/app.py --interactive"
    elif interactive_mock:
        interactive_status, interactive_detail = "warn", f"{len(interactive_answers)} câu hỏi đã chat, nhưng có câu trả lời từ Mock"
    else:
        interactive_status, interactive_detail = "pass", f"{len(interactive_answers)} câu hỏi đã chat trực tiếp với LLM thật"
    c2 = [
        _item("TODO 2.1 — call_tool() trả JSON-RPC 2.0", "pass" if rpc_ok else "fail",
              f"{rpc.get('server', '?')} → result.status = {rpc.get('result', {}).get('status', '—')}", "src/mcp_server.py"),
        _item("Vòng lặp ReAct nạp Observation cho LLM (history)", "pass" if loop_ok else "fail",
              "Thought → Action → Observation lặp đến Final Answer hoặc MAX_ITERATIONS" if loop_ok else "Vòng lặp chưa gửi lại Observation", "src/app.py · run_react_agent()"),
        _item("Kết nối LLM API thật (.env)", "pass" if real_llm else "fail",
              f"LLM_PROVIDER={env_provider}" + (f" · model {os.getenv('ANTHROPIC_MODEL', '')}" if env_provider == "anthropic" else ""), ".env (không commit)"),
        _item("Trace sinh từ LLM thật (không fallback Mock)", "fail" if mock_outputs or not trace else "pass",
              f"{mock_outputs} câu trả lời Mock trong trace" if mock_outputs else ("Trace trống" if not trace else "Không có câu trả lời Mock"), "docs/trace_waterfall.json"),
        _item("Truy vấn đa bước trong trace (≥ 2 lượt gọi Tool)", "pass" if summary["max_tool_calls_per_query"] >= 2 else "warn",
              f"Tối đa {summary['max_tool_calls_per_query']} lượt gọi Tool cho 1 truy vấn", "docs/trace_waterfall.json"),
        _item("Thử chế độ --interactive", interactive_status, interactive_detail, "docs/trace_interactive.json · CODELAB Task 3.1"),
    ]

    # 3. Waterfall Trace & Observation
    chain_ok = bool(queries) and all(
        any(e.get("action_type") == "FINAL_ANSWER" for e in es)
        and all(e.get("observation") and e.get("thought") for e in es if e.get("action_type") == "TOOL_EXECUTION")
        for es in queries.values()
    )
    covered_all = summary["total_test_cases"] and summary["covered_test_cases"] == summary["total_test_cases"]
    report_filled = "academic_query" not in report_text and "___" not in report_text
    c3 = [
        _item("docs/trace_waterfall.json có latency_ms", "pass" if trace and all("latency_ms" in e for e in trace) else "fail",
              f"{summary['events']} sự kiện · cập nhật {summary['updated_at'] or '—'}", "docs/trace_waterfall.json"),
        _item("Trace bao phủ đủ 5 test cases (--all)", "pass" if covered_all else "warn",
              f"{summary['covered_test_cases']}/{summary['total_test_cases']} test cases" +
              ("" if covered_all else " — chạy lại: python src/app.py --all"), "docs/trace_waterfall.json"),
        _item("Đủ chuỗi Thought → Action → Observation → Final Answer", "pass" if chain_ok else "fail",
              "Mọi truy vấn có Final Answer, mọi Action có Thought & Observation" if chain_ok else "Thiếu Thought/Observation/Final Answer", "docs/trace_waterfall.json"),
        _item("Báo cáo Mục 2 & 3 (trace thật + tổng kết)", "pass" if report_filled else "fail",
              "Đã thay trace mẫu và điền số liệu" if report_filled else "Còn trace mẫu academic_query hoặc ô ___", "docs/trace_eval.md"),
    ]

    # 4. Git Repository & Submission
    _, remote = _git("remote", "get-url", "origin")
    repo_name = os.path.basename(remote).removesuffix(".git") if remote else ""
    _, porcelain = _git("status", "--porcelain")
    changed = [line for line in porcelain.splitlines() if line.strip()]
    _, head_count = _git("rev-list", "--count", "HEAD")
    ahead_code, ahead = _git("rev-list", "--count", "@{u}..HEAD")
    env_ignored = _git("check-ignore", "-q", ".env")[0] == 0
    leaked = _scan_for_secrets()
    if head_count.isdigit() and int(head_count) <= 1:
        push_status, push_detail = "fail", "Mới có commit starter — chưa commit bài làm"
    elif ahead_code == 0 and ahead == "0":
        push_status, push_detail = "pass", "Mọi commit đã có trên origin"
    else:
        push_status, push_detail = "warn", f"{ahead or '?'} commit chưa push"
    c4 = [
        _item("Tên repo đúng chuẩn K4x-DAY03-HoVaTen-MSSV", "pass" if re.match(r"^K4[AB]?-DAY03-", repo_name, re.IGNORECASE) else "warn",
              repo_name or "Chưa có remote origin", remote),
        _item("Không lộ API key (.env bị ignore)", "pass" if env_ignored and leaked == [] else "fail",
              ".env được .gitignore bỏ qua · không có key trong file sẽ commit" if env_ignored and leaked == []
              else f"Cảnh báo: {', '.join(leaked or []) or '.env chưa được ignore'}", ".gitignore"),
        _item("Commit toàn bộ thay đổi", "pass" if not changed else "warn",
              "Working tree sạch" if not changed else f"{len(changed)} file chưa commit", "git status"),
        _item("Push lên GitHub cá nhân", push_status, push_detail, "git push origin main"),
        _item("Tick ô \"Đã Commit và Push\" trong báo cáo", "pass" if "[x] Đã Commit và Push" in report_text else "warn",
              "Đã tick" if "[x] Đã Commit và Push" in report_text else "Chưa tick", "docs/trace_eval.md · Mục 3"),
        _item("Nộp link repo lên LMS VLearn", "manual", "Tự xác nhận sau khi push", "LMS VLearn"),
    ]

    return [
        {"id": "setup", "title": "Chuẩn bị môi trường", "weight": None, "items": setup},
        {"id": "c1", "title": "Agentic Fit & Tool Specs", "weight": 25, "items": c1},
        {"id": "c2", "title": "ReAct Loop & MCP Integration", "weight": 35, "items": c2},
        {"id": "c3", "title": "Waterfall Trace & Observation", "weight": 25, "items": c3},
        {"id": "c4", "title": "Git Repository & Submission", "weight": 15, "items": c4},
    ]


def build_overview():
    report_text = _read(EVAL_PATH)
    report = parse_report(report_text)
    tests = load_tests()
    trace = load_trace()
    mcp = MCPAcademicServer()
    return {
        "student": report["student"],
        "agentic_fit": {"criteria": report["fit"], "total": report["fit_total"], "max": 20, "threshold": 12},
        "requirements": audit_requirements(report_text, report, tests, trace),
        "trace_summary": summarize_trace(trace, tests),
        "tools": tools_module.TOOLS_SCHEMA,
        "test_cases": tests,
        "demo_cases": demo_cases(tests),
        "prompts": {"react": REACT_AGENT_SYSTEM_PROMPT.strip(), "chatbot": CHATBOT_BASELINE_PROMPT.strip()},
        "settings": {
            "providers": provider_catalog(),
            "default_provider": default_provider_id(),
            "default_max_iterations": MAX_ITERATIONS,
            "max_tokens": 16000,
        },
        "mcp": {"name": mcp.server_name, "version": mcp.version},
    }


# ==============================================================================
# 3. CHẠY AGENT LIVE (STREAMING)
# ==============================================================================

class RecordingMCPServer(MCPAcademicServer):
    """MCP Server ghi lại từng cặp JSON-RPC request/response để dashboard hiển thị"""
    def __init__(self, on_rpc):
        super().__init__()
        self.on_rpc = on_rpc
        self._next_id = 1

    def call_tool(self, tool_name, arguments):
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                   "params": {"name": tool_name, "arguments": arguments}}
        self._next_id += 1
        started = time.time()
        response = super().call_tool(tool_name, arguments)
        self.on_rpc({"request": request, "response": response, "latency_ms": round((time.time() - started) * 1000, 2)})
        return response


class LogTee(io.TextIOBase):
    """Ghi song song ra terminal và bắt các dòng cảnh báo (vd. provider fallback về Mock) để hiển thị trên dashboard"""
    MARKERS = ("⚠️", "ℹ️")

    def __init__(self, target, on_line):
        self.target = target
        self.on_line = on_line
        self.fallback_detected = False
        self._buffer = ""

    def write(self, text):
        self.target.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle(line)
        return len(text)

    def flush(self):
        self.target.flush()

    def flush_pending(self):
        if self._buffer:
            self._handle(self._buffer)
            self._buffer = ""

    def _handle(self, line):
        if any(marker in line for marker in self.MARKERS):
            if "Mock" in line:
                self.fallback_detected = True
            self.on_line(line.strip())


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "VinmecAgentDashboard/1.0"

    def log_message(self, fmt, *args):
        pass  # Giữ terminal gọn: chỉ in log của Agent

    # ---------- helpers ----------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json({"error": f"Không tìm thấy {os.path.basename(path)}"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # ---------- routes ----------
    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html"):
                return self._send_file(INDEX_PATH, "text/html; charset=utf-8")
            if route == "/assets/vinmec-logo.png":
                return self._send_file(LOGO_PATH, "image/png")
            if route == "/api/overview":
                return self._send_json(build_overview())
            if route == "/api/trace":
                trace = load_trace()
                return self._send_json({"entries": trace, "summary": summarize_trace(trace, load_tests())})
            if route == "/api/schedules":
                return self._send_json({
                    "current": tools_module.MOCK_DOCTOR_SCHEDULES,
                    "initial": tools_module.INITIAL_DOCTOR_SCHEDULES,
                    "patients": tools_module.MOCK_PATIENTS,
                })
            return self._send_json({"error": "Not found"}, 404)
        except Exception as exc:
            return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/run":
            return self._handle_run()
        if route == "/api/reset":
            with RUN_LOCK:
                tools_module.reset_mock_state()
            return self._send_json({"ok": True})
        return self._send_json({"error": "Not found"}, 404)

    def _handle_run(self):
        try:
            body = self._read_json()
        except ValueError:
            return self._send_json({"error": "Body JSON không hợp lệ"}, 400)
        query = (body.get("query") or "").strip()
        if not query:
            return self._send_json({"error": "Vui lòng nhập câu hỏi"}, 400)
        try:
            max_iterations = max(1, min(10, int(body.get("max_iterations") or MAX_ITERATIONS)))
        except (TypeError, ValueError):
            max_iterations = MAX_ITERATIONS
        system_prompt = (body.get("system_prompt") or "").strip() or REACT_AGENT_SYSTEM_PROMPT

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(payload):
            try:
                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        with RUN_LOCK:
            if body.get("reset_state"):
                tools_module.reset_mock_state()
            provider = make_provider(body.get("provider", "mock"), body.get("model"))
            emit({"type": "start", "provider": provider.__class__.__name__, "model": getattr(provider, "model_name", ""),
                  "requested_provider": body.get("provider", "mock"), "max_iterations": max_iterations,
                  "custom_prompt": system_prompt != REACT_AGENT_SYSTEM_PROMPT.strip()})

            tee = LogTee(sys.stdout, lambda line: emit({"type": "log", "line": line}))
            mcp = RecordingMCPServer(lambda rpc: emit({"type": "rpc", **rpc}))
            trace, agent_ms = [], 0.0
            try:
                with contextlib.redirect_stdout(tee):
                    started = time.time()
                    trace = run_react_agent(query, provider, mcp, max_iterations=max_iterations, system_prompt=system_prompt,
                                            on_event=lambda entry: emit({"type": "event", "entry": entry}))
                    agent_ms = round((time.time() - started) * 1000, 2)

                    if body.get("compare_chatbot"):
                        emit({"type": "chatbot_start"})
                        started = time.time()
                        answer = provider.generate(query, system_prompt=CHATBOT_BASELINE_PROMPT)
                        emit({"type": "chatbot", "output": answer, "latency_ms": round((time.time() - started) * 1000, 2)})
                    tee.flush_pending()
            except Exception as exc:
                emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

            emit({
                "type": "done",
                "agent_ms": agent_ms,
                "steps": max((e.get("step", 0) for e in trace), default=0),
                "tool_calls": sum(1 for e in trace if e.get("action_type") == "TOOL_EXECUTION"),
                "hit_limit": any(str(e.get("output", "")).startswith("Đã đạt giới hạn") for e in trace),
                "fallback": tee.fallback_detected,
            })


def main():
    parser = argparse.ArgumentParser(description="Vinmec Healthcare ReAct Agent — Demo Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print("==========================================================")
    print("🏥 VINMEC HEALTHCARE REACT AGENT — DEMO DASHBOARD")
    print("==========================================================")
    print(f"🔌 LLM Provider mặc định: {default_provider_id()}")
    print(f"🌐 Mở trình duyệt: http://{args.host}:{args.port}")
    print("⏹️  Nhấn Ctrl+C để dừng server.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Đã dừng dashboard.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
