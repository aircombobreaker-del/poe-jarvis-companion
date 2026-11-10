"""
PoE Jarvis Companion - Main Launcher
Launches the native PyQt6 Desktop App (uses modern Metal/Direct3D hardware rendering, low RAM).
"""

import sys

# Ensure UTF-8 console output on Windows (prevents cp1252 UnicodeEncodeError on Thai/Emoji print)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    if "--cli" in sys.argv:
        from companion import PoEJarvisCompanion

        print("🛡️ PoE Jarvis Companion - CLI Mode")
        app = PoEJarvisCompanion()
        while True:
            try:
                user_input = input("\n[พิมพ์คำถาม หรือกด Enter เพื่อพูดผ่านไมค์ (Ctrl+C เพื่อออก)]: ")
                if user_input.strip():
                    app.handle_text_query(user_input)
                else:
                    app.listen_and_process_once()
            except KeyboardInterrupt:
                print("\nลาก่อนครับท่าน!")
                break
    else:
        from ui_app import PoEJarvisApp

        PoEJarvisApp()


if __name__ == "__main__":
    main()
