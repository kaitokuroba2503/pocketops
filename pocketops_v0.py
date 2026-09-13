#!/usr/bin/env python3
"""
PocketOps v0 — Tracer bullet (Giai đoạn 1) — bản đã QA lại
=============================================================
Mục tiêu: chứng minh luồng end-to-end chạy được trên Termux, có sandbox
cho code VÀ điều khiển điện thoại thật (termux-api + ADB), đã tự review
và vá 20 điểm yếu + thêm 5 tính năng (xem lịch sử chat để biết chi tiết
từng điểm).

CHƯA TEST trên Termux/Android thật — Kaito cần tự test theo hướng dẫn
"Bàn giao" ở cuối file, báo lại traceback thật nếu có lỗi.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/.pocketops/history.db")
SANDBOX_ROOT = os.path.expanduser("~/.pocketops/sandbox")
CONFIG_PATH = os.path.expanduser("~/.pocketops/config.json")
SCREENSHOT_DIR = os.path.expanduser("~/.pocketops/screenshots")

VERSION = "0.4.0"  # [MỚI] fix #12 — biết đang chạy đúng bản nào, tránh nhầm file -1.py
MAX_LOG_OUTPUT_CHARS = 4000  # [MỚI] fix #15 — giới hạn output lưu vào DB
MAX_HISTORY_ROWS = 500  # [MỚI] fix #5 — tự dọn lịch sử cũ, không phình vô hạn

DRY_RUN = False


# =========================================================================
# Hạ tầng dùng chung (KHÔNG đổi logic cũ, chỉ tối ưu I/O — fix #15)
# =========================================================================

def _ensure_dirs() -> None:
    """Tạo thư mục làm việc nếu chưa có. Bỏ qua nhanh nếu đã tồn tại (fix #15)."""
    if os.path.isdir(SANDBOX_ROOT) and os.path.isdir(SCREENSHOT_DIR):
        return
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(SANDBOX_ROOT, exist_ok=True)
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


_DB_INITIALIZED = False  # fix #15: chỉ CREATE TABLE 1 lần mỗi lần chạy


def _init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 3000")  # [MỚI] fix #4: tránh lỗi khi 2 lệnh chạy trùng
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            command TEXT NOT NULL,
            output TEXT,
            status TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    _DB_INITIALIZED = True


def _log_session(command: str, output: str, status: str) -> None:
    """Ghi lại 1 phiên chạy vào SQLite. Tự cắt bớt output quá dài (fix #15)
    và tự dọn bớt lịch sử cũ nếu vượt MAX_HISTORY_ROWS (fix #5)."""
    if output and len(output) > MAX_LOG_OUTPUT_CHARS:
        output = output[:MAX_LOG_OUTPUT_CHARS] + f"\n...(đã cắt bớt, output gốc dài hơn {MAX_LOG_OUTPUT_CHARS} ký tự)"
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute(
        "INSERT INTO sessions (created_at, command, output, status) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), command, output, status),
    )
    conn.execute(
        """DELETE FROM sessions WHERE id NOT IN (
            SELECT id FROM sessions ORDER BY id DESC LIMIT ?
        )""",
        (MAX_HISTORY_ROWS,),
    )
    conn.commit()
    conn.close()


def get_history(so_luong: int = 10) -> list[tuple]:
    """[MỚI] fix #11 — đọc lại N phiên gần nhất để xem lại lịch sử."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 3000")
    rows = conn.execute(
        "SELECT created_at, command, status FROM sessions ORDER BY id DESC LIMIT ?",
        (so_luong,),
    ).fetchall()
    tong_so = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.close()
    return rows, tong_so


def clear_history() -> int:
    """[MỚI] tính năng thêm — xoá sạch lịch sử theo yêu cầu chủ động của Kaito."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 3000")
    so_dong = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()
    return so_dong


def _load_config() -> dict:
    """[MỚI] fix #8 — đọc IP:PORT ADB lần kết nối gần nhất."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(data: dict) -> None:
    """[MỚI] fix #8 — lưu lại IP:PORT ADB để lần sau gợi ý nhanh."""
    existing = _load_config()
    existing.update(data)
    with open(CONFIG_PATH, "w") as f:
        json.dump(existing, f)


def _safe_int(value: str, label: str) -> int:
    """[MỚI] fix #1 — parse số an toàn, báo lỗi tiếng Việt thay vì traceback."""
    try:
        return int(value)
    except (ValueError, TypeError):
        raise PocketOpsInputError(f"'{value}' không phải là số hợp lệ cho {label}.")


class PocketOpsInputError(Exception):
    """[MỚI] fix #2 — lỗi đầu vào do người dùng, không phải bug, in gọn không traceback."""
    pass


# =========================================================================
# Sandbox chạy code (GIỮ NGUYÊN 100% logic cũ)
# =========================================================================

SANDBOX_DISTRO = "alpine"  # nhẹ nhất trong các distro proot-distro hỗ trợ


def _proot_distro_installed() -> bool:
    """Kiểm tra đã cài proot-distro (lệnh) chưa."""
    return shutil.which("proot-distro") is not None


def _sandbox_distro_ready() -> bool:
    """fix (12/9/2026, lần 2): bản trước đoán sai đường dẫn rootfs của
    proot-distro -> báo nhầm 'chưa cài' dù Kaito đã cài xong thành công.
    Đổi cách kiểm tra: hỏi thẳng proot-distro qua 'list' thay vì tự đoán
    đường dẫn file, đáng tin hơn vì không phụ thuộc cấu trúc thư mục nội bộ
    có thể đổi giữa các phiên bản proot-distro."""
    if not _proot_distro_installed():
        return False
    try:
        result = subprocess.run(
            ["proot-distro", "list"], capture_output=True, text=True, timeout=10
        )
        output = result.stdout or ""
        # dòng của distro đã cài thường có chữ 'Installed' cạnh tên
        for line in output.splitlines():
            if SANDBOX_DISTRO in line.lower() and "installed" in line.lower():
                return True
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def setup_sandbox() -> tuple[str, str]:
    """Cài Alpine Linux con qua proot-distro — chạy 1 lần duy nhất, có thể mất vài phút.

    Đây LÀ bước dựng "điện thoại ảo" thật sự — sau bước này, run_in_sandbox
    mới cô lập filesystem đúng như thiết kế ban đầu.
    """
    if not _proot_distro_installed():
        return "Chưa cài proot-distro. Chạy: pkg install proot-distro", "error"
    print("[PocketOps] Đang tải Alpine Linux (~vài chục MB, có thể mất 1-3 phút tuỳ mạng)...")
    try:
        result = subprocess.run(
            ["proot-distro", "install", SANDBOX_DISTRO],
            capture_output=True, text=True, timeout=600,
        )
        output = (result.stdout or "") + (result.stderr or "")
        status = "ok" if result.returncode == 0 else "error"
        return output.strip(), status
    except subprocess.TimeoutExpired:
        return "Cài Alpine quá 10 phút, đã huỷ — kiểm tra lại mạng rồi thử lại.", "error"


def _sandbox_timeout() -> int:
    """[MỚI] fix #6 — cho phép chỉnh timeout qua biến môi trường thay vì cứng 60s."""
    try:
        return int(os.environ.get("POCKETOPS_SANDBOX_TIMEOUT", "60"))
    except ValueError:
        return 60


def run_in_sandbox(shell_command: str) -> tuple[str, str]:
    """Chạy 1 lệnh shell — cô lập THẬT qua proot-distro (Alpine Linux con)."""
    if not _proot_distro_installed():
        print("[CẢNH BÁO] Chưa cài proot-distro -> chạy KHÔNG cô lập. "
              "Cài: pkg install proot-distro", file=sys.stderr)
        full_cmd = ["/bin/sh", "-c", shell_command]
    else:
        full_cmd = [
            "proot-distro", "login", SANDBOX_DISTRO,
            "--isolated",
            "--bind", f"{SANDBOX_ROOT}:/sandbox",
            "--work-dir", "/sandbox",
            "--", "/bin/sh", "-c", shell_command,
        ]

    if DRY_RUN:
        return f"[DRY-RUN] Sẽ chạy: {' '.join(full_cmd)}", "ok"

    timeout_s = _sandbox_timeout()
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout_s)
        output = result.stdout + result.stderr
        status = "ok" if result.returncode == 0 else "error"

        if status == "error" and "not installed" in output.lower():
            print(f"[CẢNH BÁO] proot-distro báo chưa cài '{SANDBOX_DISTRO}' -> "
                  f"chạy 1 lần: python pocketops_v0.py setup-sandbox", file=sys.stderr)
            fallback_cmd = ["/bin/sh", "-c", shell_command]
            fb_result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=timeout_s)
            return fb_result.stdout + fb_result.stderr, ("ok" if fb_result.returncode == 0 else "error")

        return output, status
    except subprocess.TimeoutExpired:
        # [MỚI] fix #18: gợi ý luôn cách chỉnh timeout thay vì chỉ báo lỗi suông
        return (
            f"Lệnh chạy quá {timeout_s} giây, đã bị dừng. Cần lâu hơn? Đặt biến môi "
            f"trường: export POCKETOPS_SANDBOX_TIMEOUT=<số giây> rồi chạy lại.",
            "error",
        )
    except FileNotFoundError:
        return "Không tìm thấy proot-distro/sh khi chạy thật. Cài lại: pkg install proot-distro", "error"


# =========================================================================
# Điều khiển điện thoại qua Termux:API (giữ nguyên logic, không đổi)
# =========================================================================

PHONE_TIMEOUT = 8


def _run_termux_api(args: list[str], input_text: str | None = None) -> tuple[str, str]:
    if DRY_RUN:
        return f"[DRY-RUN] Sẽ chạy: {' '.join(args)}", "ok"
    try:
        result = subprocess.run(
            args, input=input_text, capture_output=True, text=True, timeout=PHONE_TIMEOUT
        )
        output = (result.stdout or "") + (result.stderr or "")
        status = "ok" if result.returncode == 0 else "error"
        return output.strip(), status
    except subprocess.TimeoutExpired:
        return f"Lệnh {args[0]} quá {PHONE_TIMEOUT}s, đã huỷ (tránh treo app).", "error"
    except FileNotFoundError:
        return f"Không tìm thấy '{args[0]}'. Cài bằng: pkg install termux-api", "error"


def phone_notify(title: str, message: str) -> tuple[str, str]:
    return _run_termux_api(["termux-notification", "--title", title, "--content", message])


def phone_toast(message: str) -> tuple[str, str]:
    return _run_termux_api(["termux-toast", message])


def phone_vibrate(duration_ms: int = 300) -> tuple[str, str]:
    if duration_ms > MAX_VIBRATE_MS:  # [MỚI] fix #8
        return (
            f"Từ chối rung {duration_ms}ms — vượt giới hạn an toàn {MAX_VIBRATE_MS}ms. "
            f"Muốn rung lâu hơn thật? Đặt: export POCKETOPS_MAX_VIBRATE_MS=<số ms>",
            "error",
        )
    return _run_termux_api(["termux-vibrate", "-d", str(duration_ms)])


def phone_clipboard_set(text: str) -> tuple[str, str]:
    return _run_termux_api(["termux-clipboard-set"], input_text=text)


def phone_clipboard_get() -> tuple[str, str]:
    return _run_termux_api(["termux-clipboard-get"])


def phone_open_url(url: str) -> tuple[str, str]:
    return _run_termux_api(["termux-open-url", url])


def phone_tts_speak(text: str) -> tuple[str, str]:
    return _run_termux_api(["termux-tts-speak", text])


def phone_battery_status() -> tuple[str, str]:
    return _run_termux_api(["termux-battery-status"])


# =========================================================================
# Điều khiển màn hình thật qua ADB — bản đã QA
# =========================================================================
# THIẾT LẬP 1 LẦN (trên điện thoại, ngoài Termux):
#   1. Cài đặt -> Giới thiệu điện thoại -> bấm 7 lần "Số bản dựng"
#   2. Tuỳ chọn nhà phát triển -> bật "Gỡ lỗi không dây"
#   3. "Ghép nối bằng mã ghép nối" -> lấy IP:PORT + mã 6 số
#   4. Trong Termux:
#        pkg install android-tools
#        adb pair <IP>:<PORT_GHÉP_NỐI>
#        adb connect <IP>:<PORT_KẾT_NỐI>
#        adb devices   # phải thấy dòng kết thúc bằng "device"
#   Sau khi restart điện thoại, IP/port đổi -> phải "adb connect" lại.
#
# TUỲ CHỌN NÂNG CAO (biến môi trường):
#   POCKETOPS_ADB_SERIAL=<serial>       # fix #7: chọn đúng máy khi có nhiều thiết bị
#   POCKETOPS_SKIP_ADB_CHECK=1          # fix #4: bỏ qua check 'adb devices' mỗi lệnh cho nhanh
#   POCKETOPS_REQUIRE_CONFIRM=1         # fix #18: bắt xác nhận trước hành động rủi ro cao

ADB_TIMEOUT = 10
ADB_PULL_TIMEOUT = 25  # [MỚI] fix #7: ảnh chụp màn hình cần thời gian tải lâu hơn lệnh thường
MAX_VIBRATE_MS = int(os.environ.get("POCKETOPS_MAX_VIBRATE_MS", "5000"))  # [MỚI] fix #8

# fix #9: mở rộng danh sách keycode phổ biến
KNOWN_KEYEVENTS = {
    "BACK", "HOME", "ENTER", "DEL", "MENU", "APP_SWITCH",
    "VOLUME_UP", "VOLUME_DOWN", "VOLUME_MUTE", "POWER", "TAB", "ESCAPE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER",
    "SPACE", "CAMERA", "MEDIA_PLAY_PAUSE", "MEDIA_NEXT", "MEDIA_PREVIOUS",
    "BRIGHTNESS_UP", "BRIGHTNESS_DOWN", "NOTIFICATION", "SEARCH",
}

HIGH_RISK_ACTIONS = {"tap", "swipe", "type", "open-app"}  # fix #18


def _adb_base_args() -> list[str]:
    """fix #7: gắn -s <serial> nếu Kaito chỉ định, tránh gửi nhầm máy."""
    serial = os.environ.get("POCKETOPS_ADB_SERIAL")
    return ["-s", serial] if serial else []


def _run_adb(args: list[str], _retry: bool = True, timeout: int | None = None) -> tuple[str, str]:
    """fix #16: mở rộng danh sách cụm lỗi tạm thời để thử lại, không chỉ 2 cụm cũ."""
    if DRY_RUN:
        return f"[DRY-RUN] Sẽ chạy: adb {' '.join(_adb_base_args() + args)}", "ok"
    full = ["adb"] + _adb_base_args() + args
    timeout = timeout or ADB_TIMEOUT
    LOI_TAM_THOI = ("device offline", "no devices", "closed", "connection refused", "device unauthorized")
    try:
        result = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        output = (result.stdout or "") + (result.stderr or "")
        status = "ok" if result.returncode == 0 else "error"
        if status == "error" and _retry and any(cum in output.lower() for cum in LOI_TAM_THOI):
            return _run_adb(args, _retry=False, timeout=timeout)
        return output.strip(), status
    except subprocess.TimeoutExpired:
        return f"Lệnh adb quá {timeout}s, đã huỷ.", "error"
    except FileNotFoundError:
        return "Không tìm thấy 'adb'. Cài bằng: pkg install android-tools", "error"


def _adb_is_connected() -> bool:
    if os.environ.get("POCKETOPS_SKIP_ADB_CHECK") == "1":
        return True
    output, status = _run_adb(["devices"])
    if status != "ok":
        return False
    lines = [l for l in output.splitlines() if l.strip() and "List of devices" not in l]
    serial_can_khop = os.environ.get("POCKETOPS_ADB_SERIAL")
    if serial_can_khop:
        # fix #3: khi có chỉ định serial, chỉ coi là "đã kết nối" nếu ĐÚNG serial đó có mặt
        connected = any(
            l.strip().startswith(serial_can_khop) and l.strip().endswith("device")
            for l in lines
        )
    else:
        connected = any(l.strip().endswith("device") for l in lines)
    if connected:
        _save_config({"last_connected_at": datetime.now(timezone.utc).isoformat()})
    return connected


def _require_adb() -> tuple[str, str] | None:
    if not _adb_is_connected():
        return (
            "Chưa kết nối ADB. Chạy 'adb connect <IP>:<PORT>' trước "
            "(xem hướng dẫn thiết lập trong code). Nếu chắc chắn đã kết nối và chỉ "
            "muốn bỏ qua bước kiểm tra cho nhanh, đặt POCKETOPS_SKIP_ADB_CHECK=1.",
            "error",
        )
    return None


def _confirm_high_risk(action_name: str) -> bool:
    """fix #18: tuỳ chọn bắt xác nhận trước hành động rủi ro cao."""
    if os.environ.get("POCKETOPS_REQUIRE_CONFIRM") != "1":
        return True
    reply = input(f"[XÁC NHẬN] Sắp thực hiện '{action_name}' — gõ y để tiếp tục: ")
    return reply.strip().lower() == "y"


def phone_tap(x: int, y: int) -> tuple[str, str]:
    print("[CẢNH BÁO] Sắp chạm thật vào màn hình.")
    if not _confirm_high_risk("tap"):
        return "Đã huỷ theo yêu cầu xác nhận.", "error"
    err = _require_adb()
    if err:
        return err
    return _run_adb(["shell", "input", "tap", str(x), str(y)])


def phone_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> tuple[str, str]:
    # fix #16: mặc định tăng lên 400ms để hệ thống nhận đúng là vuốt, không phải chạm
    print("[CẢNH BÁO] Sắp vuốt thật trên màn hình.")
    if not _confirm_high_risk("swipe"):
        return "Đã huỷ theo yêu cầu xác nhận.", "error"
    err = _require_adb()
    if err:
        return err
    return _run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])


def phone_keyevent(keycode: str) -> tuple[str, str]:
    keycode_upper = keycode.upper()
    if keycode_upper not in KNOWN_KEYEVENTS:  # fix #10
        print(f"[GỢI Ý] '{keycode}' không nằm trong danh sách phổ biến "
              f"({', '.join(sorted(KNOWN_KEYEVENTS))}) — vẫn thử gửi, "
              "nếu lỗi kiểm tra lại chính tả.")
    err = _require_adb()
    if err:
        return err
    return _run_adb(["shell", "input", "keyevent", keycode_upper])


def _escape_adb_text(text: str) -> str:
    """fix #13: escape dấu '\\' TRƯỚC TIÊN, nếu không các ký tự escape thêm
    sau sẽ tự nhiên chứa '\\' chưa escape, làm hỏng chuỗi cuối cùng."""
    text = text.replace("\\", "\\\\")  # phải làm đầu tiên
    replacements = {
        " ": "%s",
        "&": "\\&",
        "(": "\\(",
        ")": "\\)",
        "<": "\\<",
        ">": "\\>",
        "|": "\\|",
        ";": "\\;",
        '"': '\\"',
        "'": "\\'",
        "$": "\\$",
        "`": "\\`",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


def phone_type_text(text: str) -> tuple[str, str]:
    print("[CẢNH BÁO] Sắp gõ text thật vào ô đang mở — đảm bảo đúng ô trước khi chạy.")
    if not _confirm_high_risk("type"):
        return "Đã huỷ theo yêu cầu xác nhận.", "error"
    err = _require_adb()
    if err:
        return err
    safe_text = _escape_adb_text(text)
    return _run_adb(["shell", "input", "text", safe_text])


def phone_open_app(package_name: str) -> tuple[str, str]:
    if not _confirm_high_risk("open-app"):
        return "Đã huỷ theo yêu cầu xác nhận.", "error"
    err = _require_adb()
    if err:
        return err
    output, status = _run_adb(
        ["shell", "cmd", "package", "resolve-activity", "--brief", package_name]
    )
    # fix #2: parse an toàn — không index [-1] mù, tự bắt trường hợp output
    # toàn khoảng trắng hoặc không có dòng nào chứa "/"
    dong_co_component = [l.strip() for l in output.splitlines() if "/" in l] if output else []
    if status == "ok" and dong_co_component:
        component = dong_co_component[-1]
        result = _run_adb(["shell", "am", "start", "-n", component])
        if result[1] == "ok":
            return result
    fallback = _run_adb(
        ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"]
    )
    if fallback[1] == "error":
        return (
            fallback[0] + f"\n[GỢI Ý] Kiểm tra lại chính tả package '{package_name}' "
            "(ví dụ đúng là com.whatsapp, không phải com.whatapp).",
            "error",
        )
    return fallback


def phone_screenshot() -> tuple[str, str]:
    err = _require_adb()
    if err:
        return err
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    device_path = f"/sdcard/{filename}"
    local_path = os.path.join(SCREENSHOT_DIR, filename)
    _, status1 = _run_adb(["shell", "screencap", "-p", device_path])
    if status1 != "ok":
        return "Chụp màn hình thất bại.", "error"
    if DRY_RUN:
        return f"[DRY-RUN] Sẽ tải ảnh về {local_path}", "ok"
    result = subprocess.run(
        ["adb"] + _adb_base_args() + ["pull", device_path, local_path],
        capture_output=True, text=True, timeout=ADB_PULL_TIMEOUT,  # fix #7
    )
    _, status_don_dep = _run_adb(["shell", "rm", device_path])  # fix #10: không bỏ qua kết quả nữa
    if result.returncode != 0:
        return "Tải ảnh về Termux thất bại: " + result.stderr, "error"
    canh_bao_don_dep = "" if status_don_dep == "ok" else \
        "\n[LƯU Ý] Không dọn được file tạm trên máy — ảnh vẫn còn ở /sdcard/, không ảnh hưởng file đã tải về."
    return f"Đã lưu ảnh chụp màn hình: {local_path}{canh_bao_don_dep}", "ok"


def list_screenshots() -> tuple[str, str]:
    """[MỚI] tính năng thêm — liệt kê các ảnh đã chụp, kèm kích thước/thời gian."""
    if not os.path.isdir(SCREENSHOT_DIR):
        return "Chưa có ảnh chụp màn hình nào.", "ok"
    files = sorted(os.listdir(SCREENSHOT_DIR), reverse=True)
    if not files:
        return "Chưa có ảnh chụp màn hình nào.", "ok"
    dong = []
    for f in files:
        full = os.path.join(SCREENSHOT_DIR, f)
        size_kb = os.path.getsize(full) / 1024
        dong.append(f"  {f} ({size_kb:.0f} KB)")
    return f"Có {len(files)} ảnh:\n" + "\n".join(dong), "ok"


def phone_adb_status() -> tuple[str, str]:
    # fix #1: gọi qua _adb_is_connected() để timestamp được cập nhật đúng lúc kiểm tra,
    # không chỉ khi 1 hành động high-risk khác vô tình trigger nó trước đó
    dang_ket_noi = _adb_is_connected()
    output, status = _run_adb(["devices", "-l"])
    config = _load_config()
    last = config.get("last_connected_at", "chưa từng kết nối thành công")
    serial = os.environ.get("POCKETOPS_ADB_SERIAL", "(không chỉ định, dùng máy mặc định)")
    trang_thai_hien_tai = "ĐANG kết nối" if dang_ket_noi else "KHÔNG kết nối"
    return (
        f"Trạng thái hiện tại: {trang_thai_hien_tai}\n"
        f"Serial đang chỉ định: {serial}\n"
        f"Kết nối thành công gần nhất: {last}\n"
        f"Danh sách thiết bị:\n{output}"
    ), status


PHONE_ACTIONS = {
    "notify": lambda args: phone_notify(args[0], " ".join(args[1:])),
    "toast": lambda args: phone_toast(" ".join(args)),
    "vibrate": lambda args: phone_vibrate(_safe_int(args[0], "thời gian rung (ms)") if args else 300),
    "clipboard-set": lambda args: phone_clipboard_set(" ".join(args)),
    "clipboard-get": lambda args: phone_clipboard_get(),
    "open": lambda args: phone_open_url(args[0]),
    "speak": lambda args: phone_tts_speak(" ".join(args)),
    "battery": lambda args: phone_battery_status(),
    # nhóm ADB:
    "tap": lambda args: phone_tap(_safe_int(args[0], "toạ độ x"), _safe_int(args[1], "toạ độ y")),
    "swipe": lambda args: phone_swipe(
        _safe_int(args[0], "x1"), _safe_int(args[1], "y1"),
        _safe_int(args[2], "x2"), _safe_int(args[3], "y2"),
        _safe_int(args[4], "duration_ms") if len(args) > 4 else 400,
    ),
    "keyevent": lambda args: phone_keyevent(args[0]),
    "type": lambda args: phone_type_text(" ".join(args)),
    "open-app": lambda args: phone_open_app(args[0]),
    "screenshot": lambda args: phone_screenshot(),
    "adb-status": lambda args: phone_adb_status(),
    "list-screenshots": lambda args: list_screenshots(),  # [MỚI]
}

ADB_ACTIONS = {"tap", "swipe", "keyevent", "type", "open-app", "screenshot", "adb-status"}


# =========================================================================
# NGÔN NGỮ TỰ NHIÊN — "phone say" — hiểu câu tiếng Việt thường, không cần
# nhớ đúng cú pháp lệnh. Đây là bản ĐOÁN TỪ KHOÁ (không cần API key,
# không thông minh bằng Siri thật) — nếu muốn thông minh hơn, cần nối
# với Claude API (tính năng nâng cấp, chưa làm ở bản này).
# =========================================================================

# Mỗi mục: (các từ khoá phải xuất hiện, hàm hành động, cách lấy tham số từ câu)
_NLU_RULES = [
    (["chụp màn hình", "chup man hinh", "screenshot"], "screenshot"),
    (["về màn hình chính", "ve man hinh chinh", "home"], "keyevent:HOME"),
    (["quay lại", "quay lai", "back", "thoát", "thoat"], "keyevent:BACK"),
    (["rung", "vibrate"], "vibrate"),
    (["pin", "battery"], "battery"),
    (["xem lịch sử", "xem lich su", "history"], "__history__"),
]


def parse_natural_language(cau: str) -> tuple[str, list[str]] | None:
    """Đoán hành động từ 1 câu tiếng Việt tự nhiên dựa theo từ khoá.
    Trả về (action, args) hoặc None nếu không đoán được. Đây là cách ĐƠN
    GIẢN, không phải AI thật — chỉ khớp cụm từ cố định."""
    cau_thuong = cau.lower().strip()

    # "mở <tên app>" -> cần map tên app quen thuộc sang package thật
    if "mở" in cau_thuong or "mo " in cau_thuong:
        APP_ALIASES = {
            "liên quân": "com.garena.game.kgvn", "lien quan": "com.garena.game.kgvn",
            "đồng hồ": "com.google.android.deskclock", "dong ho": "com.google.android.deskclock",
            "chrome": "com.android.chrome",
        }
        for ten, package in APP_ALIASES.items():
            if ten in cau_thuong:
                return ("open-app", [package])
        return None  # nhận ra ý định "mở" nhưng không rõ app nào -> không đoán mò

    for tu_khoa_list, action in _NLU_RULES:
        if any(tk in cau_thuong for tk in tu_khoa_list):
            if ":" in action:
                act, arg = action.split(":")
                return (act, [arg])
            return (action, [])
    return None


# =========================================================================
# Gemini — nâng cấp "say" lên hiểu câu tự nhiên thật, không chỉ khớp từ khoá
# =========================================================================
# Cần: export GEMINI_API_KEY="AIzaSy..." (lấy free tại aistudio.google.com)
# CHƯA TEST được với key thật trong sandbox này — Kaito test và gửi lại
# lỗi/kết quả thật để debug đúng, không đoán mò thêm.

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_TIMEOUT = 15

# Danh sách hành động Gemini ĐƯỢC PHÉP chọn — đây là lớp an toàn quan trọng:
# dù AI trả lời gì, chỉ chạy nếu action nằm trong đúng danh sách này.
_GEMINI_ALLOWED_ACTIONS = sorted(list(PHONE_ACTIONS.keys()) + ["__history__", "__unknown__"])


def call_gemini(cau: str) -> tuple[str, list[str]] | None:
    """Gửi câu tiếng Việt cho Gemini, yêu cầu trả về đúng JSON hành động.
    Trả về None nếu lỗi bất kỳ (mạng, key sai, JSON hỏng...) để bên gọi
    tự fallback sang cách đoán từ khoá, không bao giờ raise ra ngoài."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    prompt = f"""Bạn là bộ phân tích lệnh điều khiển điện thoại. Đọc câu tiếng Việt của
người dùng và trả về DUY NHẤT 1 dòng JSON, không giải thích gì thêm, đúng
định dạng: {{"action": "<tên hành động>", "args": [<tham số dạng chuỗi>]}}

Các hành động hợp lệ DUY NHẤT (không được bịa ra hành động khác):
{', '.join(_GEMINI_ALLOWED_ACTIONS)}

Nếu người dùng muốn mở app, dùng action "open-app" với args là tên package
Android nếu bạn biết chắc (ví dụ Liên Quân là com.garena.game.kgvn), nếu
không chắc chắn thì trả về action "__unknown__".
Nếu không hiểu câu hoặc câu không liên quan điều khiển điện thoại, trả về
{{"action": "__unknown__", "args": []}}

Câu của người dùng: "{cau}\""""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(text)
        action = parsed.get("action")
        args = parsed.get("args", [])
        if action not in _GEMINI_ALLOWED_ACTIONS:
            return None
        if action == "__unknown__":
            return None
        return (action, [str(a) for a in args])
    except Exception:
        return None


def run_phone_action(action: str, args: list[str]) -> tuple[str, str]:
    """fix #2 + #3: bọc lỗi thiếu tham số / sai kiểu thành thông báo tiếng Việt gọn."""
    handler = PHONE_ACTIONS.get(action)
    if handler is None:
        available = ", ".join(PHONE_ACTIONS.keys())
        return f"Không có hành động '{action}'. Các hành động hỗ trợ: {available}", "error"
    try:
        return handler(args)
    except IndexError:
        return f"Thiếu tham số cho hành động '{action}'. Xem lại cú pháp bằng: python pocketops_v0.py", "error"
    except PocketOpsInputError as e:
        return str(e), "error"


# =========================================================================
# CLI
# =========================================================================

def _print_usage() -> None:
    print(f"PocketOps v{VERSION}")  # [MỚI] fix #12
    print("Dùng:")
    print("  python pocketops_v0.py setup-sandbox    # chạy 1 LẦN DUY NHẤT để dựng sandbox Alpine")
    print('  python pocketops_v0.py sandbox "<lệnh shell chạy trong phòng cách ly>"')
    print("  python pocketops_v0.py sandbox-run <file.py>   # copy + chạy file, không cần gõ heredoc dài")
    print("  python pocketops_v0.py phone <hành động> [tham số...] [--dry-run]")
    print("  python pocketops_v0.py history [số_lượng]      # xem lại lịch sử đã chạy")
    print("  python pocketops_v0.py clear-history            # xoá sạch lịch sử")
    print('  python pocketops_v0.py say "<câu tiếng Việt tự nhiên>"   # vd: say "mở liên quân"')
    print()
    print("  -- Nhóm termux-api (không cần ADB) --")
    print('  phone notify "Tiêu đề" "Nội dung"')
    print('  phone toast "Xin chào"')
    print("  phone vibrate 500")
    print("  phone clipboard-set / clipboard-get")
    print("  phone open <url>")
    print('  phone speak "Việc đã hoàn tất"')
    print("  phone battery")
    print()
    print("  -- Nhóm ADB (cần adb connect trước, xem hướng dẫn trong code) --")
    print("  phone tap 500 1200")
    print("  phone swipe 500 1500 500 500 400")
    print("  phone keyevent BACK")
    print('  phone type "xin chào"')
    print("  phone open-app com.android.chrome")
    print("  phone screenshot            # chụp màn hình trước khi tap mù toạ độ")
    print("  phone list-screenshots       # xem danh sách ảnh đã chụp")
    print("  phone adb-status             # kiểm tra đang kết nối máy nào")
    print()
    print("  Biến môi trường tuỳ chọn: POCKETOPS_ADB_SERIAL, POCKETOPS_SKIP_ADB_CHECK=1, "
          "POCKETOPS_REQUIRE_CONFIRM=1, POCKETOPS_SANDBOX_TIMEOUT=<giây>, "
          "POCKETOPS_MAX_VIBRATE_MS=<ms>")


def main() -> None:
    global DRY_RUN
    try:
        argv = sys.argv[1:]
        if "--dry-run" in argv:  # fix #11
            DRY_RUN = True
            argv = [a for a in argv if a != "--dry-run"]

        _ensure_dirs()
        _init_db()

        if len(argv) < 1:
            _print_usage()
            sys.exit(1)

        mode = argv[0]

        if mode == "history":
            # [MỚI] tính năng thêm — xem lại lịch sử đã chạy
            so_luong = 10
            if len(argv) > 1:
                try:
                    so_luong = int(argv[1])
                except ValueError:
                    print(f"'{argv[1]}' không phải số hợp lệ, dùng mặc định 10.")
            rows, tong_so = get_history(so_luong)
            print(f"[PocketOps] Tổng số lệnh đã lưu: {tong_so} (hiện {len(rows)} lệnh gần nhất)")
            for created_at, command, status in rows:
                print(f"  [{status:5}] {created_at}  {command}")

        elif mode == "clear-history":
            so_dong = clear_history()
            print(f"[PocketOps] Đã xoá {so_dong} dòng lịch sử.")

        elif mode == "say":
            # [MỚI] tính năng thêm — hiểu câu tiếng Việt tự nhiên, không cần đúng cú pháp lệnh
            if len(argv) < 2:
                print('Dùng: python pocketops_v0.py say "<câu tiếng Việt, ví dụ: mở liên quân>"')
                sys.exit(1)
            cau = " ".join(argv[1:])
            ket_qua = call_gemini(cau)  # thử AI thật trước nếu có GEMINI_API_KEY
            nguon = "Gemini AI"
            if ket_qua is None:
                ket_qua = parse_natural_language(cau)  # fallback đoán từ khoá
                nguon = "đoán từ khoá (không có/lỗi Gemini)"
            if ket_qua is None:
                print(f"[PocketOps] Không hiểu câu '{cau}'. Đây là bản đoán từ khoá đơn giản, "
                      "chưa thông minh như Siri thật — dùng đúng cú pháp lệnh (xem: python pocketops_v0.py) "
                      "hoặc thử câu khác rõ ý hơn.")
                sys.exit(1)
            action, args = ket_qua
            if action == "__history__":
                rows, tong_so = get_history(10)
                print(f"[PocketOps] Tổng số lệnh đã lưu: {tong_so}")
                for created_at, command, status in rows:
                    print(f"  [{status:5}] {created_at}  {command}")
            else:
                print(f"[PocketOps] Hiểu là ({nguon}): {action} {' '.join(args)}")
                output, status = run_phone_action(action, args)
                print("---- Kết quả ----")
                print(output if output else "(không có output, coi như thành công)")
                _log_session(f"say: {cau} -> {action}", output, status)
                if status == "error":
                    sys.exit(1)

        elif mode == "setup-sandbox":
            output, status = setup_sandbox()
            print("---- Kết quả ----")
            print(output)
            if status == "ok":
                print("[PocketOps] Sandbox Alpine đã sẵn sàng — từ giờ 'sandbox' sẽ cô lập thật.")
            else:
                print(f"[PocketOps] Cài đặt thất bại (trạng thái: {status}). Xem lỗi ở trên.")

        elif mode == "sandbox-run":
            # [MỚI] fix theo yêu cầu Kaito: rút gọn việc test code mới,
            # không cần gõ heredoc dài — chỉ cần: sandbox-run <đường dẫn file>
            if len(argv) < 2:
                print("Thiếu đường dẫn file. Dùng: python pocketops_v0.py sandbox-run <file.py>")
                sys.exit(1)
            local_path = argv[1]
            if not os.path.isfile(local_path):
                print(f"Không tìm thấy file: {local_path}")
                sys.exit(1)
            filename = os.path.basename(local_path)
            dest_path = os.path.join(SANDBOX_ROOT, filename)
            if os.path.exists(dest_path):  # [MỚI] fix #14: báo rõ khi ghi đè
                print(f"[PocketOps] Lưu ý: đang GHI ĐÈ file cũ cùng tên trong sandbox ({filename}).")
            shutil.copy(local_path, dest_path)
            print(f"[PocketOps] Đã copy {filename} vào sandbox, đang chạy...")
            command = f"python3 /sandbox/{filename}"
            output, status = run_in_sandbox(command)
            print("---- Kết quả ----")
            print(output)
            _log_session(f"sandbox-run: {filename}", output, status)
            print(f"[PocketOps] Đã lưu log (trạng thái: {status})")
            if status == "error":
                sys.exit(1)

        elif mode == "sandbox":
            if len(argv) < 2:
                print('Thiếu lệnh. Dùng: python pocketops_v0.py sandbox "<lệnh>"')
                sys.exit(1)
            command = " ".join(argv[1:])
            print(f"[PocketOps] Đang chạy trong sandbox: {command}")
            output, status = run_in_sandbox(command)
            print("---- Kết quả ----")
            print(output)
            _log_session(f"sandbox: {command}", output, status)
            print(f"[PocketOps] Đã lưu log (trạng thái: {status})")
            if status == "error":
                sys.exit(1)

        elif mode == "phone":
            if len(argv) < 2:
                _print_usage()
                sys.exit(1)
            action = argv[1]
            action_args = argv[2:]
            nhan_rui_ro = "[RỦI RO CAO] " if action in HIGH_RISK_ACTIONS else ""  # [MỚI] fix #17
            print(f"[PocketOps] {nhan_rui_ro}Đang thực hiện thao tác điện thoại: {action}")
            output, status = run_phone_action(action, action_args)
            print("---- Kết quả ----")
            print(output if output else "(không có output, coi như thành công)")
            # fix #13 + #14: che nội dung 'type' trong log, thêm tiền tố rõ nhóm lệnh
            if action == "type":
                log_cmd = f"phone type: (đã gõ {len(' '.join(action_args))} ký tự, nội dung không lưu)"
            elif action in ADB_ACTIONS:
                log_cmd = f"phone(adb) {action}: {' '.join(action_args)}"
            else:
                log_cmd = f"phone(termux-api) {action}: {' '.join(action_args)}"
            _log_session(log_cmd, output, status)
            print(f"[PocketOps] Đã lưu log (trạng thái: {status})")
            if status == "error":  # [MỚI] fix: trả đúng exit code để && / script khác hoạt động đúng
                sys.exit(1)

        else:
            print(f"Chế độ '{mode}' không hợp lệ.")
            _print_usage()
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n[PocketOps] Đã huỷ theo yêu cầu.")
        sys.exit(130)
    except Exception as e:  # fix #3: không bao giờ dump traceback thô lên màn hình Kaito
        print(f"[PocketOps] Lỗi không mong muốn: {e}")
        print("Nếu lỗi này lặp lại, gửi lại dòng lệnh đã gõ để kiểm tra kỹ hơn.")
        sys.exit(1)


if __name__ == "__main__":
    main()
