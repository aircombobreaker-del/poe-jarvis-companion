import io
import time
import platform
import subprocess
import numpy as np
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr
from config_manager import ConfigManager

VISION_KEYWORDS = [
    "ดูหน้าจอ", "แคปหน้าจอ", "แคปภาพ", "แคปรูป", "ดูภาพ",
    "บอสตัวนี้", "ไอเทมนี้", "ธนูนี้", "อาวุธนี้", "เสื้อนี้",
    "ชุดนี้", "ดูนี่", "ดูรูปนี้", "จอนี้", "บอสนี้"
]

WAKE_WORDS = [
    "จาร์วิส", "จาวิส", "จาวิด", "จาร์วิด", "จารวิส",
    "จาวีส", "จาวิต", "ดาร์วิส", "ชาวิส",
    "สวัสดีจาร์วิส", "สวัสดีจาวิส", "สวัสดีจาวิด",
    "เฮ้จาร์วิส", "เฮ้ จาร์วิส", "jarvis"
]


class NativeAudioData(sr.AudioData):
    """
    Subclasses speech_recognition.AudioData to encode FLAC natively in memory
    using soundfile (libsndfile ARM64/x64) instead of speech_recognition's
    broken 32-bit/Intel 'flac-mac' binary.
    """
    def get_flac_data(self, convert_rate=None, convert_width=None):
        raw_bytes = self.get_raw_data(convert_rate=convert_rate, convert_width=convert_width)
        rate = convert_rate or self.sample_rate
        pcm_array = np.frombuffer(raw_bytes, dtype=np.int16)
        buf = io.BytesIO()
        sf.write(buf, pcm_array, rate, format="FLAC", subtype="PCM_16")
        return buf.getvalue()


class VoiceListener:
    def __init__(self, config_manager: ConfigManager = None, sample_rate: int = 16000):
        self.config_mgr = config_manager or ConfigManager()
        self.sample_rate = sample_rate
        self.recognizer = sr.Recognizer()
        self.recognizer.operation_timeout = 8  # Prevent infinite network hang on Google STT

        mic_cfg = self.config_mgr.config.get("mic", {})
        self.silence_timeout = mic_cfg.get("silence_timeout_sec", 1.8)
        self.energy_threshold = float(mic_cfg.get("energy_threshold", 250))
        self.wake_word = mic_cfg.get("wake_word", "จาร์วิส").lower()

    def play_chime(self):
        """Plays an audio cue when Jarvis detects a command and starts processing."""
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(
                    ["afplay", "/System/Library/Sounds/Tink.aiff"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif platform.system() == "Windows":
                import winsound
                winsound.Beep(1200, 150)
        except Exception:
            pass

    def _get_dynamic_wake_words(self) -> list[str]:
        """
        Builds the complete list of active wake words:
        1) Dynamic variations of the custom AI Name (e.g. '<ai_name>', 'สวัสดี<ai_name>', 'เฮ้<ai_name>')
        2) Any custom wake_word in config
        3) Default 'จาร์วิส' phonetic variations as fallback in case the user calls the old name by habit
        """
        ai_name = self.config_mgr.config.get("ai_name", "จาร์วิส").strip().lower()
        custom_wake = self.config_mgr.config.get("mic", {}).get("wake_word", "จาร์วิส").strip().lower()

        dynamic = []
        if ai_name:
            dynamic.extend([
                ai_name,
                f"สวัสดี{ai_name}",
                f"สวัสดี {ai_name}",
                f"เฮ้{ai_name}",
                f"เฮ้ {ai_name}",
            ])
        if custom_wake:
            dynamic.append(custom_wake)

        return list(dict.fromkeys(dynamic + WAKE_WORDS))

    def has_wake_word(self, text: str) -> bool:
        """Checks if the custom AI name, configured wake word, or Jarvis fallback is present."""
        lower = text.lower()
        for w in self._get_dynamic_wake_words():
            if w and w in lower:
                return True
        return False

    def check_vision_intent(self, text: str) -> bool:
        """Checks if the user's spoken command wants Jarvis to inspect the screen."""
        lower = text.lower()
        return any(k in lower for k in VISION_KEYWORDS)

    def clean_wake_word(self, text: str) -> str:
        """Removes the wake word / AI name prefix from the spoken query."""
        cleaned = text
        all_triggers = sorted(self._get_dynamic_wake_words(), key=len, reverse=True)
        for trigger in all_triggers:
            if trigger:
                cleaned = cleaned.replace(trigger, "").replace(trigger.lower(), "")
        return cleaned.strip()

    def run_continuous_stream(
        self,
        on_utterance_pcm,
        level_callback=None,
        should_stop=None,
        is_muted_callback=None,
        max_duration: int = 30,
    ):
        """
        Keeps a SINGLE persistent sd.InputStream open for the entire listening session
        so macOS CoreAudio / PortAudio never deadlocks from repeated stream open/close cycles,
        and never blocks the audio thread on network calls.
        """
        chunk_duration = 0.1  # 100ms chunks
        chunk_samples = int(self.sample_rate * chunk_duration)

        while not (should_stop and should_stop()):
            recorded_chunks = []
            is_speaking = False
            speech_start_time = None
            silence_start_time = None

            try:
                with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16") as stream:
                    while not (should_stop and should_stop()):
                        chunk, _ = stream.read(chunk_samples)

                        # If Jarvis is currently speaking (Speaker Anti-Echo Lock), discard mic input
                        if is_muted_callback and is_muted_callback():
                            recorded_chunks.clear()
                            is_speaking = False
                            speech_start_time = None
                            silence_start_time = None
                            if level_callback:
                                try:
                                    level_callback(-1, False)  # -1 indicates anti-echo lock active
                                except Exception:
                                    pass
                            continue

                        audio_data = chunk.flatten()
                        rms = float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))
                        threshold = self.energy_threshold
                        # Discord-style hysteresis: once speaking starts, allow softer trailing syllables (55%)
                        active_threshold = threshold * 0.55 if is_speaking else threshold
                        above = rms > active_threshold

                        if level_callback:
                            try:
                                level_callback(int(min(6000, rms)), above)
                            except Exception:
                                pass

                        if above:
                            if not is_speaking:
                                is_speaking = True
                                speech_start_time = time.time()
                            silence_start_time = None
                            recorded_chunks.append(audio_data)
                        else:
                            if is_speaking:
                                recorded_chunks.append(audio_data)
                                if silence_start_time is None:
                                    silence_start_time = time.time()
                                elif time.time() - silence_start_time >= self.silence_timeout:
                                    # Utterance finished by silence timeout!
                                    full_audio = np.concatenate(recorded_chunks, axis=0).tobytes()
                                    recorded_chunks.clear()
                                    is_speaking = False
                                    speech_start_time = None
                                    silence_start_time = None
                                    on_utterance_pcm(full_audio)
                            else:
                                # Keep a rolling 400ms pre-buffer so the first syllable is never cut off
                                recorded_chunks.append(audio_data)
                                if len(recorded_chunks) > 4:
                                    recorded_chunks.pop(0)

                        # Enforce max_duration ONLY after speech has started
                        if is_speaking and speech_start_time and (time.time() - speech_start_time >= max_duration):
                            full_audio = np.concatenate(recorded_chunks, axis=0).tobytes()
                            recorded_chunks.clear()
                            is_speaking = False
                            speech_start_time = None
                            silence_start_time = None
                            on_utterance_pcm(full_audio)
            except Exception as e:
                print(f"[Mic Stream Warning] Reconnecting audio input stream: {e}")
                time.sleep(0.5)

    def record_until_silence(
        self,
        max_duration: int = 30,
        level_callback=None,
        should_stop=None,
        is_muted_callback=None,
    ) -> bytes:
        """
        Listens to the microphone using sounddevice.
        - Uses Discord-style hysteresis: requires rms > threshold to START speaking,
          and rms > threshold * 0.55 to CONTINUE speaking so mid-sentence syllables
          never get cut off when threshold is set high.
        - Counts max_duration (30s) ONLY from when speech actually starts, preventing
          premature 5-6s cutoffs!
        """
        chunk_duration = 0.1  # 100ms chunks
        chunk_samples = int(self.sample_rate * chunk_duration)
        recorded_chunks = []

        is_speaking = False
        speech_start_time = None
        silence_start_time = None
        idle_start_time = time.time()

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="int16") as stream:
            while True:
                if should_stop and should_stop():
                    return b""

                chunk, _ = stream.read(chunk_samples)

                # If Jarvis is currently speaking (Speaker Anti-Echo Lock), discard mic input
                if is_muted_callback and is_muted_callback():
                    recorded_chunks.clear()
                    is_speaking = False
                    speech_start_time = None
                    silence_start_time = None
                    idle_start_time = time.time()
                    if level_callback:
                        try:
                            level_callback(0, False)
                        except Exception:
                            pass
                    continue

                audio_data = chunk.flatten()

                # Compute RMS energy to detect voice activity
                rms = float(np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)))
                threshold = self.energy_threshold
                # Discord-style hysteresis: once speaking starts, allow softer trailing syllables (55% of threshold)
                active_threshold = threshold * 0.55 if is_speaking else threshold
                above = rms > active_threshold

                if level_callback:
                    try:
                        level_callback(int(min(6000, rms)), above)
                    except Exception:
                        pass

                if above:
                    if not is_speaking:
                        is_speaking = True
                        speech_start_time = time.time()
                    silence_start_time = None
                    recorded_chunks.append(audio_data)
                else:
                    if is_speaking:
                        recorded_chunks.append(audio_data)
                        if silence_start_time is None:
                            silence_start_time = time.time()
                        elif time.time() - silence_start_time >= self.silence_timeout:
                            break
                    else:
                        # Keep a rolling 400ms pre-buffer so the first syllable is never cut off
                        recorded_chunks.append(audio_data)
                        if len(recorded_chunks) > 4:
                            recorded_chunks.pop(0)
                        # Reset idle loop every 15s of pure silence to keep stream fresh without cutting speech
                        if time.time() - idle_start_time >= 15.0:
                            return b""

                # Only enforce max_duration AFTER the user actually started speaking
                if is_speaking and speech_start_time and (time.time() - speech_start_time >= max_duration):
                    break

        if not is_speaking or len(recorded_chunks) == 0:
            return b""

        full_audio = np.concatenate(recorded_chunks, axis=0)
        return full_audio.tobytes()

    def transcribe_audio_bytes(self, pcm_bytes: bytes) -> str:
        """Transcribes raw PCM audio bytes to Thai text using NativeAudioData."""
        if not pcm_bytes:
            return ""
        try:
            audio_data = NativeAudioData(pcm_bytes, self.sample_rate, 2)
            text = self.recognizer.recognize_google(audio_data, language="th-TH")
            return text.strip()
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            print(f"[Transcription Error] {e}")
            return ""
