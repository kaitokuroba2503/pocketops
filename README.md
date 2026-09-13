# PocketOps 📱⚙️

**An AI agent orchestrator that runs 100% on an Android phone — no PC, no laptop, no computer of any kind.**

This entire project was coded, tested, debugged, and published to GitHub using only Termux on a single Android phone, built by a self-taught Vietnamese developer.

*(Bản tiếng Việt ở dưới / Vietnamese version below)*

## What is PocketOps?

PocketOps is a Python CLI that lets an AI agent operate safely on your phone — it can **execute code in a real isolated sandbox**, and it can **control the phone directly** (notifications, vibration, screen taps, screenshots, opening apps...).

Unlike most popular AI agents today, which ship with weak security defaults (sandboxing off by default), PocketOps is designed **safe by default**: every piece of AI-generated code runs inside an isolated Alpine Linux instance (via `proot-distro`), fully separated from the real filesystem — this was verified by deliberately trying to break out of the sandbox and patching the hole before publishing.

## Key features

- 🔒 **Real sandbox isolation** — run Python/shell code inside a contained Alpine Linux instance, no access to your real files
- 📲 **Phone control** — notifications, toast, vibration, clipboard, open URLs, text-to-speech, battery status — no root required
- 👆 **Screen control via ADB** — tap, swipe, key events, launch apps by package name, screenshots (via Wireless debugging, no root)
- 🚀 **`sandbox-run`** — copy and run a code file inside the sandbox in a single command, no need to type long heredocs
- 📜 **Full history** — every command ever run is logged and viewable via `history`
- ⚡ **No lag** — every operation has a timeout, nothing ever hangs indefinitely

## Installation

Requires [Termux](https://f-droid.org/packages/com.termux/) (install from F-Droid) on Android.

```bash
pkg update -y && pkg upgrade -y
pkg install python proot-distro termux-api android-tools git -y
```

Install the **Termux:API** app from F-Droid (needed for notify/toast/vibrate).

Download `pocketops_v0.py` to your phone, then:

```bash
python pocketops_v0.py setup-sandbox    # run ONCE to bootstrap the Alpine sandbox
```

## Quick usage

```bash
# Run code inside the isolated sandbox
python pocketops_v0.py sandbox "python3 --version"

# Run a code file without typing heredocs
python pocketops_v0.py sandbox-run my_script.py

# Send a phone notification
python pocketops_v0.py phone notify "PocketOps" "Task completed"

# Take a screenshot (requires Wireless debugging setup, see code comments)
python pocketops_v0.py phone screenshot

# View command history
python pocketops_v0.py history
```

See the full command list with:
```bash
python pocketops_v0.py
```

## Why this project exists

Most AI agent tools today assume you have a computer. In many parts of the world — including many self-taught young developers in Vietnam — a phone is the only device people have. PocketOps proves that building a serious, safe, and practical AI agent tool is entirely possible with nothing but a phone.

## Contributing

This project is in early development (v0.4.0). Issues and Pull Requests are welcome.

## License

MIT

---

# 🇻🇳 Bản tiếng Việt

**AI agent orchestrator chạy 100% trên điện thoại Android — không cần PC, laptop, hay bất kỳ máy tính nào.**

Toàn bộ dự án này được lập trình, test, debug và đăng lên GitHub hoàn toàn qua Termux trên 1 chiếc điện thoại Android duy nhất, do 1 người Việt Nam tự học lập trình thực hiện.

## PocketOps là gì?

PocketOps là một CLI Python cho AI agent chạy an toàn trên điện thoại của bạn — vừa có thể **thực thi code trong sandbox cô lập thật sự**, vừa có thể **điều khiển trực tiếp điện thoại** (thông báo, rung, chạm màn hình, chụp ảnh, mở app...).

Khác với các AI agent phổ biến hiện nay vốn mặc định bảo mật lỏng lẻo (sandbox tắt mặc định), PocketOps được thiết kế theo hướng **an toàn là mặc định**: mọi đoạn code do AI sinh ra đều chạy trong 1 bản Linux con (Alpine, qua `proot-distro`) hoàn toàn tách biệt khỏi hệ thống thật — đã tự kiểm chứng bằng cách chủ động tìm cách "vượt ngục" và vá lỗ hổng trước khi công bố.

## Tính năng chính

- 🔒 **Sandbox cô lập thật** — chạy code Python/shell trong Alpine Linux con, không đụng được vào file thật của bạn
- 📲 **Điều khiển điện thoại** — thông báo, toast, rung, clipboard, mở URL, đọc chữ (TTS), xem pin — không cần root
- 👆 **Điều khiển màn hình qua ADB** — chạm, vuốt, gõ phím, mở app theo tên, chụp màn hình (qua Wireless debugging, không cần root)
- 🚀 **`sandbox-run`** — copy và chạy 1 file code trong sandbox chỉ bằng 1 lệnh, không cần gõ heredoc dài
- 📜 **Lịch sử đầy đủ** — mọi lệnh đã chạy được lưu lại, xem lại bất cứ lúc nào bằng `history`
- ⚡ **Không lag** — mọi thao tác đều có timeout, không bao giờ treo vô thời hạn

## Cài đặt

Yêu cầu: [Termux](https://f-droid.org/packages/com.termux/) (cài từ F-Droid) trên Android.

```bash
pkg update -y && pkg upgrade -y
pkg install python proot-distro termux-api android-tools git -y
```

Cài app **Termux:API** từ F-Droid (để dùng notify/toast/vibrate).

Tải `pocketops_v0.py` về máy, rồi:

```bash
python pocketops_v0.py setup-sandbox    # chạy 1 lần duy nhất, dựng sandbox Alpine
```

## Sử dụng nhanh

```bash
# Chạy code trong sandbox cô lập
python pocketops_v0.py sandbox "python3 --version"

# Chạy 1 file code, không cần gõ heredoc
python pocketops_v0.py sandbox-run my_script.py

# Gửi thông báo lên điện thoại
python pocketops_v0.py phone notify "PocketOps" "Đã xong tác vụ"

# Chụp màn hình (cần bật Wireless debugging trước, xem hướng dẫn trong code)
python pocketops_v0.py phone screenshot

# Xem lại lịch sử đã chạy
python pocketops_v0.py history
```

Xem đầy đủ danh sách lệnh bằng:
```bash
python pocketops_v0.py
```

## Vì sao dự án này ra đời?

Phần lớn công cụ AI agent hiện nay giả định người dùng có máy tính. Ở nhiều nơi trên thế giới — bao gồm rất nhiều lập trình viên trẻ Việt Nam đang tự học — điện thoại là thiết bị duy nhất họ có. PocketOps chứng minh rằng việc xây dựng công cụ AI agent nghiêm túc, an toàn, và thực dụng hoàn toàn khả thi chỉ với 1 chiếc điện thoại.

## Đóng góp

Dự án đang trong giai đoạn phát triển ban đầu (v0.4.0). Issues và Pull Requests luôn được chào đón.

## License

MIT
