"""
🚀 CORE AGENT APPLICATION (DAY 03: CHATBOT VS REACT AGENT)
Thực thi so sánh giữa Chatbot Baseline (Cấp 2) và ReAct Agent kết nối MCP Server (Cấp 3).
"""

import json
import os
import sys
import time
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from mcp_server import MCPAcademicServer
from prompts import (
    CHATBOT_BASELINE_PROMPT,
    REACT_AGENT_SYSTEM_PROMPT,
    MAX_ITERATIONS
)
from providers import get_llm_provider

load_dotenv()

def load_test_cases():
    """Tải danh sách 5 test cases từ config/test_cases.json hoặc config/test_cases.example.json"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config", "test_cases.json")
    if not os.path.exists(config_path):
        example_path = os.path.join(base_dir, "config", "test_cases.example.json")
        if os.path.exists(example_path):
            print("⚠️ [CONFIG NOTICE]: Chưa thấy file 'config/test_cases.json'. Đang dùng mẫu 'config/test_cases.example.json'.")
            print("👉 Hãy chạy: copy config/test_cases.example.json config/test_cases.json và viết test cases theo đề tài của bạn!\n")
            config_path = example_path
        else:
            config_path = "test_cases.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_waterfall_trace(trace_data: list, filename: str = "trace_waterfall.json"):
    """Ghi vết log Waterfall Trace Log ra file docs/<filename> (mặc định docs/trace_waterfall.json)"""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs_dir = os.path.join(base_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    trace_path = os.path.join(docs_dir, filename)
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(trace_data, f, ensure_ascii=False, indent=2)
    print(f"📊 [OBSERVABILITY]: Đã lưu {len(trace_data)} sự kiện Waterfall Trace tại '{trace_path}'!")


def run_baseline_chatbot(user_query: str, provider):
    """Chạy Chatbot gốc (Cấp 2) không có công cụ gọi Tool"""
    print(f"\n💬 [CHATBOT BASELINE] Câu hỏi: {user_query}")
    response = provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)
    print(f"🤖 Chatbot phản hồi:\n{response}")


def run_react_agent(user_query: str, provider, mcp_server: MCPAcademicServer, max_iterations: int = MAX_ITERATIONS,
                    system_prompt: str = None, on_event=None) -> list:
    """
    [REACT AGENT LOOP] Thực thi vòng lặp Thought -> Action -> Observation với MCP Server
    Trả về danh sách trace log của phiên thực thi.
    """
    print(f"\n🤖 [REACT AGENT] Câu hỏi: {user_query}")
    
    step = 0
    trace_logs = []
    history = []  # Các lượt Action -> Observation đã thực hiện, nạp lại cho LLM ở lượt kế tiếp
    tools_list = mcp_server.list_tools()

    def record(entry: dict):
        """Ghi 1 sự kiện vào trace và phát sự kiện realtime (dashboard) nếu có"""
        trace_logs.append(entry)
        if on_event:
            on_event(entry)

    while step < max_iterations:
        step += 1
        print(f"\n--- 🔄 Vòng lặp ReAct Loop (Step {step}/{max_iterations}) ---")
        if on_event:
            on_event({"step": step, "query": user_query, "action_type": "LLM_THINKING"})

        # Gọi LLM với Native Tool Calling Specs + toàn bộ lịch sử Observation
        llm_start_time = time.time()
        llm_response = provider.generate_with_tools(
            user_query, tools_list, system_prompt=system_prompt or REACT_AGENT_SYSTEM_PROMPT, history=history
        )
        llm_latency_ms = round((time.time() - llm_start_time) * 1000, 2)

        thought = llm_response.get("thought") or "Đang suy luận..."
        print(f"🧠 [Thought]: {thought}")

        # Trường hợp 1: LLM đã đủ thông tin và trả lời bằng văn bản -> kết thúc vòng lặp
        if llm_response.get("type") != "tool_call":
            final_content = llm_response.get("content", "")
            print(f"🏁 [Final Answer]: {final_content}")
            record({
                "step": step,
                "query": user_query,
                "action_type": "FINAL_ANSWER",
                "thought": thought,
                "output": final_content,
                "latency_ms": llm_latency_ms
            })
            break

        # Trường hợp 2: LLM đề xuất gọi Tool (Action) -> MCP Server thực thi -> Observation
        executed_calls = []
        for call in llm_response.get("tool_calls", []):
            print(f"🛠️ [Action Proposed]: {call['name']}({call['arguments']})")

            tool_start_time = time.time()
            mcp_result = mcp_server.call_tool(call["name"], call["arguments"])
            tool_latency_ms = round((time.time() - tool_start_time) * 1000, 2)
            observation = mcp_result.get("result", {})
            print(f"👁️ [Observation từ MCP Server]: {json.dumps(observation, ensure_ascii=False)}")

            executed_calls.append({**call, "observation": observation})
            record({
                "step": step,
                "query": user_query,
                "action_type": "TOOL_EXECUTION",
                "thought": thought,
                "tool_name": call["name"],
                "arguments": call["arguments"],
                "observation": observation,
                "llm_latency_ms": llm_latency_ms,
                "tool_latency_ms": tool_latency_ms,
                "latency_ms": round(llm_latency_ms + tool_latency_ms, 2)
            })

        # Nạp Observation vào lịch sử để LLM suy luận tiếp ở vòng sau
        history.append({"thought": thought, "raw": llm_response.get("raw"), "tool_calls": executed_calls})
    else:
        # Hết MAX_ITERATIONS mà LLM vẫn chưa đưa ra câu trả lời cuối cùng
        stop_message = f"Đã đạt giới hạn {max_iterations} vòng lặp ReAct nhưng chưa có câu trả lời cuối cùng."
        print(f"⚠️ [MAX_ITERATIONS]: {stop_message}")
        record({
            "step": step,
            "query": user_query,
            "action_type": "FINAL_ANSWER",
            "thought": "Dừng vòng lặp do vượt quá số bước cho phép.",
            "output": stop_message,
            "latency_ms": 0.0
        })

    return trace_logs


if __name__ == "__main__":
    print("==========================================================")
    print("🏥 VINUNI AI COURSE - DAY 03 LAB: VINMEC HEALTHCARE REACT AGENT")
    print("==========================================================")
    
    provider = get_llm_provider()
    mcp_server = MCPAcademicServer()
    
    print(f"🔌 LLM Provider: {provider.__class__.__name__}")
    print(f"🌐 MCP Server: {mcp_server.server_name}\n")
    
    tests = load_test_cases()
    print(f"✅ Đã tải thành công {len(tests)} Test Cases thử nghiệm.\n")
    
    if "--interactive" in sys.argv:
        print("🎮 [INTERACTIVE MODE] Trò chuyện trực tiếp với ReAct Agent:")
        print("💡 Gợi ý câu hỏi thử nghiệm:")
        print("   - Câu hỏi chung: 'Trước khi đi khám sức khỏe tổng quát tôi cần chuẩn bị gì?'")
        print("   - Tra cứu lịch bác sĩ: 'Tra cứu lịch trống của bác sĩ khoa Tim mạch ngày 15/09/2026'")
        print("   - Đặt lịch khám: 'Đặt lịch khám cho BN2026001 với BS.CKII Nguyễn Minh Tuấn lúc 14:00 15/09/2026'")
        print("   - Gõ 'exit' hoặc 'quit' để kết thúc phiên trò chuyện.\n")
        session_logs = []
        while True:
            try:
                user_input = input("👤 Sinh viên hỏi: ").strip()
                if not user_input or user_input.lower() in ["exit", "quit"]:
                    print("👋 Tạm biệt! Kết thúc phiên trò chuyện.")
                    break
                session_logs.extend(run_react_agent(user_input, provider, mcp_server))
                # Lưu riêng cả phiên chat để không ghi đè trace nghiệm thu của --all
                save_waterfall_trace(session_logs, "trace_interactive.json")
            except (KeyboardInterrupt, EOFError):
                print("\n👋 Đã thoát phiên tương tác.")
                break
    elif "--all" in sys.argv:
        print("🚀 [TEST SUITE MODE] Kiểm tra 5 Test Cases:")
        completed_count = 0
        todo_count = 0
        all_traces = []
        
        for tc in tests:
            print(f"\n==================================================")
            print(f"🧪 [{tc['id']}] Loại test: {tc['type']} (Độ phức tạp: {tc['complexity']})")
            print(f"📌 Kỳ vọng: {tc['expected_behavior']}")
            
            if tc["question"].strip().startswith("TODO"):
                print(f"⏸️ [CHƯA KÍCH HOẠT - ĐANG LÀ TODO]:")
                print(f"   {tc['question']}")
                print(f"   👉 Hãy mở file 'config/test_cases.json' để viết câu hỏi thực tế cho Test Case này!")
                todo_count += 1
            else:
                logs = run_react_agent(tc["question"], provider, mcp_server)
                all_traces.extend(logs)
                completed_count += 1
                
        print(f"\n==================================================")
        print(f"📊 [KẾT QUẢ TEST SUITE]: Đã thực thi {completed_count}/{len(tests)} Test Cases | {todo_count} Test Cases đang chờ điền câu hỏi (TODO)")
        tool_call_count = sum(1 for t in all_traces if t["action_type"] == "TOOL_EXECUTION")
        print(f"🛠️ [MCP]: Tổng số lượt gọi Tool qua MCP Server: {tool_call_count}")
        if all_traces:
            save_waterfall_trace(all_traces)
        print(f"💡 Để trò chuyện trực tiếp từng câu: Chạy 'python src/app.py --interactive'")
    else:
        # Chế độ mặc định khi chỉ gõ 'python src/app.py'
        print("ℹ️ HƯỚNG DẪN SỬ DỤNG CHƯƠNG TRÌNH:")
        print("  1. Chat trực tiếp liên tục:   python src/app.py --interactive")
        print("  2. Chạy toàn bộ Test Cases:    python src/app.py --all\n")
        
        sample_query = tests[1]["question"]
        print(f"--- 🏁 DEMO CHẠY THỬ 1 TEST CASE MẪU (TC02: Tra cứu lịch bác sĩ) ---")
        logs = run_react_agent(sample_query, provider, mcp_server)
        save_waterfall_trace(logs)
        print("\n💡 Hãy thử ngay lệnh: python src/app.py --interactive để chat trực tiếp!")
