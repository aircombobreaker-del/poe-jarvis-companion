"""
PoE Jarvis Companion - Main Engine (Phase 2)
Integrates Voice Listener, Screen Capture, Gemini Brain, and Edge-TTS.
"""

from config_manager import ConfigManager
from tts_engine import TTSEngine
from voice_listener import VoiceListener
from screen_capture import ScreenCaptureEngine
from brain import PoEJarvisBrain

class PoEJarvisCompanion:
    def __init__(self):
        self.config_mgr = ConfigManager()
        self.tts = TTSEngine(
            voice=self.config_mgr.config["voice"]["speaker"],
            pitch=self.config_mgr.config["voice"]["pitch"],
            rate=self.config_mgr.config["voice"]["rate"]
        )
        self.listener = VoiceListener(self.config_mgr)
        self.screen_engine = ScreenCaptureEngine()
        self.brain = PoEJarvisBrain(self.config_mgr)

    def handle_text_query(self, user_text: str, capture_screen: bool = False) -> str:
        """
        Processes a query directly (used for testing or keyboard input).
        """
        screen_bytes = None
        if capture_screen or self.listener.check_vision_intent(user_text):
            print("[Companion] 📸 กำลังจับภาพหน้าจอเกมเพื่อวิเคราะห์...")
            screen_bytes = self.screen_engine.capture_screen_bytes()

        cleaned_text = self.listener.clean_wake_word(user_text)
        print(f"[Companion] 🧠 ส่งคำถามไปยัง Jarvis ({self.config_mgr.config.get('active_game', 'poe2').upper()}): '{cleaned_text}'")
        
        # Immediate Filler Cue
        if screen_bytes:
            self.tts.speak("รับทราบครับ กำลังวิเคราะห์ภาพหน้าจอให้ครับ...", block=False)

        response = self.brain.ask(cleaned_text, image_bytes=screen_bytes)
        print(f"[Companion] 🔊 คำตอบจาก Jarvis:\n{response}\n")
        self.tts.speak(response, block=True)
        return response

    def listen_and_process_once(self) -> str:
        """
        Listens to the microphone for one turn, detects wake-word/intent,
        and speaks the response back.
        """
        print("\n[Companion] 🎙️ ไมโครโฟนเปิดแล้ว กรุณาพูด (เช่น: 'สวัสดีจาร์วิส ธนูนี้สเตตัสดียังไง')...")
        pcm = self.listener.record_until_silence()
        if not pcm:
            print("[Companion] ไม่ได้ยินเสียงพูด")
            return ""

        text = self.listener.transcribe_audio_bytes(pcm)
        if not text:
            print("[Companion] ไม่สามารถแปลงเสียงเป็นข้อความได้")
            return ""

        print(f"[Companion] 🗣️ ได้ยินเสียง: '{text}'")
        self.listener.play_chime()
        return self.handle_text_query(text)

if __name__ == "__main__":
    app = PoEJarvisCompanion()
    # Test handling a text command with screen capture
    app.handle_text_query("สวัสดีจาร์วิส ช่วยดูหน้าจอวิเคราะห์บอสให้หน่อยครับ")
