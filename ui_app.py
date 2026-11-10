import os
import sys
import time
import queue
import webbrowser

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QCheckBox,
    QFileDialog,
    QTabWidget,
    QGroupBox,
    QProgressBar,
    QTextEdit,
    QLineEdit,
    QComboBox,
    QSlider,
    QScrollArea,
    QMessageBox,
    QDialog,
    QFrame,
)

from config_manager import ConfigManager, get_app_data_dir
from tts_engine import TTSEngine
from screen_capture import ScreenCaptureEngine
from brain import PoEJarvisBrain
from voice_listener import VoiceListener
from version import APP_VERSION
from updater import check_for_updates

PERSONA_OPTIONS = [
    ("short", "⚡ ตอบสั้นกระชับ (เน้นเล่นไว ไม่เวิ่นเว้อ 1-2 ประโยค)"),
    ("normal", "🤖 ตอบปกติ (สไตล์ Jarvis สุขุมสมดุล)"),
    ("funny", "🎉 เพื่อนเล่นสายเฮฮา (กวนๆ ตบมุก เหมาะกับไลฟ์สด)"),
    ("serious", "📊 จริงจังวิเคราะห์ลึก (สาย Theorycraft จัดเต็ม)"),
]


class VoicePreviewWorker(QThread):
    def __init__(self, tts: TTSEngine, speaker: str, pitch: str, rate: str, ai_name: str, user_title: str):
        super().__init__()
        self.tts = tts
        self.speaker = speaker
        self.pitch = pitch
        self.rate = rate
        self.ai_name = ai_name
        self.user_title = user_title

    def run(self):
        self.tts.preview_voice(
            speaker=self.speaker,
            pitch=self.pitch,
            rate=self.rate,
            ai_name=self.ai_name,
            user_title=self.user_title,
        )


class ScreenTestWorker(QThread):
    finished_signal = pyqtSignal(str)

    def __init__(self, screen_engine: ScreenCaptureEngine, brain: PoEJarvisBrain, tts: TTSEngine):
        super().__init__()
        self.screen_engine = screen_engine
        self.brain = brain
        self.tts = tts

    def run(self):
        img_bytes = self.screen_engine.capture_screen_bytes()
        query = "ช่วยดูภาพหน้าจอนี้ให้หน่อย แนะนำข้อมูลหรือบอสในภาพครับ"
        resp = self.brain.ask(query, image_bytes=img_bytes)
        self.finished_signal.emit(resp)
        self.tts.speak(resp, block=True)


class CommandQueueWorker(QThread):
    """
    Processes recorded audio (STT Transcription -> Brain -> TTS) asynchronously
    in the background so the Microphone Audio Stream never blocks on network I/O
    and never closes/reopens the hardware mic stream!
    """
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str, str)
    quota_signal = pyqtSignal()
    sources_signal = pyqtSignal(list)

    def __init__(
        self,
        cmd_queue: queue.Queue,
        config_mgr: ConfigManager,
        listener: VoiceListener,
        screen_engine: ScreenCaptureEngine,
        brain: PoEJarvisBrain,
        tts: TTSEngine,
    ):
        super().__init__()
        self.cmd_queue = cmd_queue
        self.config_mgr = config_mgr
        self.listener = listener
        self.screen_engine = screen_engine
        self.brain = brain
        self.tts = tts
        self.running = True
        self.is_busy = False

    def stop(self):
        self.running = False
        self.cmd_queue.put(None)

    def run(self):
        while self.running:
            try:
                item = self.cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is None or not self.running:
                break

            # Item can be raw PCM audio bytes from ListenLoopWorker
            if isinstance(item, bytes):
                self.is_busy = True
                self.status_signal.emit("⏳ สถานะ: กำลังแปลงเสียงพูดเป็นข้อความ...", "#38bdf8")
                text = self.listener.transcribe_audio_bytes(item)
                if not text or not self.running:
                    self.is_busy = False
                    if self.cmd_queue.empty():
                        self.status_signal.emit("🎙️ สถานะ: กำลังรอฟังเสียง... (พูดได้เลยครับ)", "#f59e0b")
                    continue

                # Extra safety: ignore if transcribed text matches Jarvis's own last spoken sentence
                if self.tts.last_spoken_text and len(text) > 4 and text in self.tts.last_spoken_text:
                    self.is_busy = False
                    if self.cmd_queue.empty():
                        self.status_signal.emit("🎙️ สถานะ: กำลังรอฟังเสียง... (พูดได้เลยครับ)", "#f59e0b")
                    continue

                free_talk = self.config_mgr.config.get("mic", {}).get("free_talk_mode", False)
                has_wake = self.listener.has_wake_word(text)

                if not (free_talk or has_wake):
                    ai_name = self.config_mgr.config.get("ai_name", "จาร์วิส")
                    self.log_signal.emit(
                        f"💬 (ได้ยินเสียงแต่ไม่มีคำเรียกชื่อ '{ai_name}' หรือ 'จาร์วิส': \"{text}\" - หากไม่อยากเรียกชื่อ กดเปิด '⚡ โหมดคุยอิสระ' ได้ครับ)"
                    )
                    self.is_busy = False
                    if self.cmd_queue.empty():
                        self.status_signal.emit("🎙️ สถานะ: กำลังรอฟังเสียง... (พูดได้เลยครับ)", "#f59e0b")
                    continue

                self.listener.play_chime()
                cleaned_query = self.listener.clean_wake_word(text) if has_wake else text.strip()
                needs_vision = self.listener.check_vision_intent(text)
                user_title = self.config_mgr.config.get("user_title", "ท่าน")
                self.log_signal.emit(f"🗣️ {user_title}: \"{text}\"")
            else:
                cleaned_query, needs_vision = item
                self.is_busy = True

            screen_bytes = None
            if needs_vision:
                self.status_signal.emit("📸 สถานะ: กำลังแคปหน้าจอวิเคราะห์...", "#38bdf8")
                self.log_signal.emit("📸 ตรวจพบคีย์เวิร์ดดูจอ กำลังแคปภาพหน้าจอเกม...")
                self.tts.speak("รับทราบครับ กำลังดูหน้าจอให้ครับ...", block=False)
                screen_bytes = self.screen_engine.capture_screen_bytes()
            else:
                self.status_signal.emit("🧠 สถานะ: กำลังค้นหาข้อมูลเว็บ/Maxroll...", "#38bdf8")

            resp = self.brain.ask(cleaned_query or "สวัสดีครับ", image_bytes=screen_bytes)
            ai_name = self.brain.config.get("ai_name", "จาร์วิส")
            self.log_signal.emit(f"🤖 {ai_name}: {resp}")

            if self.brain.last_sources:
                self.sources_signal.emit(self.brain.last_sources)
                src_lines = [f"   • {s['title']} -> {s['url']}" for s in self.brain.last_sources[:4]]
                self.log_signal.emit(
                    "📎 ลิงก์บิลด์/เว็บที่ค้นพบ (กดปุ่ม '🔗 ดูลิงก์เว็บ/บิลด์' ด้านบนเพื่อคลิกเปิดเว็บได้เลย):\n"
                    + "\n".join(src_lines)
                )

            self.quota_signal.emit()

            self.status_signal.emit(f"🔊 สถานะ: {ai_name} กำลังพูดตอบ... (ล็อกไมค์กันเสียงสะท้อนอัตโนมัติ)", "#a855f7")
            self.tts.speak(resp, block=True)

            self.is_busy = False
            if self.cmd_queue.empty() and self.running:
                self.status_signal.emit("🎙️ สถานะ: กำลังรอฟังเสียง... (พูดได้เลยครับ)", "#f59e0b")


class ListenLoopWorker(QThread):
    """
    Pure Audio Thread: keeps a SINGLE persistent sd.InputStream open for the entire
    listening session (zero network calls on this thread!) so the microphone stream
    and Live Volume Meter never freeze or deadlock.
    """
    mic_level_signal = pyqtSignal(int, bool)

    def __init__(
        self,
        listener: VoiceListener,
        cmd_queue: queue.Queue,
        tts: TTSEngine,
    ):
        super().__init__()
        self.listener = listener
        self.cmd_queue = cmd_queue
        self.tts = tts
        self.running = True

    def stop(self):
        self.running = False

    def _is_jarvis_speaking(self) -> bool:
        return self.tts.is_speaking or (time.time() - self.tts.last_spoken_end_time < 0.5)

    def run(self):
        self.listener.run_continuous_stream(
            on_utterance_pcm=lambda pcm_bytes: self.cmd_queue.put(pcm_bytes),
            level_callback=lambda rms, above: self.mic_level_signal.emit(rms, above),
            should_stop=lambda: not self.running,
            is_muted_callback=self._is_jarvis_speaking,
            max_duration=30,
        )


MODERN_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #111827;
    color: #f3f4f6;
}
QWidget {
    color: #f3f4f6;
    font-size: 13px;
}
QTabWidget::pane {
    border: 1px solid #374151;
    border-radius: 8px;
    background-color: #1f2937;
    padding: 8px;
}
QTabBar::tab {
    background-color: #1f2937;
    color: #9ca3af;
    border: 1px solid #374151;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 24px;
    margin-right: 4px;
    font-weight: bold;
    font-size: 14px;
}
QTabBar::tab:selected {
    background-color: #2563eb;
    color: #ffffff;
    border-color: #2563eb;
}
QGroupBox {
    background-color: #1f2937;
    border: 1px solid #374151;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px 12px;
    font-weight: bold;
    font-size: 13px;
    color: #60a5fa;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QLineEdit, QComboBox, QTextEdit {
    background-color: #111827;
    color: #f9fafb;
    border: 1px solid #4b5563;
    border-radius: 6px;
    padding: 4px 10px;
    min-height: 24px;
    font-size: 13px;
}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
    border: 1px solid #3b82f6;
}
QPushButton {
    background-color: #374151;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: bold;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #4b5563;
}
QProgressBar {
    background-color: #111827;
    border: 1px solid #374151;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    font-weight: bold;
    height: 16px;
}
QProgressBar::chunk {
    background-color: #2563eb;
    border-radius: 5px;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
"""


class PoEJarvisWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🛡️ PoE Jarvis Companion - Path of Exile 1 & 2")
        self.resize(880, 810)
        self.setMinimumSize(780, 700)
        self.setStyleSheet(MODERN_STYLESHEET)

        # Initialize Core Engines
        self.config_mgr = ConfigManager()
        self.tts = TTSEngine(
            voice=self.config_mgr.config["voice"]["speaker"],
            pitch=self.config_mgr.config["voice"]["pitch"],
            rate=self.config_mgr.config["voice"]["rate"],
        )
        self.screen_engine = ScreenCaptureEngine()
        self.brain = PoEJarvisBrain(self.config_mgr)
        self.listener = VoiceListener(self.config_mgr)

        self.is_listening = False
        self.cmd_queue = None
        self.cmd_worker = None
        self.listen_worker = None
        self.preview_worker = None
        self.screen_worker = None
        self.last_discovered_sources: list[dict] = []

        self._build_ui()
        self._load_config_to_ui()
        self.update_quota_display()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(8)

        # 1. Top Header Bar
        header_frame = QFrame()
        header_frame.setStyleSheet(
            "QFrame { background-color: #1f2937; border: 1px solid #374151; border-radius: 10px; padding: 6px; }"
        )
        header_layout = QHBoxLayout(header_frame)

        title_lbl = QLabel(f"🛡️ PoE Jarvis Companion (v{APP_VERSION})")
        title_lbl.setStyleSheet("font-size: 19px; font-weight: bold; color: #ffffff; border: none;")
        header_layout.addWidget(title_lbl)

        btn_update = QPushButton("🔄 เช็กอัปเดต")
        btn_update.setStyleSheet(
            "QPushButton { background-color: #374151; color: #38bdf8; padding: 6px 12px; font-size: 12px; } "
            "QPushButton:hover { background-color: #4b5563; }"
        )
        btn_update.clicked.connect(self._check_app_update)
        header_layout.addWidget(btn_update)

        header_layout.addStretch()

        mode_lbl = QLabel("โหมดเกม:")
        mode_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #9ca3af; border: none;")
        header_layout.addWidget(mode_lbl)

        self.btn_poe2 = QPushButton("⚔️ Path of Exile 2")
        self.btn_poe2.setCheckable(True)
        self.btn_poe2.clicked.connect(lambda: self._switch_game("poe2"))
        header_layout.addWidget(self.btn_poe2)

        self.btn_poe1 = QPushButton("🔥 Path of Exile 1")
        self.btn_poe1.setCheckable(True)
        self.btn_poe1.clicked.connect(lambda: self._switch_game("poe1"))
        header_layout.addWidget(self.btn_poe1)

        main_layout.addWidget(header_frame)

        # 2. Main Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.tab_dashboard = QWidget()
        self.tab_settings = QWidget()

        self.tabs.addTab(self.tab_dashboard, "🎮 หน้าควบคุมหลัก (Dashboard)")
        self.tabs.addTab(self.tab_settings, "⚙️ การตั้งค่าระบบ (Settings)")

        self._build_dashboard_tab()
        self._build_settings_tab()

    def _build_dashboard_tab(self):
        layout = QVBoxLayout(self.tab_dashboard)
        layout.setSpacing(8)

        # 1) Status & Quota Group
        status_group = QGroupBox("📊 สถานะระบบและโควตาประจำวัน")
        status_layout = QVBoxLayout(status_group)

        top_status_row = QHBoxLayout()
        self.status_lbl = QLabel("🟢 สถานะ: พร้อมทำงาน (Idle)")
        self.status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #4ade80;")
        top_status_row.addWidget(self.status_lbl)

        top_status_row.addStretch()
        top_status_row.addWidget(QLabel("🎭 นิสัยของ Jarvis:"))
        self.persona_combo = QComboBox()
        for _, label in PERSONA_OPTIONS:
            self.persona_combo.addItem(label)
        self.persona_combo.setMinimumWidth(330)
        self.persona_combo.currentIndexChanged.connect(self._on_persona_changed)
        top_status_row.addWidget(self.persona_combo)
        status_layout.addLayout(top_status_row)

        self.quota_lbl = QLabel("โควตาวันนี้: 0 / 1,500 ครั้ง (เหลือ 1,500 ครั้ง) | รีเซ็ตทุกวัน 07:00 น. (เวลาไทย)")
        self.quota_lbl.setStyleSheet("font-size: 12px; color: #e5e7eb;")
        status_layout.addWidget(self.quota_lbl)

        self.quota_bar = QProgressBar()
        self.quota_bar.setRange(0, 1500)
        self.quota_bar.setValue(0)
        status_layout.addWidget(self.quota_bar)

        # Context Memory Counter & Save/Load/Clear Row
        mem_row = QHBoxLayout()
        self.memory_lbl = QLabel("🧠 ความจำบริบทสะสม: 0 ประโยค (~0 โทเคน) | โหมด: Gemini (แนบไม่จำกัด)")
        self.memory_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #38bdf8;")
        mem_row.addWidget(self.memory_lbl, stretch=1)

        btn_save_mem = QPushButton("💾 เซฟความจำแยกไฟล์")
        btn_save_mem.setStyleSheet("QPushButton { background-color: #0284c7; padding: 5px 10px; font-size: 12px; } QPushButton:hover { background-color: #0369a1; }")
        btn_save_mem.clicked.connect(self._save_memory_file_dialog)
        mem_row.addWidget(btn_save_mem)

        btn_load_mem = QPushButton("📂 โหลดความจำ")
        btn_load_mem.setStyleSheet("QPushButton { background-color: #4f46e5; padding: 5px 10px; font-size: 12px; } QPushButton:hover { background-color: #4338ca; }")
        btn_load_mem.clicked.connect(self._load_memory_file_dialog)
        mem_row.addWidget(btn_load_mem)

        btn_clear_mem_top = QPushButton("🧹 ล้างความจำ")
        btn_clear_mem_top.setStyleSheet("QPushButton { background-color: #4b5563; padding: 5px 10px; font-size: 12px; } QPushButton:hover { background-color: #6b7280; }")
        btn_clear_mem_top.clicked.connect(self._clear_conversation_memory)
        mem_row.addWidget(btn_clear_mem_top)

        status_layout.addLayout(mem_row)

        layout.addWidget(status_group)

        # 2) Discord-Style Noise Gate & Live Mic Sensitivity Group (Expanded to 6000!)
        gate_group = QGroupBox("🎚️ ตัวตัดเสียงรบกวน/เสียงทีวี (Input Sensitivity แบบ Discord - ปรับได้สูงสุด 6,000)")
        gate_layout = QVBoxLayout(gate_group)

        meter_top = QHBoxLayout()
        self.mic_state_lbl = QLabel("ระดับเสียงไมค์สด: รอเปิดไมค์...")
        self.mic_state_lbl.setStyleSheet("font-size: 12px; color: #9ca3af;")
        meter_top.addWidget(self.mic_state_lbl)

        meter_top.addStretch()
        self.threshold_lbl = QLabel("จุดตัดเสียงรบกวน (Threshold): 250 / 6,000 (ลากให้สูงกว่าเสียงทีวี)")
        self.threshold_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #fbbf24;")
        meter_top.addWidget(self.threshold_lbl)
        gate_layout.addLayout(meter_top)

        # Live VU Meter Bar (0 to 6000)
        self.mic_meter = QProgressBar()
        self.mic_meter.setRange(0, 6000)
        self.mic_meter.setValue(0)
        self.mic_meter.setFormat("%v / 6000")
        self.mic_meter.setStyleSheet(
            "QProgressBar { background-color: #111827; border: 1px solid #374151; border-radius: 6px; height: 16px; } "
            "QProgressBar::chunk { background-color: #4b5563; border-radius: 5px; }"
        )
        gate_layout.addWidget(self.mic_meter)

        # Threshold Slider directly aligned with the meter range (50 to 6000)
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(50, 6000)
        self.threshold_slider.setSingleStep(50)
        self.threshold_slider.setPageStep(250)
        self.threshold_slider.setValue(250)
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        gate_layout.addWidget(self.threshold_slider)

        layout.addWidget(gate_group)

        # 3) Action Buttons Row (Start Mic + Free-Talk Mode + Screen Capture Test)
        btn_row = QHBoxLayout()

        self.btn_listen = QPushButton("🎙️ เริ่มต้นเปิดไมค์ (Start Listening)")
        self.btn_listen.setMinimumHeight(42)
        self.btn_listen.setStyleSheet(
            "QPushButton { background-color: #2563eb; font-size: 13px; } QPushButton:hover { background-color: #1d4ed8; }"
        )
        self.btn_listen.clicked.connect(self._toggle_listening)
        btn_row.addWidget(self.btn_listen, stretch=2)

        self.btn_free_talk = QPushButton("⚡ โหมดคุยอิสระ: ปิด (ต้องเรียกชื่อ)")
        self.btn_free_talk.setCheckable(True)
        self.btn_free_talk.setMinimumHeight(42)
        self.btn_free_talk.clicked.connect(self._toggle_free_talk)
        btn_row.addWidget(self.btn_free_talk, stretch=2)

        self.btn_sources = QPushButton("🔗 ดูลิงก์เว็บ/บิลด์ (0)")
        self.btn_sources.setMinimumHeight(42)
        self.btn_sources.setStyleSheet(
            "QPushButton { background-color: #0f766e; font-size: 13px; } QPushButton:hover { background-color: #0d9488; }"
        )
        self.btn_sources.clicked.connect(self._show_sources_dialog)
        btn_row.addWidget(self.btn_sources, stretch=1)

        self.btn_capture = QPushButton("📸 แคปจอวิเคราะห์")
        self.btn_capture.setMinimumHeight(42)
        self.btn_capture.setStyleSheet(
            "QPushButton { background-color: #4b5563; font-size: 13px; } QPushButton:hover { background-color: #6b7280; }"
        )
        self.btn_capture.clicked.connect(self._manual_screen_test)
        btn_row.addWidget(self.btn_capture, stretch=1)

        self.btn_stop_speech = QPushButton("🔇 ตัดบท/หยุดพูด")
        self.btn_stop_speech.setMinimumHeight(42)
        self.btn_stop_speech.setStyleSheet(
            "QPushButton { background-color: #991b1b; font-size: 13px; } QPushButton:hover { background-color: #b91c1c; }"
        )
        self.btn_stop_speech.clicked.connect(self._stop_jarvis_speech)
        btn_row.addWidget(self.btn_stop_speech, stretch=1)

        layout.addLayout(btn_row)

        # 4) Live Activity Log Group
        log_group = QGroupBox("📝 บันทึกการสนทนากับ Jarvis (พูดแทรกขณะ Jarvis พูด เพื่อเข้าคิวคำถามถัดไปได้ทันที)")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            "QTextEdit { background-color: #0b0f19; color: #a7f3d0; font-family: Menlo, monospace; font-size: 13px; }"
        )
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)
        self.log_message(
            "ยินดีต้อนรับสู่ PoE Jarvis Companion!\n"
            "• เลือก '🎭 นิสัยของ Jarvis' ด้านบนได้เลย (สั้นกระชับ / ปกติ / สายเฮฮาไลฟ์สด / จริงจังวิเคราะห์ลึก)\n"
            "• สไลเดอร์ 'จุดตัดเสียงรบกวน' ขยายให้ปรับได้สูงสุดถึง 6,000 เพื่อตัดเสียงทีวีดังๆ ได้สบาย\n"
            "• เมื่อเลือกใช้ Local LLM จะใช้งานได้ฟรีไม่จำกัดและไม่นับรวมในโควตา 1,500 ครั้งครับ!"
        )

    def _build_settings_tab(self):
        outer_layout = QVBoxLayout(self.tab_settings)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer_layout.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        # 0. Personalization Group (AI Name & User Title)
        personal_group = QGroupBox("👤 ตั้งค่าชื่อ AI และคำเรียกตัวคุณ (Personalization & Auto-Sync Wake Word)")
        personal_layout = QVBoxLayout(personal_group)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("🤖 ชื่อของ AI:"))
        self.ai_name_entry = QLineEdit()
        self.ai_name_entry.setPlaceholderText("เช่น จาร์วิส, ฟรายเดย์, อัลตรอน, น้องเมย์")
        self.ai_name_entry.textChanged.connect(self._on_ai_name_changed)
        name_row.addWidget(self.ai_name_entry, stretch=1)

        name_row.addSpacing(12)
        name_row.addWidget(QLabel("👑 ให้ AI เรียกคุณว่า:"))
        self.user_title_entry = QLineEdit()
        self.user_title_entry.setPlaceholderText("เช่น ท่าน, บอส, ลูกพี่, เสี่ย, หรือชื่อเล่นของคุณ")
        name_row.addWidget(self.user_title_entry, stretch=1)
        personal_layout.addLayout(name_row)

        self.sync_hint_lbl = QLabel(
            "💡 ซิงค์อัตโนมัติ: เมื่อเปลี่ยนชื่อ AI คุณสามารถพูดเรียกชื่อใหม่ได้ทันที (หรือเผลอเรียก 'จาร์วิส' ระบบก็ยังขานรับให้ครับ!)"
        )
        self.sync_hint_lbl.setStyleSheet("font-size: 12px; color: #38bdf8;")
        personal_layout.addWidget(self.sync_hint_lbl)

        layout.addWidget(personal_group)

        # 1. Brain Provider Group
        brain_group = QGroupBox("🧠 สมองของ AI (Brain Provider)")
        brain_layout = QVBoxLayout(brain_group)

        self.rb_gemini = QRadioButton("Google Gemini Flash (แนะนำ - ไว ฉลาด มีโควตาฟรี 1,500 ครั้ง/วัน)")
        self.rb_gemini.setChecked(True)
        self.rb_gemini.toggled.connect(self.update_quota_display)
        brain_layout.addWidget(self.rb_gemini)

        gemini_row = QHBoxLayout()
        gemini_row.addWidget(QLabel("API Key:"))
        self.api_key_entry = QLineEdit()
        self.api_key_entry.setPlaceholderText("วางรหัส Gemini API Key ที่นี่...")
        self.api_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        gemini_row.addWidget(self.api_key_entry, stretch=1)

        btn_eye = QPushButton("👁️ ดูรหัส")
        btn_eye.clicked.connect(self._toggle_key_visibility)
        gemini_row.addWidget(btn_eye)

        btn_gemini_guide = QPushButton("📖 คู่มือขอ API Key ฟรี (3 สเต็ป)")
        btn_gemini_guide.setStyleSheet("QPushButton { background-color: #0284c7; } QPushButton:hover { background-color: #0369a1; }")
        btn_gemini_guide.clicked.connect(self._show_gemini_guide)
        gemini_row.addWidget(btn_gemini_guide)
        brain_layout.addLayout(gemini_row)

        gemini_mem_note = QLabel(
            "✨ หากใช้ Google Gemini ไม่ต้องระบุจำนวนความจำ ระบบจะแนบบริบททั้งหมดแบบไม่จำกัดให้อัตโนมัติ เพื่อให้ AI รู้จักคุณและบิลด์ของคุณมากขึ้นเรื่อยๆ ครับ!"
        )
        gemini_mem_note.setStyleSheet("font-size: 12px; color: #38bdf8; margin-bottom: 4px;")
        brain_layout.addWidget(gemini_mem_note)

        self.rb_local = QRadioButton("Local LLM (LM Studio / Ollama - ฟรีไม่จำกัด ไม่ตัดโควตา + ค้นเว็บข้อมูลเกมอัตโนมัติ)")
        self.rb_local.toggled.connect(self.update_quota_display)
        brain_layout.addWidget(self.rb_local)

        local_row = QHBoxLayout()
        local_row.addWidget(QLabel("Server URL:"))
        self.local_url_entry = QLineEdit()
        self.local_url_entry.setPlaceholderText("http://localhost:1234/v1")
        local_row.addWidget(self.local_url_entry, stretch=1)

        local_row.addWidget(QLabel("Model:"))
        self.local_model_entry = QLineEdit()
        self.local_model_entry.setPlaceholderText("qwen2.5-7b-instruct")
        local_row.addWidget(self.local_model_entry, stretch=1)

        btn_local_guide = QPushButton("📖 คู่มือ LM Studio")
        btn_local_guide.setStyleSheet("QPushButton { background-color: #7c3aed; } QPushButton:hover { background-color: #6d28d9; }")
        btn_local_guide.clicked.connect(self._show_lmstudio_guide)
        local_row.addWidget(btn_local_guide)
        brain_layout.addLayout(local_row)

        local_mem_row = QHBoxLayout()
        local_mem_row.addWidget(QLabel("🧠 จำนวนประโยคความจำสำหรับ Local LLM (ค่าเริ่มต้น 50):"))
        self.local_context_limit_entry = QLineEdit()
        self.local_context_limit_entry.setPlaceholderText("50")
        self.local_context_limit_entry.setMaximumWidth(90)
        local_mem_row.addWidget(self.local_context_limit_entry)

        self.chk_local_unlimited = QCheckBox("แนบไม่จำกัด (สำหรับเครื่องสเปกแรง)")
        self.chk_local_unlimited.toggled.connect(
            lambda checked: self.local_context_limit_entry.setEnabled(not checked)
        )
        local_mem_row.addWidget(self.chk_local_unlimited)
        local_mem_row.addStretch()
        brain_layout.addLayout(local_mem_row)

        layout.addWidget(brain_group)

        # 2. Voice Settings Group
        voice_group = QGroupBox("🔊 ระบบเสียงและการพูด (Voice Settings)")
        voice_layout = QVBoxLayout(voice_group)

        speaker_row = QHBoxLayout()
        speaker_row.addWidget(QLabel("เลือกเสียงผู้พูด:"))
        self.speaker_combo = QComboBox()
        self.speaker_combo.addItems([
            "นิวัฒน์ (ชายสุขุม - เหมาะกับ Jarvis)",
            "เปรมวดี (หญิงสดใส นุ่มนวล)",
        ])
        speaker_row.addWidget(self.speaker_combo, stretch=1)

        btn_preview = QPushButton("🔊 ทดสอบฟังเสียง (Preview Voice)")
        btn_preview.setStyleSheet("QPushButton { background-color: #059669; } QPushButton:hover { background-color: #047857; }")
        btn_preview.clicked.connect(self._test_voice_preview)
        speaker_row.addWidget(btn_preview)
        voice_layout.addLayout(speaker_row)

        pitch_row = QHBoxLayout()
        self.pitch_lbl = QLabel("ระดับความทุ้ม/แหลม (Pitch): +0Hz")
        self.pitch_lbl.setMinimumWidth(220)
        pitch_row.addWidget(self.pitch_lbl)

        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setRange(-20, 20)
        self.pitch_slider.setValue(0)
        self.pitch_slider.valueChanged.connect(lambda v: self.pitch_lbl.setText(f"ระดับความทุ้ม/แหลม (Pitch): {v:+d}Hz"))
        pitch_row.addWidget(self.pitch_slider, stretch=1)
        voice_layout.addLayout(pitch_row)

        rate_row = QHBoxLayout()
        self.rate_lbl = QLabel("ความเร็วการพูด (Speed): +0%")
        self.rate_lbl.setMinimumWidth(220)
        rate_row.addWidget(self.rate_lbl)

        self.rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setRange(-20, 20)
        self.rate_slider.setValue(0)
        self.rate_slider.valueChanged.connect(lambda v: self.rate_lbl.setText(f"ความเร็วการพูด (Speed): {v:+d}%"))
        rate_row.addWidget(self.rate_slider, stretch=1)
        voice_layout.addLayout(rate_row)

        layout.addWidget(voice_group)

        # 3. Microphone & Wake Word Group
        mic_group = QGroupBox("🎙️ การดักฟังไมโครโฟน (Microphone & Wake Word)")
        mic_layout = QHBoxLayout(mic_group)

        mic_layout.addWidget(QLabel("คำปลุกเรียกชื่อ (Wake Word):"))
        self.wake_word_entry = QLineEdit()
        self.wake_word_entry.setPlaceholderText("สวัสดีจาร์วิส")
        mic_layout.addWidget(self.wake_word_entry)

        mic_layout.addSpacing(16)
        self.silence_lbl = QLabel("หยุดพูดเงียบ (Timeout): 1.8 วินาที")
        mic_layout.addWidget(self.silence_lbl)

        self.silence_slider = QSlider(Qt.Orientation.Horizontal)
        self.silence_slider.setRange(10, 35)
        self.silence_slider.setValue(18)
        self.silence_slider.valueChanged.connect(self._on_silence_changed)
        mic_layout.addWidget(self.silence_slider, stretch=1)

        layout.addWidget(mic_group)

        # Save Button
        btn_save = QPushButton("💾 บันทึกการตั้งค่าทั้งหมด (Save Settings)")
        btn_save.setMinimumHeight(46)
        btn_save.setStyleSheet(
            "QPushButton { background-color: #16a34a; font-size: 15px; } QPushButton:hover { background-color: #15803d; }"
        )
        btn_save.clicked.connect(self._save_settings)
        layout.addWidget(btn_save)

        layout.addStretch()

    def _on_ai_name_changed(self, text: str):
        clean_name = text.strip() or "จาร์วิส"
        # Automatically sync wake word field when user types a new AI name
        self.wake_word_entry.setText(f"สวัสดี{clean_name}")
        self.sync_hint_lbl.setText(
            f"💡 ซิงค์คำปลุกไมค์อัตโนมัติ: พูดว่า '{clean_name}', 'สวัสดี{clean_name}' หรือเผลอเรียก 'จาร์วิส' ก็สั่งงานได้ทั้งหมดครับ!"
        )

    def _on_persona_changed(self, idx: int):
        if 0 <= idx < len(PERSONA_OPTIONS):
            key, label = PERSONA_OPTIONS[idx]
            self.config_mgr.config["persona_style"] = key
            self.config_mgr.save()
            self.log_message(f"🎭 เปลี่ยนนิสัยของ Jarvis เป็น: {label}")

    def _on_threshold_changed(self, val: int):
        self.threshold_lbl.setText(f"จุดตัดเสียงรบกวน (Threshold): {val:,} / 6,000 (ลากให้สูงกว่าเสียงทีวี)")
        self.listener.energy_threshold = float(val)
        self.config_mgr.config.setdefault("mic", {})["energy_threshold"] = val
        self.config_mgr.save()

    def _on_silence_changed(self, val: int):
        sec = round(val / 10.0, 1)
        self.silence_lbl.setText(f"หยุดพูดเงียบ (Timeout): {sec:.1f} วินาที")
        self.listener.silence_timeout = sec

    def _on_mic_level_update(self, rms: int, above: bool):
        if rms < 0:
            self.mic_meter.setValue(0)
            self.mic_state_lbl.setText("🔇 ล็อกไมค์ชั่วคราวขณะ AI กำลังพูดตอบ (ป้องกันเสียงลำโพงสะท้อนเข้าไมค์)")
            self.mic_state_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #c084fc;")
            return

        self.mic_meter.setValue(min(6000, rms))
        if above:
            self.mic_state_lbl.setText(f"🎙️ กำลังดูดเสียงพูดของคุณ! (ความดัง: {rms:,})")
            self.mic_state_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #4ade80;")
            self.mic_meter.setStyleSheet(
                "QProgressBar { background-color: #111827; border: 1px solid #22c55e; border-radius: 6px; height: 16px; } "
                "QProgressBar::chunk { background-color: #22c55e; border-radius: 5px; }"
            )
        else:
            self.mic_state_lbl.setText(f"🔇 ต่ำกว่าจุดตัดเสียง - ตัดเสียงรบกวน/ทีวี (ความดังห้อง: {rms:,})")
            self.mic_state_lbl.setStyleSheet("font-size: 12px; color: #9ca3af;")
            self.mic_meter.setStyleSheet(
                "QProgressBar { background-color: #111827; border: 1px solid #374151; border-radius: 6px; height: 16px; } "
                "QProgressBar::chunk { background-color: #4b5563; border-radius: 5px; }"
            )

    def _update_free_talk_button(self, enabled: bool):
        self.btn_free_talk.setChecked(enabled)
        ai_name = self.config_mgr.config.get("ai_name", "จาร์วิส")
        if enabled:
            self.btn_free_talk.setText("⚡ คุยอิสระ: เปิด (ไม่ต้องเรียกชื่อ)")
            self.btn_free_talk.setStyleSheet(
                "QPushButton { background-color: #d97706; color: #ffffff; font-size: 13px; } QPushButton:hover { background-color: #b45309; }"
            )
        else:
            self.btn_free_talk.setText(f"⚡ คุยอิสระ: ปิด (ต้องเรียก '{ai_name}')")
            self.btn_free_talk.setStyleSheet(
                "QPushButton { background-color: #374151; color: #d1d5db; font-size: 13px; } QPushButton:hover { background-color: #4b5563; }"
            )

    def _toggle_free_talk(self):
        enabled = self.btn_free_talk.isChecked()
        ai_name = self.config_mgr.config.get("ai_name", "จาร์วิส")
        self.config_mgr.config.setdefault("mic", {})["free_talk_mode"] = enabled
        self.config_mgr.save()
        self._update_free_talk_button(enabled)
        if enabled:
            self.log_message(f"⚡ เปิด 'โหมดคุยอิสระ' แล้ว: พูดปุ๊บส่งข้อความทันทีโดยไม่ต้องเรียกชื่อ '{ai_name}' ครับ!")
        else:
            self.log_message(f"🛡️ ปิด 'โหมดคุยอิสระ' แล้ว: ต้องเรียกชื่อ '{ai_name}' (หรือ 'จาร์วิส') ในประโยคครับ")

    def _update_game_buttons(self, game: str):
        if game == "poe2":
            self.btn_poe2.setChecked(True)
            self.btn_poe1.setChecked(False)
            self.btn_poe2.setStyleSheet("QPushButton { background-color: #2563eb; color: #fff; padding: 8px 16px; }")
            self.btn_poe1.setStyleSheet("QPushButton { background-color: #374151; color: #9ca3af; padding: 8px 16px; }")
        else:
            self.btn_poe1.setChecked(True)
            self.btn_poe2.setChecked(False)
            self.btn_poe1.setStyleSheet("QPushButton { background-color: #7c3aed; color: #fff; padding: 8px 16px; }")
            self.btn_poe2.setStyleSheet("QPushButton { background-color: #374151; color: #9ca3af; padding: 8px 16px; }")

    def _switch_game(self, game: str):
        self.config_mgr.set_active_game(game)
        self._update_game_buttons(game)
        self.update_quota_display()
        title = "Path of Exile 2 (PoE 2)" if game == "poe2" else "Path of Exile 1 (PoE 1)"
        self.log_message(f"🔄 สลับไปโหมด {title} เรียบร้อยแล้ว ฐานข้อมูลและคำแนะนำจะอ้างอิงตามภาคนี้ครับ")

    def _check_app_update(self):
        info = check_for_updates()
        if info.get("has_update"):
            ans = QMessageBox.information(
                self,
                "พบอัปเดตเวอร์ชันใหม่!",
                f"🎉 พบเวอร์ชันใหม่ v{info['latest_version']} (เวอร์ชันปัจจุบันของคุณ: v{info['current_version']})\n\n"
                f"รายละเอียด:\n{info['notes'][:300]}\n\n"
                f"ต้องการเปิดหน้าเว็บ GitHub เพื่อดาวน์โหลดเวอร์ชันล่าสุดเลยหรือไม่ครับ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans == QMessageBox.StandardButton.Yes:
                webbrowser.open(info["release_url"])
        else:
            QMessageBox.information(
                self,
                "เวอร์ชันล่าสุดแล้ว",
                f"✅ คุณกำลังใช้งานเวอร์ชันล่าสุดแล้วครับ (v{APP_VERSION})\n"
                f"ข้อมูลตั้งค่าและไฟล์ความจำถูกเก็บไว้ที่:\n{get_app_data_dir()}",
            )

    def _save_memory_file_dialog(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "บันทึกไฟล์ความจำการสนทนา (เช่น แยกตามตัวละคร/บิลด์)",
            os.path.join(get_app_data_dir(), "memory_profile.json"),
            "JSON Memory Files (*.json)",
        )
        if filepath:
            if not filepath.endswith(".json"):
                filepath += ".json"
            self.brain.save_history_to_file(filepath)
            self.update_quota_display()
            self.log_message(f"💾 บันทึกความจำการคุย ({len(self.brain.chat_history)} ประโยค) ลงไฟล์ '{os.path.basename(filepath)}' เรียบร้อยแล้วครับ!")

    def _load_memory_file_dialog(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "เลือกไฟล์ความจำการสนทนาที่ต้องการโหลด",
            get_app_data_dir(),
            "JSON Memory Files (*.json)",
        )
        if filepath:
            count = self.brain.load_history_from_file(filepath)
            self.update_quota_display()
            self.log_message(f"📂 โหลดความจำจากไฟล์ '{os.path.basename(filepath)}' สำเร็จ ({count} ประโยค) คุยต่อจากเดิมได้ทันทีครับ!")

    def _clear_conversation_memory(self):
        total = len(self.brain.chat_history)
        if total == 0:
            self.log_message("🧹 ความจำบริบทว่างเปล่าอยู่แล้วครับ")
            return
        ans = QMessageBox.question(
            self,
            "ยืนยันการล้างความจำบริบท",
            f"ต้องการล้างความจำการคุยที่สะสมไว้ทั้งหมด ({total} ประโยค) เพื่อเริ่มบริบทใหม่ใช่หรือไม่ครับ?\n"
            "(หากต้องการเก็บไว้ใช้ทีหลัง สามารถกดปุ่ม '💾 เซฟความจำแยกไฟล์' ก่อนล้างได้ครับ)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.brain.clear_history()
            self.update_quota_display()
            self.log_message("🧹 ล้างความจำบริบทที่คุยสะสมไว้เรียบร้อยแล้ว (เริ่มคุยหัวข้อใหม่ได้เลยครับ)")

    def _load_config_to_ui(self):
        cfg = self.config_mgr.config
        self._update_game_buttons(cfg.get("active_game", "poe2"))

        persona_key = cfg.get("persona_style", "short")
        for idx, (k, _) in enumerate(PERSONA_OPTIONS):
            if k == persona_key:
                self.persona_combo.blockSignals(True)
                self.persona_combo.setCurrentIndex(idx)
                self.persona_combo.blockSignals(False)
                break

        self.ai_name_entry.blockSignals(True)
        self.ai_name_entry.setText(cfg.get("ai_name", "จาร์วิส"))
        self.ai_name_entry.blockSignals(False)
        self.user_title_entry.setText(cfg.get("user_title", "ท่าน"))

        self.api_key_entry.setText(cfg.get("gemini_api_key", ""))
        local = cfg.get("local_llm", {})
        if local.get("enabled"):
            self.rb_local.setChecked(True)
        else:
            self.rb_gemini.setChecked(True)

        self.local_url_entry.setText(local.get("server_url", "http://localhost:1234/v1"))
        self.local_model_entry.setText(local.get("model", "qwen2.5-7b-instruct"))
        self.local_context_limit_entry.setText(str(local.get("context_limit", 50)))
        unlim = bool(local.get("unlimited_context", False))
        self.chk_local_unlimited.setChecked(unlim)
        self.local_context_limit_entry.setEnabled(not unlim)

        voice = cfg.get("voice", {})
        speaker = voice.get("speaker", "th-TH-NiwatNeural")
        self.speaker_combo.setCurrentIndex(1 if "Premwadee" in speaker else 0)

        try:
            p_val = int(voice.get("pitch", "+0Hz").replace("Hz", "").replace("+", ""))
            self.pitch_slider.setValue(p_val)
        except Exception:
            pass

        try:
            r_val = int(voice.get("rate", "+0%").replace("%", "").replace("+", ""))
            self.rate_slider.setValue(r_val)
        except Exception:
            pass

        mic = cfg.get("mic", {})
        self.wake_word_entry.setText(mic.get("wake_word", "สวัสดีจาร์วิส"))
        s_val = int(float(mic.get("silence_timeout_sec", 1.8)) * 10)
        self.silence_slider.setValue(s_val)

        thresh = int(mic.get("energy_threshold", 250))
        self.threshold_slider.setValue(thresh)
        self.listener.energy_threshold = float(thresh)

        free_talk = bool(mic.get("free_talk_mode", False))
        self._update_free_talk_button(free_talk)

    def _toggle_key_visibility(self):
        if self.api_key_entry.echoMode() == QLineEdit.EchoMode.Password:
            self.api_key_entry.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.api_key_entry.setEchoMode(QLineEdit.EchoMode.Password)

    def _test_voice_preview(self):
        speaker_id = "th-TH-PremwadeeNeural" if self.speaker_combo.currentIndex() == 1 else "th-TH-NiwatNeural"
        pitch_str = f"{self.pitch_slider.value():+d}Hz"
        rate_str = f"{self.rate_slider.value():+d}%"
        ai_name = self.ai_name_entry.text().strip() or "จาร์วิส"
        user_title = self.user_title_entry.text().strip() or "ท่าน"

        self.log_message(f"🔊 กำลังเล่นเสียงตัวอย่างของ '{ai_name}' (เรียกคุณว่า '{user_title}')...")
        self.preview_worker = VoicePreviewWorker(self.tts, speaker_id, pitch_str, rate_str, ai_name, user_title)
        self.preview_worker.start()

    def _save_settings(self):
        speaker_id = "th-TH-PremwadeeNeural" if self.speaker_combo.currentIndex() == 1 else "th-TH-NiwatNeural"
        active_game = "poe2" if self.btn_poe2.isChecked() else "poe1"
        persona_key = PERSONA_OPTIONS[self.persona_combo.currentIndex()][0]
        ai_name = self.ai_name_entry.text().strip() or "จาร์วิส"
        user_title = self.user_title_entry.text().strip() or "ท่าน"
        wake_word = self.wake_word_entry.text().strip() or f"สวัสดี{ai_name}"
        new_cfg = {
            "gemini_api_key": self.api_key_entry.text().strip(),
            "ai_name": ai_name,
            "user_title": user_title,
            "persona_style": persona_key,
            "active_game": active_game,
            "voice": {
                "engine": "edge-tts",
                "speaker": speaker_id,
                "pitch": f"{self.pitch_slider.value():+d}Hz",
                "rate": f"{self.rate_slider.value():+d}%",
            },
            "mic": {
                "wake_word": wake_word,
                "silence_timeout_sec": round(self.silence_slider.value() / 10.0, 1),
                "energy_threshold": self.threshold_slider.value(),
                "free_talk_mode": self.btn_free_talk.isChecked(),
            },
            "local_llm": {
                "enabled": self.rb_local.isChecked(),
                "server_url": self.local_url_entry.text().strip() or "http://localhost:1234/v1",
                "model": self.local_model_entry.text().strip() or "qwen2.5-7b-instruct",
                "context_limit": int(self.local_context_limit_entry.text().strip()) if self.local_context_limit_entry.text().strip().isdigit() else 50,
                "unlimited_context": self.chk_local_unlimited.isChecked(),
            },
            "daily_quota": self.config_mgr.config.get("daily_quota", {"used": 0, "limit": 1500}),
        }
        self.config_mgr.save(new_cfg)
        self.tts.set_voice(speaker_id, new_cfg["voice"]["pitch"], new_cfg["voice"]["rate"])
        self.listener.energy_threshold = float(new_cfg["mic"]["energy_threshold"])
        self.listener.silence_timeout = new_cfg["mic"]["silence_timeout_sec"]
        self._update_free_talk_button(self.btn_free_talk.isChecked())
        self.update_quota_display()
        self.log_message(
            f"✅ บันทึกการตั้งค่าแล้ว: AI ชื่อ '{ai_name}' | เรียกผู้เล่นว่า '{user_title}' | คำปลุกไมค์: '{ai_name}' หรือ 'จาร์วิส'"
        )
        QMessageBox.information(
            self,
            "บันทึกสำเร็จ",
            f"✅ บันทึกการตั้งค่าเรียบร้อยแล้วครับ!\n\n"
            f"• ชื่อ AI: {ai_name}\n"
            f"• คำเรียกตัวคุณ: {user_title}\n"
            f"• คำเรียกปลุกไมค์: พูดว่า '{ai_name}', '{wake_word}' หรือ 'จาร์วิส' ได้เลยครับ!",
        )

    def update_quota_display(self):
        quota = self.config_mgr.get_quota_status()
        used = quota["used"]
        limit = quota["limit"]
        remaining = quota["remaining"]
        is_local = self.rb_local.isChecked() or self.config_mgr.config.get("local_llm", {}).get("enabled", False)
        if is_local:
            self.quota_lbl.setText(
                f"🟣 โหมด Local LLM: ฟรีไม่จำกัด (ไม่ถูกนับรวมในโควตา!) | โควตา Gemini คงเหลือ: {remaining:,} / {limit:,} ครั้ง"
            )
        else:
            self.quota_lbl.setText(
                f"โควตา Gemini วันนี้: ใช้ไปแล้ว {used:,} / {limit:,} ครั้ง (เหลือ {remaining:,} ครั้ง) | รีเซ็ตทุกวัน 07:00 น."
            )
        self.quota_bar.setMaximum(limit)
        self.quota_bar.setValue(used)

        mem = self.brain.get_memory_stats()
        self.memory_lbl.setText(
            f"🧠 ความจำสะสม: {mem['total_turns']:,} ประโยค (~{mem['est_tokens']:,} โทเคน) | {mem['mode_label']}"
        )

    def log_message(self, msg: str):
        self.log_text.append(msg + "\n")

    def _show_gemini_guide(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("📖 คู่มือขอ Gemini API Key ฟรี (3 สเต็ป)")
        dlg.resize(520, 360)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("วิธีขอ API Key ฟรีจาก Google AI Studio")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #60a5fa;")
        lay.addWidget(title)

        steps = QLabel(
            "1. กดปุ่มด้านล่างเพื่อเข้าเว็บ Google AI Studio\n"
            "   (ล็อกอินด้วยบัญชี Gmail ของคุณ ไม่ต้องผูกบัตรเครดิต)\n\n"
            "2. กดปุ่มสีฟ้า 'Get API key' ตรงเมนูด้านซ้ายบน\n\n"
            "3. กด 'Create API key' แล้วคัดลอก (Copy) รหัสคีย์ยาวๆ\n"
            "   นำมาวางในช่อง 'API Key' ของโปรแกรมนี้ แล้วกดบันทึก\n\n"
            "🎉 ได้โควตาฟรี 1,500 ครั้ง/วัน รีเซ็ตใหม่ทุกเช้า 07:00 น.!"
        )
        steps.setStyleSheet("font-size: 13px; line-height: 1.6;")
        lay.addWidget(steps)

        btn_open = QPushButton("🌐 เปิดหน้าเว็บ Google AI Studio ทันที")
        btn_open.setMinimumHeight(40)
        btn_open.setStyleSheet("QPushButton { background-color: #0284c7; } QPushButton:hover { background-color: #0369a1; }")
        btn_open.clicked.connect(lambda: webbrowser.open("https://aistudio.google.com/app/apikey"))
        lay.addWidget(btn_open)
        dlg.exec()

    def _show_lmstudio_guide(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("📖 คู่มือใช้งาน LM Studio (เล่นออฟไลน์ + ค้นเน็ตอัตโนมัติ)")
        dlg.resize(540, 380)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("วิธีใช้งานคู่กับ LM Studio (ไอคอนสีม่วง)")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #a78bfa;")
        lay.addWidget(title)

        steps = QLabel(
            "1. ดาวน์โหลดโปรแกรม LM Studio จากเว็บ lmstudio.ai\n\n"
            "2. ในช่องค้นหา ดาวน์โหลดโมเดลขนาด 7B-8B ที่แนะนำ:\n"
            "   - 'Qwen 2.5 7B Instruct' (ภาษาไทยเก่งมาก)\n"
            "   - หรือ 'Llama 3.1 8B Instruct'\n\n"
            "3. ไปที่แท็บ Server (ไอคอน <-> ด้านซ้าย)\n"
            "   แล้วกดปุ่มสีเขียว 'Start Server' (พอร์ต 1234)\n\n"
            "4. ในโปรแกรมนี้ เลือกหัวข้อ 'Local LLM' แล้วกดบันทึกได้เลย!\n"
            "   (เมื่อถามเรื่องบอส/ไอเทม ระบบจะค้นเว็บ poe2db ป้อนให้ Local LLM อัตโนมัติ)"
        )
        steps.setStyleSheet("font-size: 13px; line-height: 1.6;")
        lay.addWidget(steps)

        btn_open = QPushButton("🌐 เปิดหน้าเว็บ LM Studio")
        btn_open.setMinimumHeight(40)
        btn_open.setStyleSheet("QPushButton { background-color: #7c3aed; } QPushButton:hover { background-color: #6d28d9; }")
        btn_open.clicked.connect(lambda: webbrowser.open("https://lmstudio.ai/"))
        lay.addWidget(btn_open)
        dlg.exec()

    def _on_sources_discovered(self, sources: list):
        self.last_discovered_sources = sources or []
        count = len(self.last_discovered_sources)
        self.btn_sources.setText(f"🔗 ดูลิงก์เว็บ/บิลด์ ({count})")
        if count > 0:
            self.btn_sources.setStyleSheet(
                "QPushButton { background-color: #0d9488; color: #ffffff; font-size: 13px; } QPushButton:hover { background-color: #14b8a6; }"
            )

    def _show_sources_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("🔗 ลิงก์ข้อมูลเว็บ / บิลด์ที่ AI ค้นพบล่าสุด")
        dlg.resize(680, 500)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)

        header = QLabel("📌 รายการเว็บไซต์และไกด์บิลด์ที่ AI ดึงข้อมูลมาตอบคำถามล่าสุด")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #2dd4bf;")
        lay.addWidget(header)

        if not self.last_discovered_sources:
            empty_lbl = QLabel(
                "ยังไม่มีลิงก์ที่ค้นพบในรอบนี้ครับ\n"
                "ลองพูดถามเรื่องบิลด์ (เช่น 'ไปดูบิลด์ใน Maxroll ให้หน่อยว่าอันไหนแรงๆ') หรือถามเรื่องบอส/ไอเทมได้เลยครับ!"
            )
            empty_lbl.setStyleSheet("font-size: 13px; color: #9ca3af; padding: 20px;")
            lay.addWidget(empty_lbl)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            c_lay = QVBoxLayout(container)
            c_lay.setSpacing(10)

            for idx, src in enumerate(self.last_discovered_sources, 1):
                card = QFrame()
                card.setStyleSheet(
                    "QFrame { background-color: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 10px; }"
                )
                row = QHBoxLayout(card)

                info_col = QVBoxLayout()
                t_lbl = QLabel(f"{idx}. {src.get('title', 'ลิงก์ข้อมูล')}")
                t_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #f9fafb; border: none;")
                info_col.addWidget(t_lbl)

                u_lbl = QLabel(src.get("url", ""))
                u_lbl.setStyleSheet("font-size: 12px; color: #38bdf8; border: none;")
                info_col.addWidget(u_lbl)

                snip = src.get("snippet", "")
                if snip:
                    s_lbl = QLabel(snip[:140] + ("..." if len(snip) > 140 else ""))
                    s_lbl.setStyleSheet("font-size: 12px; color: #9ca3af; border: none;")
                    s_lbl.setWordWrap(True)
                    info_col.addWidget(s_lbl)

                row.addLayout(info_col, stretch=1)

                url_target = src.get("url", "")
                btn_go = QPushButton("🌐 เปิดหน้าเว็บนี้")
                btn_go.setMinimumHeight(36)
                btn_go.setStyleSheet(
                    "QPushButton { background-color: #2563eb; padding: 6px 14px; } QPushButton:hover { background-color: #1d4ed8; }"
                )
                btn_go.clicked.connect(lambda _, u=url_target: webbrowser.open(u))
                row.addWidget(btn_go)

                c_lay.addWidget(card)

            c_lay.addStretch()
            scroll.setWidget(container)
            lay.addWidget(scroll)

        dlg.exec()

    def _manual_screen_test(self):
        self.status_lbl.setText("📸 สถานะ: กำลังแคปหน้าจอและวิเคราะห์...")
        self.status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")
        self.log_message("📸 กำลังแคปภาพหน้าจอเกมและส่งให้ Jarvis วิเคราะห์...")

        self.screen_worker = ScreenTestWorker(self.screen_engine, self.brain, self.tts)
        self.screen_worker.finished_signal.connect(self._on_screen_test_done)
        self.screen_worker.start()

    def _on_screen_test_done(self, resp: str):
        self.log_message(f"🤖 Jarvis: {resp}")
        self.update_quota_display()
        self.status_lbl.setText("🟢 สถานะ: พร้อมทำงาน (Idle)")
        self.status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #4ade80;")

    def _stop_jarvis_speech(self):
        self.tts.stop()
        self.log_message("🔇 สั่งให้ Jarvis หยุดพูดทันที (ไมโครโฟนพร้อมรับคำสั่งใหม่แล้วครับ)")

    def _toggle_listening(self):
        if not self.is_listening:
            # Ensure any previous worker has fully released the microphone stream before opening a new one
            if self.listen_worker and self.listen_worker.isRunning():
                self.listen_worker.stop()
                self.listen_worker.wait(1500)
            if self.cmd_worker and self.cmd_worker.isRunning():
                self.cmd_worker.stop()
                self.cmd_worker.wait(1000)

            self.is_listening = True
            self.btn_listen.setText("⏹️ หยุดฟังเสียง (Stop)")
            self.btn_listen.setStyleSheet(
                "QPushButton { background-color: #dc2626; font-size: 13px; } QPushButton:hover { background-color: #b91c1c; }"
            )
            self.status_lbl.setText("🎙️ สถานะ: กำลังรอฟังเสียง... (พูดได้เลยครับ)")
            self.status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #f59e0b;")
            self.mic_state_lbl.setText("🎙️ กำลังเชื่อมต่อไมโครโฟนสด...")
            self.log_message("🎙️ เปิดไมโครโฟนแบบ Continuous Stream แล้ว (ไม่ตัด/ไม่ค้างระหว่างคุยต่อเนื่องครับ)")

            self.cmd_queue = queue.Queue()
            self.cmd_worker = CommandQueueWorker(
                self.cmd_queue,
                self.config_mgr,
                self.listener,
                self.screen_engine,
                self.brain,
                self.tts,
            )
            self.cmd_worker.log_signal.connect(self.log_message)
            self.cmd_worker.status_signal.connect(self._update_status_from_worker)
            self.cmd_worker.quota_signal.connect(self.update_quota_display)
            self.cmd_worker.sources_signal.connect(self._on_sources_discovered)
            self.cmd_worker.start()

            self.listen_worker = ListenLoopWorker(
                self.listener, self.cmd_queue, self.tts
            )
            self.listen_worker.mic_level_signal.connect(self._on_mic_level_update)
            self.listen_worker.start()
        else:
            self.is_listening = False
            if self.listen_worker:
                self.listen_worker.stop()
                self.listen_worker.wait(1500)
            if self.cmd_worker:
                self.cmd_worker.stop()
            self.mic_meter.setValue(0)
            self.mic_state_lbl.setText("ระดับเสียงไมค์สด: ปิดไมค์อยู่")
            self.mic_state_lbl.setStyleSheet("font-size: 12px; color: #9ca3af;")
            self.btn_listen.setText("🎙️ เริ่มต้นเปิดไมค์ (Start)")
            self.btn_listen.setStyleSheet(
                "QPushButton { background-color: #2563eb; font-size: 13px; } QPushButton:hover { background-color: #1d4ed8; }"
            )
            self.status_lbl.setText("🟢 สถานะ: พร้อมทำงาน (Idle)")
            self.status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #4ade80;")
            self.log_message("⏹️ ปิดการดักฟังไมโครโฟนเรียบร้อยแล้ว")

    def _update_status_from_worker(self, text: str, color: str):
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color};")


def PoEJarvisApp():
    app = QApplication(sys.argv)
    win = PoEJarvisWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    PoEJarvisApp()
