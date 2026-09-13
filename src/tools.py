"""
🛠️ TOOL DEFINITIONS & EXECUTION BACKEND
Mã nguồn chứa danh sách Tool Schemas (JSON Schema) và Execution Layer phục vụ cho MCP Server.
Chủ đề: 4.3 Trợ lý Tư vấn Sức khỏe Vinmec (Tra cứu lịch bác sĩ & Đặt lịch khám bệnh).
"""

import copy
import json
import re
from typing import Dict, Any, List, Optional

# ==============================================================================
# 1. KHAI BÁO TOOL SCHEMAS CHUẨN NATIVE JSON SCHEMA (TASK 1.2)
# ==============================================================================

TOOLS_SCHEMA = [
    # Tool 1: Công cụ tra cứu thông tin (Read)
    {
        "name": "doctor_schedule_query",
        "description": (
            "Tra cứu danh sách bác sĩ và các khung giờ khám còn trống theo chuyên khoa tại Bệnh viện Vinmec. "
            "Dùng tool này TRƯỚC khi đặt lịch để biết bác sĩ nào còn lịch và khung giờ chính xác."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "specialty": {
                    "type": "string",
                    "description": "Tên chuyên khoa cần tra cứu (ví dụ: 'Tim mạch', 'Nhi khoa', 'Tai Mũi Họng')"
                },
                "date": {
                    "type": "string",
                    "description": "Ngày khám cần lọc theo định dạng dd/mm/yyyy (ví dụ: '15/09/2026'). Bỏ trống để xem tất cả các ngày."
                }
            },
            "required": ["specialty"]
        }
    },

    # Tool 2: Công cụ hành động (Write) — [TASK 1.2]
    {
        "name": "schedule_appointment",
        "description": (
            "Đặt lịch khám bệnh cho bệnh nhân với một bác sĩ Vinmec vào một khung giờ còn trống. "
            "Chỉ gọi khi đã biết chính xác mã bệnh nhân, tên bác sĩ và khung giờ còn trống."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "Mã bệnh nhân cần đặt lịch (ví dụ: 'BN2026001')"
                },
                "doctor_name": {
                    "type": "string",
                    "description": "Họ tên bác sĩ muốn đặt lịch (ví dụ: 'BS.CKII Nguyễn Minh Tuấn')"
                },
                "datetime_str": {
                    "type": "string",
                    "description": "Thời gian khám theo định dạng 'HH:MM dd/mm/yyyy' (ví dụ: '14:00 15/09/2026')"
                }
            },
            "required": ["patient_id", "doctor_name", "datetime_str"]
        }
    }
]

# ==============================================================================
# 2. MÔ PHỎNG DỮ LIỆU & HÀM THỰC THI TOOL (EXECUTION LAYER)
# ==============================================================================

MOCK_PATIENTS = {
    "BN2026001": {
        "full_name": "Lê Hoàng Nam",
        "phone": "0901 234 567",
        "insurance": "Bảo hiểm Vinmec Care"
    },
    "BN2026002": {
        "full_name": "Phạm Thu Hà",
        "phone": "0912 345 678",
        "insurance": "BHYT"
    }
}

MOCK_DOCTOR_SCHEDULES = {
    "tim mạch": [
        {
            "doctor_name": "BS.CKII Nguyễn Minh Tuấn",
            "hospital": "Vinmec Times City",
            "available_slots": ["08:00 15/09/2026", "14:00 15/09/2026", "09:00 16/09/2026"]
        },
        {
            "doctor_name": "ThS.BS Đỗ Thị Lan",
            "hospital": "Vinmec Times City",
            "available_slots": ["10:00 15/09/2026", "15:30 16/09/2026"]
        }
    ],
    "nhi khoa": [
        {
            "doctor_name": "BS.CKI Trần Quang Huy",
            "hospital": "Vinmec Central Park",
            "available_slots": ["08:30 15/09/2026", "13:30 16/09/2026"]
        }
    ],
    "tai mũi họng": [
        {
            "doctor_name": "TS.BS Vũ Thanh Mai",
            "hospital": "Vinmec Times City",
            "available_slots": ["10:30 16/09/2026", "16:00 16/09/2026"]
        },
        {
            "doctor_name": "BS.CKI Hoàng Văn Đức",
            "hospital": "Vinmec Smart City",
            "available_slots": ["08:00 16/09/2026", "14:00 17/09/2026"]
        }
    ]
}


# Ảnh chụp dữ liệu ban đầu để khôi phục lịch trống sau khi demo đặt lịch
INITIAL_DOCTOR_SCHEDULES = copy.deepcopy(MOCK_DOCTOR_SCHEDULES)


def reset_mock_state() -> None:
    """Khôi phục lịch trống của bác sĩ về trạng thái ban đầu (dùng cho dashboard demo)"""
    MOCK_DOCTOR_SCHEDULES.clear()
    MOCK_DOCTOR_SCHEDULES.update(copy.deepcopy(INITIAL_DOCTOR_SCHEDULES))


def _normalize_specialty(specialty: str) -> str:
    """Chuẩn hóa tên chuyên khoa: chữ thường, bỏ tiền tố 'khoa'"""
    s = specialty.strip().lower()
    return re.sub(r"^(chuyên\s+)?khoa\s+", "", s)


def _normalize_date(date_str: str) -> str:
    """Chuẩn hóa ngày về dd/mm/yyyy (hỗ trợ cả yyyy-mm-dd)"""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{m.group(1)}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_str)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return date_str.strip()


def _normalize_datetime(datetime_str: str) -> str:
    """Chuẩn hóa thời gian về 'HH:MM dd/mm/yyyy' (chấp nhận thứ tự đảo hoặc chữ 'ngày')"""
    t = re.search(r"(\d{1,2})[:h](\d{2})", datetime_str)
    if not t:
        return datetime_str.strip()
    return f"{int(t.group(1)):02d}:{t.group(2)} {_normalize_date(datetime_str)}"


def _find_doctor(doctor_name: str) -> Optional[Dict[str, Any]]:
    """Tìm bác sĩ theo tên (không phân biệt hoa thường, cho phép thiếu học hàm)"""
    query = doctor_name.strip().lower()
    for doctors in MOCK_DOCTOR_SCHEDULES.values():
        for doctor in doctors:
            full_name = doctor["doctor_name"].lower()
            if query and (query in full_name or full_name in query):
                return doctor
    return None


def execute_doctor_schedule_query(specialty: str, date: str = "") -> str:
    """Thực thi tra cứu lịch làm việc còn trống của bác sĩ theo chuyên khoa"""
    doctors = MOCK_DOCTOR_SCHEDULES.get(_normalize_specialty(specialty))
    if not doctors:
        return json.dumps({
            "status": "NOT_FOUND",
            "message": (
                f"Không tìm thấy chuyên khoa '{specialty}' trong hệ thống đặt lịch Vinmec. "
                f"Các chuyên khoa hiện có: {', '.join(k.title() for k in MOCK_DOCTOR_SCHEDULES)}."
            )
        }, ensure_ascii=False)

    target_date = _normalize_date(date) if date else ""
    results: List[Dict[str, Any]] = []
    for doctor in doctors:
        slots = [s for s in doctor["available_slots"] if not target_date or s.endswith(target_date)]
        if slots:
            results.append({**doctor, "available_slots": slots})

    date_label = f" ngày {target_date}" if target_date else ""
    if not results:
        return json.dumps({
            "status": "NO_AVAILABLE_SLOT",
            "specialty": specialty,
            "message": f"Chuyên khoa {specialty} không còn khung giờ trống{date_label}."
        }, ensure_ascii=False)

    summary = "; ".join(f"{d['doctor_name']} ({d['hospital']}): {', '.join(d['available_slots'])}" for d in results)
    return json.dumps({
        "status": "SUCCESS",
        "specialty": specialty,
        "doctors": results,
        "message": f"Lịch trống chuyên khoa {specialty}{date_label} — {summary}."
    }, ensure_ascii=False)


def execute_schedule_appointment(patient_id: str, doctor_name: str, datetime_str: str) -> str:
    """Thực thi đặt lịch khám bệnh (kiểm tra bệnh nhân, bác sĩ và khung giờ còn trống)"""
    pid = patient_id.strip().upper()
    patient = MOCK_PATIENTS.get(pid)
    if not patient:
        return json.dumps({
            "status": "NOT_FOUND",
            "message": f"Không tìm thấy hồ sơ bệnh nhân có mã '{patient_id}'. Vui lòng kiểm tra lại mã bệnh nhân."
        }, ensure_ascii=False)

    doctor = _find_doctor(doctor_name)
    if not doctor:
        return json.dumps({
            "status": "NOT_FOUND",
            "message": f"Không tìm thấy bác sĩ '{doctor_name}' trong hệ thống Vinmec."
        }, ensure_ascii=False)

    slot = _normalize_datetime(datetime_str)
    if slot not in doctor["available_slots"]:
        return json.dumps({
            "status": "SLOT_UNAVAILABLE",
            "doctor_name": doctor["doctor_name"],
            "requested_slot": slot,
            "available_slots": doctor["available_slots"],
            "message": f"Khung giờ {slot} của {doctor['doctor_name']} không còn trống."
        }, ensure_ascii=False)

    doctor["available_slots"].remove(slot)
    return json.dumps({
        "status": "SUCCESS",
        "booking_id": f"VM-{pid}-{slot[:2]}{slot[3:5]}",
        "patient_id": pid,
        "patient_name": patient["full_name"],
        "doctor_name": doctor["doctor_name"],
        "hospital": doctor["hospital"],
        "datetime": slot,
        "message": (
            f"Đặt lịch thành công cho bệnh nhân {patient['full_name']} ({pid}) "
            f"với {doctor['doctor_name']} tại {doctor['hospital']} vào lúc {slot}."
        )
    }, ensure_ascii=False)


# Router gọi tool thực tế
TOOL_ROUTER = {
    "doctor_schedule_query": execute_doctor_schedule_query,
    "schedule_appointment": execute_schedule_appointment
}

def dispatch_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Hàm trung chuyển thực thi tool"""
    if tool_name in TOOL_ROUTER:
        try:
            return TOOL_ROUTER[tool_name](**arguments)
        except Exception as e:
            return json.dumps({"status": "EXECUTION_ERROR", "error": str(e)}, ensure_ascii=False)
    return json.dumps({"status": "UNKNOWN_TOOL", "error": f"Tool '{tool_name}' không tồn tại!"}, ensure_ascii=False)
