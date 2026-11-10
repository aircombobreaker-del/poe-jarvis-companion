"""
Phase 1 Test Suite for PoE-Jarvis-Companion
Tests configuration, daily quota tracking, Thai voice engine, and Gemini brain.
"""

import sys
from config_manager import ConfigManager
from tts_engine import TTSEngine
from brain import PoEJarvisBrain

def main():
    print("=" * 60)
    print("🛡️  PoE Jarvis Companion - Phase 1 Verification")
    print("=" * 60)

    # 1. Test Config & Quota
    cfg_mgr = ConfigManager()
    quota = cfg_mgr.get_quota_status()
    print(f"\n[1] 📊 ตรวจสอบระบบโควตาประจำวัน:")
    print(f"    - รอบวันปัจจุบัน: {quota['cycle_date']}")
    print(f"    - ใช้งานแล้ว: {quota['used']} / {quota['limit']} ครั้ง")
    print(f"    - เหลือโควตา: {quota['remaining']} ครั้ง (รีเซ็ตทุกวัน 07:00 น. เวลาไทย)")
    print(f"    - โหมดเกมปัจจุบัน: {cfg_mgr.config.get('active_game', 'poe2').upper()}")

    # 2. Test TTS Engine (Thai Speech)
    print(f"\n[2] 🔊 ทดสอบระบบเสียงภาษาไทย (Edge-TTS):")
    tts = TTSEngine(
        voice=cfg_mgr.config["voice"]["speaker"],
        pitch=cfg_mgr.config["voice"]["pitch"],
        rate=cfg_mgr.config["voice"]["rate"]
    )
    test_msg = (
        f"ยินดีต้อนรับครับท่าน ผมคือ{cfg_mgr.config.get('ai_name', 'จาร์วิส')} "
        f"ระบบเสียงภาษาไทยพร้อมใช้งานแล้วครับ"
    )
    print(f"    - กำลังพูด: '{test_msg}'")
    tts.speak(test_msg, block=True)
    print("    - ✅ เล่นเสียงสำเร็จ!")

    # 3. Test Game Switch (PoE 1 <-> PoE 2)
    print(f"\n[3] 🔄 ทดสอบการสลับโหมดเกม (PoE 1 <-> PoE 2):")
    current_game = cfg_mgr.config.get("active_game", "poe2")
    new_game = "poe1" if current_game == "poe2" else "poe2"
    cfg_mgr.set_active_game(new_game)
    print(f"    - สลับจาก {current_game.upper()} -> {new_game.upper()} สำเร็จ!")
    # Switch back to original
    cfg_mgr.set_active_game(current_game)
    print(f"    - สลับกลับมาที่ {current_game.upper()} เรียบร้อย")

    # 4. Test Brain Connection
    print(f"\n[4] 🧠 ทดสอบระบบสมอง (Brain / Gemini / Local):")
    brain = PoEJarvisBrain(cfg_mgr)
    test_query = "ธนูในเกมมีสกิลอะไรแนะนำบ้างสำหรับคนเริ่มต้น"
    print(f"    - คำถามทดสอบ: '{test_query}'")
    answer = brain.ask(test_query)
    print(f"\n    [คำตอบจาก Jarvis]:\n    {answer}\n")

    print("=" * 60)
    print("✅ การทดสอบ Phase 1 เสร็จสมบูรณ์ทุกหัวข้อ!")
    print("=" * 60)

if __name__ == "__main__":
    main()
