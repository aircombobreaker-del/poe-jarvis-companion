"""
One-command packaging script for PoE Jarvis Companion (Windows .exe & macOS .app).
Usage:
    python build_app.py
"""
import os
import platform
import plistlib
import shutil
import subprocess
import sys

# Ensure UTF-8 console output on Windows CI runners (prevents cp1252 UnicodeEncodeError)
for _stream in (sys.stdout, sys.stderr):
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

APP_BUNDLE_NAME = "PoE_Jarvis_Companion"


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[Build] Installing pyinstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def patch_macos_info_plist(app_path: str):
    """
    Adds NSMicrophoneUsageDescription and NSScreenCaptureUsageDescription to macOS Info.plist
    so macOS grants microphone and screen recording permissions to the standalone .app bundle.
    """
    plist_path = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.exists(plist_path):
        return
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        data["NSMicrophoneUsageDescription"] = (
            "PoE Jarvis Companion ต้องการใช้ไมโครโฟนเพื่อรับคำสั่งเสียงภาษาไทยขณะเล่นเกม"
        )
        data["NSScreenCaptureUsageDescription"] = (
            "PoE Jarvis Companion ต้องการแคปภาพหน้าจอเมื่อคุณสั่งให้ช่วยวิเคราะห์ไอเทมหรือบอส"
        )
        data["CFBundleDisplayName"] = "PoE Jarvis Companion"
        with open(plist_path, "wb") as f:
            plistlib.dump(data, f)
        print(f"[Build] Patched macOS microphone & screen permissions in {plist_path}")
    except Exception as e:
        print(f"[Build Warning] Could not patch Info.plist: {e}")


def build():
    ensure_pyinstaller()

    for folder in ["build", "dist"]:
        if os.path.exists(folder):
            shutil.rmtree(folder)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_BUNDLE_NAME,
        "--collect-data",
        "soundfile",
        "--collect-binaries",
        "soundfile",
        "--collect-data",
        "sounddevice",
        "--collect-binaries",
        "sounddevice",
        "--hidden-import",
        "edge_tts",
        "--hidden-import",
        "speech_recognition",
        "--hidden-import",
        "mss",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "google.genai",
        "main.py",
    ]

    print("[Build] Running PyInstaller:", " ".join(cmd))
    subprocess.check_call(cmd)

    system = platform.system()
    if system == "Darwin":
        app_bundle = os.path.join("dist", f"{APP_BUNDLE_NAME}.app")
        if os.path.exists(app_bundle):
            patch_macos_info_plist(app_bundle)
            print("[Build] Re-signing macOS bundle with ad-hoc signature...")
            subprocess.check_call(["codesign", "--force", "--deep", "--sign", "-", app_bundle])

            zip_path = os.path.join("dist", f"{APP_BUNDLE_NAME}_macOS.zip")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            print("[Build] Packaging with ditto to preserve macOS symlinks & code signature...")
            subprocess.check_call(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app_bundle, zip_path])
            print(f"\n[SUCCESS] Built and signed macOS app bundle: {app_bundle}")
            print(f"[SUCCESS] Created macOS zip archive via ditto: {zip_path}")
    elif system == "Windows":
        exe_folder = os.path.join("dist", APP_BUNDLE_NAME)
        if os.path.exists(exe_folder):
            zip_base = os.path.join("dist", f"{APP_BUNDLE_NAME}_Windows")
            shutil.make_archive(zip_base, "zip", "dist", APP_BUNDLE_NAME)
            print(f"\n[SUCCESS] Built Windows executable: dist/{APP_BUNDLE_NAME}/{APP_BUNDLE_NAME}.exe")
            print(f"[SUCCESS] Created Windows zip archive: {zip_base}.zip")


if __name__ == "__main__":
    build()
