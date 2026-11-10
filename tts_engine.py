import asyncio
import os
import platform
import subprocess
import tempfile
import time
import edge_tts

VOICES = {
    "niwat": {
        "id": "th-TH-NiwatNeural",
        "name": "นิวัฒน์ (เสียงชาย สุขุม สุภาพ เหมาะกับ Jarvis)",
        "gender": "Male"
    },
    "premwadee": {
        "id": "th-TH-PremwadeeNeural",
        "name": "เปรมวดี (เสียงหญิง นุ่มนวล ชัดเจน เป็นมิตร)",
        "gender": "Female"
    }
}


class TTSEngine:
    def __init__(self, voice="th-TH-NiwatNeural", pitch="+0Hz", rate="+0%"):
        self.voice = voice
        self.pitch = pitch
        self.rate = rate
        self.is_speaking = False
        self.last_spoken_end_time = 0.0
        self.last_spoken_text = ""
        self._current_proc = None

    def set_voice(self, voice, pitch="+0Hz", rate="+0%"):
        self.voice = voice
        self.pitch = pitch
        self.rate = rate

    def stop(self):
        """Immediately stops any currently playing speech."""
        if self._current_proc and self._current_proc.poll() is None:
            try:
                self._current_proc.terminate()
            except Exception:
                pass
        self.is_speaking = False
        self.last_spoken_end_time = time.time()

    async def _generate_mp3(self, text: str, output_path: str):
        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            pitch=self.pitch,
            rate=self.rate
        )
        await communicate.save(output_path)

    def speak(self, text: str, block=True):
        """
        Synthesizes Thai speech and plays it through default audio device.
        Sets self.is_speaking = True while audio is playing so the microphone
        can automatically mute speaker echo.
        """
        if not text or not text.strip():
            return

        self.last_spoken_text = text.strip()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fp:
            temp_path = fp.name

        try:
            self.is_speaking = True
            asyncio.run(self._generate_mp3(text, temp_path))

            current_os = platform.system()
            if current_os == "Darwin":
                cmd = ["afplay", temp_path]
            elif current_os == "Windows":
                cmd = ["powershell", "-c", f'(New-Object Media.SoundPlayer "{temp_path}").PlaySync()']
            else:
                cmd = ["aplay", temp_path]

            if block:
                self._current_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._current_proc.wait()
            else:
                self._current_proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        except Exception as e:
            print(f"[TTSEngine Error] {e}")
        finally:
            self.is_speaking = False
            self.last_spoken_end_time = time.time()
            if block and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def preview_voice(self, speaker=None, pitch=None, rate=None, ai_name="จาร์วิส", user_title="ท่าน"):
        """
        Plays a quick test voice sample using the custom AI name and user title.
        """
        old_voice = self.voice
        old_pitch = self.pitch
        old_rate = self.rate

        if speaker:
            self.voice = speaker
        if pitch is not None:
            self.pitch = pitch
        if rate is not None:
            self.rate = rate

        ai_name = (ai_name or "จาร์วิส").strip()
        user_title = (user_title or "ท่าน").strip()
        is_female = "Premwadee" in self.voice
        polite = "ค่ะ" if is_female else "ครับ"
        pronoun = "ฉัน" if is_female else "ผม"

        sample_text = (
            f"สวัสดี{polite}{user_title} {pronoun}คือ{ai_name} "
            f"นี่คือตัวอย่างเสียงของ{pronoun} ในระดับความทุ้มแหลมและความเร็วที่{user_title}เลือก{polite}"
        )
        self.speak(sample_text, block=True)

        self.voice = old_voice
        self.pitch = old_pitch
        self.rate = old_rate


if __name__ == "__main__":
    print("[Testing TTS Engine] Speaking in Thai...")
    engine = TTSEngine()
    engine.speak("สวัสดีครับท่าน ผมคือจาร์วิส ผู้ช่วยส่วนตัวของคุณในเกม Path of Exile พร้อมรับคำสั่งแล้วครับ")
    print("[Done]")
