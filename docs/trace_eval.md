# 📊 BÁO CÁO THU HOẠCH NGHIỆM THU BÀI LAB 3 (BƯỚC 3 — SUBMISSION ARTIFACT)

> **Họ và Tên Học viên:** Trần Cao Quốc Định  
> **Mã Sinh Viên / Mã Học viên:** 2A202602939  
> **Chủ đề Lựa chọn:** 4. Lĩnh vực Dịch vụ Khách hàng & Y tế (Customer Service & Healthcare) — Gợi ý 4.3: *Trợ lý Tư vấn Sức khỏe Vinmec* (Tra cứu lịch làm việc bác sĩ chuyên khoa & Đặt lịch khám bệnh)  

**Bộ công cụ MCP của Agent:**
- `doctor_schedule_query(specialty, date?)` — Tra cứu bác sĩ và khung giờ còn trống theo chuyên khoa *(công cụ tra cứu)*.
- `schedule_appointment(patient_id, doctor_name, datetime_str)` — Đặt lịch khám cho bệnh nhân *(công cụ hành động)*.

---

## 1. BẢNG CHẤM ĐIỂM AGENTIC FIT SCORING MATRIX (ĐÁNH GIÁ CHỦ ĐỀ)

| Tiêu chí Đánh giá | Mức độ (1 - 5) | Giải trình chi tiết lý do chọn điểm |
| :--- | :---: | :--- |
| **1. Multi-step Reasoning** | **4 / 5** | Bài toán cần chia nhỏ nhiều bước suy luận nối tiếp. Ví dụ: bệnh nhân gọi *"Tôi bị sốt"* → hệ thống hỏi thêm *"sốt bao nhiêu độ?"* → hỏi *"có ho không?"* → rồi mới quyết định đặt lịch khám hay tư vấn qua điện thoại → ghi lại hồ sơ. Trong Agent: xác định chuyên khoa → tra cứu lịch trống → chọn bác sĩ & khung giờ → đặt lịch. |
| **2. Tool Interaction** | **5 / 5** | Bắt buộc kết nối dữ liệu bên ngoài qua MCP Server. Để xử lý yêu cầu khám bệnh, hệ thống cần: xem lịch bác sĩ (database), kiểm tra bảo hiểm (API bảo hiểm), gửi tin nhắn xác nhận (SMS service), cập nhật hồ sơ bệnh nhân (EHR). LLM không thể tự biết lịch trống thời gian thực. |
| **3. Dynamic Decision** | **4 / 5** | Bước tiếp theo phụ thuộc vào kết quả quan sát bước trước. Ví dụ: *"sốt 39 độ"* → ưu tiên cao; *"sốt 37.5 độ"* → ưu tiên thấp; *"sốt và khó thở"* → chuyển nhân viên/cấp cứu ngay. Trong Agent: nếu khung giờ đã hết (`SLOT_UNAVAILABLE`) hoặc không tìm thấy chuyên khoa (`NOT_FOUND`) thì phải đổi hướng xử lý. |
| **4. Long Horizon Goal** | **3 / 5** | Mức vừa phải: một số task chỉ làm 1 lần (trả lời FAQ, đặt 1 lịch khám), nhưng cũng có goal dài hạn như theo dõi bệnh nhân mãn tính suốt 3 tháng. Healthcare có cả hai loại. |
| **TỔNG ĐIỂM AGENTIC FIT** | **16 / 20** | **16 > 12 → Bài toán rất phù hợp triển khai Agentic System**, vì cần suy luận nhiều bước, dữ liệu từ nhiều nguồn và thích ứng theo tình huống. |

---

## 2. TRÍCH XUẤT KẾT QUẢ WATERFALL TRACE LOG (SAU KHI CHẠY TEST SUITE TRÊN API THẬT)

> ⚠️ **YÊU CẦU NGHIỆM THU:** Mở tệp `.env` điền `GEMINI_API_KEY` (hoặc `OPENAI_API_KEY`) để kết nối LLM thật trước khi thực thi `python src/app.py --all`. Bài nộp chỉ dùng Mock Offline Provider sẽ không đạt điểm nghiệm thực tế.

> 🔌 **LLM Provider sử dụng:** Anthropic Claude — model `claude-sonnet-5` (Native Tool Calling, đã được giảng viên chấp thuận thay cho Gemini/OpenAI).

Dán 1 đoạn trích xuất log tiêu biểu từ file `docs/trace_waterfall.json` sinh ra từ phản hồi LLM API thật:

**Test Case tiêu biểu — TC04 (Multi-step Reasoning):** *"Tôi là bệnh nhân BN2026002, dạo này tôi hay bị đau họng và ù tai. Hãy tìm bác sĩ khoa Tai Mũi Họng còn lịch trống sớm nhất ngày 16/09/2026 và đặt lịch khám luôn cho tôi."*

Chuỗi ReAct: **Thought → `doctor_schedule_query` → Observation → Thought (chọn khung giờ sớm nhất) → `schedule_appointment` → Observation → Final Answer**.

```json
[
  {
    "step": 1,
    "action_type": "TOOL_EXECUTION",
    "thought": "I need to check the ENT doctor's schedule for September 16, 2026 first.",
    "tool_name": "doctor_schedule_query",
    "arguments": {
      "specialty": "Tai Mũi Họng",
      "date": "16/09/2026"
    },
    "observation": {
      "status": "SUCCESS",
      "specialty": "Tai Mũi Họng",
      "doctors": [
        {
          "doctor_name": "TS.BS Vũ Thanh Mai",
          "hospital": "Vinmec Times City",
          "available_slots": ["10:30 16/09/2026", "16:00 16/09/2026"]
        },
        {
          "doctor_name": "BS.CKI Hoàng Văn Đức",
          "hospital": "Vinmec Smart City",
          "available_slots": ["08:00 16/09/2026"]
        }
      ]
    },
    "llm_latency_ms": 3149.81,
    "tool_latency_ms": 0.3,
    "latency_ms": 3150.11
  },
  {
    "step": 2,
    "action_type": "TOOL_EXECUTION",
    "thought": "The earliest available slot is 08:00 on 16/09/2026 with BS.CKI Hoàng Văn Đức, so I'll go ahead and book that appointment.",
    "tool_name": "schedule_appointment",
    "arguments": {
      "patient_id": "BN2026002",
      "doctor_name": "BS.CKI Hoàng Văn Đức",
      "datetime_str": "08:00 16/09/2026"
    },
    "observation": {
      "status": "SUCCESS",
      "booking_id": "VM-BN2026002-0800",
      "patient_name": "Phạm Thu Hà",
      "hospital": "Vinmec Smart City",
      "datetime": "08:00 16/09/2026"
    },
    "llm_latency_ms": 2469.7,
    "tool_latency_ms": 0.29,
    "latency_ms": 2469.99
  },
  {
    "step": 3,
    "action_type": "FINAL_ANSWER",
    "output": "Đã đặt lịch khám thành công cho bạn: Phạm Thu Hà (BN2026002) — BS.CKI Hoàng Văn Đức, Tai Mũi Họng — Vinmec Smart City — 08:00, ngày 16/09/2026 — Mã đặt lịch: VM-BN2026002-0800. Đây là khung giờ trống sớm nhất trong ngày bạn yêu cầu. [...] Nếu xuất hiện thêm dấu hiệu như sốt cao, chóng mặt nhiều, giảm thính lực đột ngột hoặc khó thở, bạn nên đến cấp cứu ngay thay vì chờ lịch hẹn.",
    "latency_ms": 5995.12
  }
]
```
*(Trích gọn một số trường `query`, `message` cho dễ đọc — bản đầy đủ nằm trong `docs/trace_waterfall.json`.)*

**Tổng hợp kết quả 5 Test Cases (LLM API thật):**

| TC | Loại test | Chuỗi hành động thực tế | Kết quả |
| :---: | :--- | :--- | :---: |
| TC01 | direct_query | Final Answer trực tiếp (0 tool) | ✅ Pass |
| TC02 | single_tool_query | `doctor_schedule_query` (Tim mạch, 15/09/2026) → Final Answer liệt kê đúng 2 bác sĩ, 3 khung giờ | ✅ Pass |
| TC03 | appointment_booking | `schedule_appointment` (BN2026001, BS.CKII Nguyễn Minh Tuấn, 14:00 15/09/2026) → mã `VM-BN2026001-1400` | ✅ Pass |
| TC04 | multi_step_reasoning | `doctor_schedule_query` → `schedule_appointment` (08:00 16/09/2026) → mã `VM-BN2026002-0800` | ✅ Pass |
| TC05 | edge_case_handling | `doctor_schedule_query` (Da liễu) → `NOT_FOUND` → phản hồi lịch sự, liệt kê chuyên khoa hiện có, không bịa dữ liệu | ✅ Pass |

**Thử nghiệm chế độ đàm thoại trực tiếp `python src/app.py --interactive` (LLM API thật):**

Phiên chat được lưu riêng tại `docs/trace_interactive.json` (không ghi đè trace nghiệm thu của `--all`).

| Câu hỏi trong phiên chat | Chuỗi hành động thực tế | Kết quả |
| :--- | :--- | :---: |
| *"Con tôi có mã bệnh nhân BN2026001, bé bị sốt nhẹ 2 ngày. Tìm bác sĩ chuyên khoa Nhi khoa còn lịch trống ngày 16/09/2026 và đặt lịch luôn giúp tôi."* | `doctor_schedule_query` (Nhi khoa, 16/09/2026) → `schedule_appointment` (BS.CKI Trần Quang Huy, 13:30 16/09/2026) → mã `VM-BN2026001-1330`, kèm hướng dẫn theo dõi sốt và dấu hiệu cần đi cấp cứu | ✅ Đa bước |
| *"Đặt lịch khám cho bệnh nhân BN2026002 với ThS.BS Đỗ Thị Lan lúc 09:00 15/09/2026."* | `schedule_appointment` → `SLOT_UNAVAILABLE` → **Dynamic Decision:** không bịa lịch, đề xuất đúng 2 khung giờ còn trống từ Observation (10:00 15/09/2026, 15:30 16/09/2026) và hỏi bệnh nhân chọn | ✅ Quyết định động |

---

## 3. TỔNG KẾT KẾT QUẢ NGHIỆM THU & NỘP BÀI

- [x] Đã điền API Key thật trong `.env` và xác nhận Agent chạy mượt mà trên LLM API thật (Anthropic Claude `claude-sonnet-5` — đã được giảng viên chấp thuận).
- **Tổng số Test Cases đã chạy thành công:** 5 / 5 test cases.
- **Số lượt gọi Tool qua MCP Server chính xác:** 5 lượt (TC02: 1, TC03: 1, TC04: 2, TC05: 1).
- [x] Đã thử nghiệm chế độ đàm thoại trực tiếp `python src/app.py --interactive` với LLM API thật: 2 câu hỏi, 3 lượt gọi Tool qua MCP Server (log: `docs/trace_interactive.json`).
- **Kết quả đẩy Repo nộp bài:** [ ] Đã Commit và Push mã nguồn thành công lên GitHub cá nhân.

---

> ✅ **HOÀN TẤT NỘP BÀI:** Sao chép đường link GitHub Repository cá nhân của bạn và dán vào ô nộp bài trên hệ thống LMS VLearn để hoàn tất Bài Lab 3!
