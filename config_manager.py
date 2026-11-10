import json
import os
import sys
from datetime import datetime, timezone, timedelta


def get_app_data_dir() -> str:
    """
    Returns the persistent data directory:
    - When running as a packaged executable (.app / .exe via PyInstaller):
      uses ~/Documents/PoE_Jarvis_Companion so config.json and chat_memory.json
      are safely persisted outside the read-only bundle.
    - When running from source: uses the project directory.
    """
    if getattr(sys, "frozen", False):
        docs_dir = os.path.join(os.path.expanduser("~"), "Documents", "PoE_Jarvis_Companion")
        os.makedirs(docs_dir, exist_ok=True)
        return docs_dir
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_app_data_dir(), "config.json")

DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "ai_name": "จาร์วิส",
    "user_title": "ท่าน",
    "persona_style": "short",  # "short", "normal", "funny", "serious"
    "active_game": "poe2",  # "poe1" or "poe2"
    "voice": {
        "engine": "edge-tts",  # "edge-tts" or "gemini-native"
        "speaker": "th-TH-NiwatNeural",  # "th-TH-NiwatNeural" (ชาย) or "th-TH-PremwadeeNeural" (หญิง)
        "pitch": "+0Hz",
        "rate": "+0%"
    },
    "mic": {
        "wake_word": "สวัสดีจาร์วิส",
        "silence_timeout_sec": 1.8,
        "energy_threshold": 250,
        "free_talk_mode": False
    },
    "local_llm": {
        "enabled": False,
        "server_url": "http://localhost:1234/v1",
        "model": "qwen2.5-7b-instruct",
        "context_limit": 50,
        "unlimited_context": False
    },
    "daily_quota": {
        "last_reset_date": "",
        "used": 0,
        "limit": 1500
    }
}

class ConfigManager:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.config = self.load()

    def load(self):
        if not os.path.exists(self.path):
            self.save(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with default in case of new keys
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
        except Exception as e:
            print(f"[ConfigManager] Error reading config: {e}. Using defaults.")
            return DEFAULT_CONFIG.copy()

    def save(self, data=None):
        if data is None:
            data = self.config
        else:
            self.config = data
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_quota_status(self):
        """
        Quota resets every day at 00:00 UTC (07:00 AM Thailand time / UTC+7).
        """
        tz_th = timezone(timedelta(hours=7))
        now_th = datetime.now(tz_th)
        # Reset cycle is considered the date string
        # If it's before 7 AM, it belongs to yesterday's cycle
        if now_th.hour < 7:
            cycle_date = (now_th - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            cycle_date = now_th.strftime("%Y-%m-%d")

        quota = self.config.get("daily_quota", {})
        if quota.get("last_reset_date") != cycle_date:
            quota["last_reset_date"] = cycle_date
            quota["used"] = 0
            self.save()

        used = quota.get("used", 0)
        limit = quota.get("limit", 1500)
        remaining = max(0, limit - used)
        return {
            "cycle_date": cycle_date,
            "used": used,
            "limit": limit,
            "remaining": remaining
        }

    def increment_quota(self):
        status = self.get_quota_status()
        self.config["daily_quota"]["used"] += 1
        self.save()
        return self.config["daily_quota"]["used"]

    def set_active_game(self, game: str):
        if game in ["poe1", "poe2"]:
            self.config["active_game"] = game
            self.save()
            return True
        return False
