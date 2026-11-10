import json
import os
import re
import urllib.parse
import requests
from config_manager import ConfigManager, get_app_data_dir
from knowledge_prompts import get_system_instruction

APP_DIR = get_app_data_dir()

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
]

SEARCH_TRIGGER_KEYWORDS = [
    "บอส", "boss", "ไอเทม", "สกิล", "ธนู", "ดาบ", "ไม้เท้า", "เกราะ", "เสื้อ",
    "เควสต์", "เควส", "บิลด์", "บิ้ว", "บิว", "บิวต์", "เมต้า", "meta",
    "แม็คโคร", "แมคโคร", "มักโรล", "แม็กโรล", "maxroll", "mobalytics",
    "build", "poe", "แพตช์", "patch", "ดรอป", "คราฟ", "เจ็ม", "gem",
    "spirit", "สปิริต", "atlas", "แมพ", "map", "act ", "บทที่",
    "พาสซีฟ", "passive", "ascendancy", "คลาส", "class", "วิธี", "คืออะไร",
    "ทำยังไง", "หลบยังไง", "ตียังไง", "แก้ทาง", "สเตตัส", "ค่าเงิน", "divine",
    "chaos", "exalted", "ข่าว", "อากาศ", "ค้นหา", "หาข้อมูล", "เนื้อเรื่อง", "lore"
]

BUILD_REQUEST_KEYWORDS = [
    "build", "บิลด์", "บิ้ว", "บิว", "บิวต์", "maxroll", "mobalytics",
    "เมต้า", "meta", "จัดสาย", "สายไหนดี", "สายไหนแรง", "บิลไหน"
]

NEGATION_PHRASES = [
    "ไม่เอา", "ไม่ได้ขอ", "ยังไม่ทันขอ", "ไม่ได้ต้องการ", "ไม่ต้องการ",
    "ไม่ต้องส่ง", "ส่งมาให้ทำไม", "ไม่ได้ถาม"
]

SPOKEN_TERM_NORMALIZATION = {
    "แม็คโคร": "Maxroll",
    "แมคโคร": "Maxroll",
    "มักโรล": "Maxroll",
    "แม็กโรล": "Maxroll",
    "แม็กซ์โรล": "Maxroll",
    "บิ้ว": "Build",
    "บิวต์": "Build",
    "บอส X1": "บอส Act 1 (บทที่ 1)",
    "บอส x1": "บอส Act 1 (บทที่ 1)",
    "บอส X2": "บอส Act 2 (บทที่ 2)",
    "บอส x2": "บอส Act 2 (บทที่ 2)",
    "บอส X3": "บอส Act 3 (บทที่ 3)",
    "บอส x3": "บอส Act 3 (บทที่ 3)",
    "แอค 1": "Act 1 (บทที่ 1)",
    "แอ็ค 1": "Act 1 (บทที่ 1)",
    "แอค 2": "Act 2 (บทที่ 2)",
    "แอ็ค 2": "Act 2 (บทที่ 2)",
    "แอค 3": "Act 3 (บทที่ 3)",
    "แอ็ค 3": "Act 3 (บทที่ 3)",
    "ดรูนอิท": "Druid (ดรูอิด)",
    "ดรูนอิด": "Druid (ดรูอิด)",
    "ดรูอิท": "Druid (ดรูอิด)",
    "ดรูอิด": "Druid (ดรูอิด)",
    "ซอเซอร์เรส": "Sorceress",
    "ซอเซอเรส": "Sorceress",
    "เมอร์เซนารี": "Mercenary",
    "ฮันเทรส": "Huntress",
    "มังค์": "Monk",
    "วอริเออร์": "Warrior",
    "เรนเจอร์": "Ranger",
}

POE_CLASS_ALIASES = {
    "druid": ["druid", "ดรูอิด", "ดรูอิท", "ดรูนอิท", "ดรูนอิด", "shaman", "ชาแมน", "oracle", "ออราเคิล", "แปลงร่าง", "หมี", "หมาป่า"],
    "sorceress": ["sorceress", "ซอเซอร์เรส", "ซอเซอเรส", "stormweaver", "chronomancer", "varashta", "นักเวท", "แม่มดธาตุ"],
    "monk": ["monk", "มังค์", "พระ", "invoker", "chayula", "martial artist"],
    "ranger": ["ranger", "เรนเจอร์", "deadeye", "เดดอาย", "pathfinder", "พาทไฟน์เดอร์"],
    "mercenary": ["mercenary", "เมอร์เซนารี", "ทหารรับจ้าง", "witchhunter", "gemling", "tactician", "หน้าไม้"],
    "warrior": ["warrior", "วอริเออร์", "นักรบ", "titan", "ไททัน", "warbringer"],
    "witch": ["witch", "วิทช์", "แม่มดมืด", "เนโคร", "infernalist", "blood mage", "lich", "ลิช"],
    "huntress": ["huntress", "ฮันเทรส", "amazon", "อเมซอน", "spirit walker", "หอก"],
}


def detect_class_slug(text: str) -> str:
    """Detects if a specific PoE class is mentioned in the text and returns its Maxroll slug."""
    lower = text.lower()
    for slug, keywords in POE_CLASS_ALIASES.items():
        if any(kw in lower for kw in keywords):
            return slug
    return ""


def compact_text(text: str, max_len: int = 200) -> str:
    """Compacts a response into a concise 1-2 sentence summary for rolling 20-turn memory."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rsplit(" ", 1)[0] + "..."


def normalize_spoken_query(query: str) -> str:
    """Normalizes common Thai Speech-to-Text mishearings of PoE terms (e.g. 'แม็คโคร' -> 'Maxroll')."""
    normalized = query
    for wrong, right in SPOKEN_TERM_NORMALIZATION.items():
        if wrong in normalized:
            normalized = normalized.replace(wrong, right)
    return normalized


def clean_markdown_for_speech(text: str) -> str:
    """Removes Markdown formatting (*, #, `) so TTS reads cleanly."""
    cleaned = re.sub(r"[*#`_~]+", "", text)
    return cleaned.strip()


def is_asking_for_build(query: str) -> bool:
    """
    Returns True ONLY when the current spoken question explicitly asks for a build/guide
    and is NOT a negation (e.g. 'ผมยังไม่ทันขอบิลเลย', 'ไม่ได้ต้องการบิว').
    """
    lower = query.lower()
    if any(neg in lower for neg in NEGATION_PHRASES):
        return False
    return any(k in lower for k in BUILD_REQUEST_KEYWORDS)


def should_search_web(query: str) -> bool:
    """
    Returns True if the CURRENT query asks about builds, bosses, items, lore, or news.
    Never triggers on casual greetings or negations.
    """
    lower = query.lower()
    if any(neg in lower for neg in NEGATION_PHRASES):
        # Still allow searching if they negated build but asked about a boss in the same sentence
        if not any(b in lower for b in ["บอส", "boss", "act ", "บทที่", "เควส", "ไอเทม"]):
            return False
    return any(kw in lower for kw in SEARCH_TRIGGER_KEYWORDS) or bool(detect_class_slug(lower))


def fetch_maxroll_builds(
    active_game: str = "poe2",
    class_slug: str = "",
    max_builds: int = 8,
) -> tuple[str, list[dict]]:
    """
    Directly fetches the latest live Build Guides from Maxroll.gg (PoE 2 or PoE 1).
    If class_slug is provided (e.g. 'druid'), fetches specifically from /poe2/build-guides/druid!
    """
    base_path = "/poe2/build-guides" if active_game == "poe2" else "/poe/build-guides"
    if class_slug and active_game == "poe2":
        index_url = f"https://maxroll.gg{base_path}/{class_slug}"
    else:
        index_url = f"https://maxroll.gg{base_path}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    sources = []
    summary_lines = []
    try:
        r = requests.get(index_url, headers=headers, timeout=4)
        if r.status_code != 200:
            return "", []

        raw_links = re.findall(rf'href="({base_path}/[a-z0-9\-]+)"', r.text)
        class_hubs = {
            "druid", "huntress", "mercenary", "monk", "ranger", "sorceress",
            "warrior", "witch", "duelist", "marauder", "shadow", "templar", "scion",
            "league-starter", "twink-leveling", "leveling"
        }
        seen = set()
        for rel in raw_links:
            slug = rel.split("/")[-1]
            if slug in class_hubs or slug in seen:
                continue
            seen.add(slug)
            full_url = f"https://maxroll.gg{rel}"
            readable_title = slug.replace("-build-guide", "").replace("-guide", "").replace("-", " ").title()
            class_tag = f" ({class_slug.title()})" if class_slug else ""
            sources.append({
                "title": f"[Maxroll{class_tag}] {readable_title}",
                "url": full_url,
                "snippet": f"ไกด์บิลด์ {readable_title}{class_tag} บนเว็บ Maxroll ({active_game.upper()})"
            })
            summary_lines.append(f"- บิลด์{class_tag}: {readable_title} (ลิงก์: {full_url})")
            if len(sources) >= max_builds:
                break

        return "\n".join(summary_lines), sources
    except Exception:
        return "", []


def fetch_web_context_and_links(
    query: str,
    active_game: str = "poe2",
    context_hint: str = "",
    max_results: int = 4,
) -> tuple[str, list[dict]]:
    """
    Performs live web search + class-aware Maxroll build extraction, returning:
      (context_string_for_llm, list_of_source_dicts_with_title_url_snippet)
    """
    norm_query = normalize_spoken_query(query)
    lower_q = norm_query.lower()

    wants_build = is_asking_for_build(norm_query)
    # Only inherit class from conversation history if the user is CURRENTLY asking for a build!
    if wants_build:
        target_class = detect_class_slug(norm_query) or detect_class_slug(context_hint)
    else:
        target_class = detect_class_slug(norm_query)

    all_sources = []
    context_parts = []

    # 1. ONLY pull Maxroll build guides if the CURRENT question explicitly asks for a Build / Maxroll!
    if wants_build:
        maxroll_text, maxroll_sources = fetch_maxroll_builds(
            active_game=active_game, class_slug=target_class, max_builds=6
        )
        if maxroll_text:
            header_tag = f"เฉพาะอาชีพ {target_class.upper()}" if target_class else "เมต้าล่าสุด"
            context_parts.append(
                f"[รายชื่อบิลด์{header_tag}จากเว็บ Maxroll ({active_game.upper()})]:\n{maxroll_text}"
            )
            all_sources.extend(maxroll_sources)

    # 2. Search DuckDuckGo HTML for specific details + URLs about the CURRENT question
    game_tag = "Path of Exile 2" if active_game == "poe2" else "Path of Exile 1"
    if any(w in norm_query for w in ["อากาศ", "ฝน", "ข่าว", "วันที่", "กี่โมง"]):
        search_query = norm_query
    elif wants_build:
        class_search_str = f" {target_class}" if target_class and target_class not in lower_q else ""
        search_query = f"{game_tag} maxroll{class_search_str} {norm_query}"
    else:
        db_tag = "poe2db" if active_game == "poe2" else "poedb"
        search_query = f"{game_tag} {db_tag} {norm_query}"

    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.post(url, data={"q": search_query}, headers=headers, timeout=4)
        if res.status_code == 200:
            titles = re.findall(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>', res.text, flags=re.DOTALL)
            snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', res.text, flags=re.DOTALL)

            ddg_lines = []
            for (href, raw_title), raw_snip in zip(titles[:max_results], snippets[:max_results]):
                if "uddg=" in href:
                    real_url = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                else:
                    real_url = href
                t_clean = re.sub(r"<[^>]+>", "", raw_title).replace("&quot;", '"').replace("&amp;", "&").replace("&#x27;", "'").strip()
                s_clean = re.sub(r"<[^>]+>", "", raw_snip).replace("&quot;", '"').replace("&amp;", "&").replace("&#x27;", "'").strip()
                if s_clean:
                    ddg_lines.append(f"- {t_clean}: {s_clean} (URL: {real_url})")
                    if not any(src["url"] == real_url for src in all_sources):
                        all_sources.append({
                            "title": t_clean,
                            "url": real_url,
                            "snippet": s_clean,
                        })
            if ddg_lines:
                context_parts.append("[ผลการค้นหาข้อมูลเพิ่มเติมจากเว็บ]:\n" + "\n".join(ddg_lines))
    except Exception:
        pass

    return "\n\n".join(context_parts), all_sources


def fetch_web_context(query: str, active_game: str = "poe2", max_results: int = 3) -> str:
    ctx, _ = fetch_web_context_and_links(query, active_game=active_game, max_results=max_results)
    return ctx


DEFAULT_MEMORY_PATH = os.path.join(APP_DIR, "chat_memory.json")


class PoEJarvisBrain:
    def __init__(self, config_manager: ConfigManager = None):
        self.config_mgr = config_manager or ConfigManager()
        self.last_sources: list[dict] = []
        self.chat_history: list[dict] = []
        self.active_memory_path: str = DEFAULT_MEMORY_PATH
        self.load_history_from_file(self.active_memory_path)

    @property
    def config(self):
        return self.config_mgr.config

    def save_history_to_file(self, filepath: str = None) -> str:
        """Saves accumulated conversation memory to disk (auto-saves to chat_memory.json or custom character file)."""
        target = filepath or self.active_memory_path or DEFAULT_MEMORY_PATH
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.chat_history, f, ensure_ascii=False, indent=2)
            if filepath:
                self.active_memory_path = filepath
        except Exception as e:
            print(f"[Memory Save Error] {e}")
        return target

    def load_history_from_file(self, filepath: str = None) -> int:
        """Loads conversation memory from disk and returns number of turns loaded."""
        target = filepath or self.active_memory_path or DEFAULT_MEMORY_PATH
        if not os.path.exists(target):
            return 0
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.chat_history = [
                    t for t in data if isinstance(t, dict) and "user" in t and "ai" in t
                ]
                self.active_memory_path = target
                # Also sync to default chat_memory.json so next app launch remembers this loaded profile
                if os.path.abspath(target) != os.path.abspath(DEFAULT_MEMORY_PATH):
                    self.save_history_to_file(DEFAULT_MEMORY_PATH)
                    self.active_memory_path = target
                return len(self.chat_history)
        except Exception as e:
            print(f"[Memory Load Error] {e}")
        return 0

    def clear_history(self):
        """Clears the conversation memory and updates the saved file."""
        self.chat_history.clear()
        self.save_history_to_file(DEFAULT_MEMORY_PATH)
        if self.active_memory_path and os.path.exists(self.active_memory_path):
            self.save_history_to_file(self.active_memory_path)

    def _get_active_turns_slice(self) -> list[dict]:
        """
        Determines which turns to attach based on current brain mode:
        - Gemini: Unlimited (attaches all turns so AI knows the player better over time)
        - Local LLM: Uses local_llm.context_limit (default 50) unless local_llm.unlimited_context is True
        """
        if not self.chat_history:
            return []
        local_cfg = self.config.get("local_llm", {})
        if local_cfg.get("enabled", False):
            if local_cfg.get("unlimited_context", False):
                return self.chat_history
            limit = int(local_cfg.get("context_limit", 50) or 50)
            return self.chat_history[-limit:] if limit > 0 else self.chat_history
        # Gemini mode: attach all accumulated turns (Unlimited!)
        return self.chat_history

    def get_memory_stats(self) -> dict:
        """Returns live statistics about accumulated conversation memory and token usage."""
        total_turns = len(self.chat_history)
        active_slice = self._get_active_turns_slice()
        active_turns = len(active_slice)
        total_chars = sum(len(t.get("user", "")) + len(t.get("ai", "")) + 18 for t in active_slice)
        # Approx 1 token per 3.2 characters for mixed Thai/English compact text
        est_tokens = int(total_chars / 3.2) if total_chars > 0 else 0

        local_cfg = self.config.get("local_llm", {})
        if local_cfg.get("enabled", False):
            if local_cfg.get("unlimited_context", False):
                mode_label = "Local LLM (แนบไม่จำกัด)"
            else:
                limit = int(local_cfg.get("context_limit", 50) or 50)
                mode_label = f"Local LLM (แนบสูงสุด {limit} ประโยคล่าสุด)"
        else:
            mode_label = "Gemini (แนบไม่จำกัด - ยิ่งคุย AI ยิ่งรู้จักคุณมากขึ้น)"

        return {
            "total_turns": total_turns,
            "active_turns": active_turns,
            "est_tokens": est_tokens,
            "mode_label": mode_label,
            "file_name": os.path.basename(self.active_memory_path or DEFAULT_MEMORY_PATH),
        }

    def _get_recent_context_hint(self, last_n: int = 4) -> str:
        """Returns recent user queries & AI answers to detect ongoing class/topic context."""
        if not self.chat_history:
            return ""
        recent = self.chat_history[-last_n:]
        return " ".join(f"{t['user']} {t['ai']}" for t in recent)

    def _format_compact_history(self) -> str:
        """Formats the active slice of past turns in a compact summary block."""
        active_slice = self._get_active_turns_slice()
        if not active_slice:
            return ""
        lines = []
        for idx, turn in enumerate(active_slice, 1):
            lines.append(f"{idx}. ผู้เล่น: {turn['user']} -> AI: {turn['ai']}")
        return (
            f"[ประวัติการสนทนาที่ผ่านมา ({len(active_slice)} ประโยค - ใช้เป็นข้อมูลอ้างอิงเฉพาะเมื่อผู้เล่นถามต่อเนื่องเรื่องเดิมเท่านั้น! "
            f"หากคำถามปัจจุบันเป็นการทักทายทั่วไป หรือเปลี่ยนไปถามเรื่องอื่น เช่น บอส/เควสต์/ไอเทม ให้ตอบเฉพาะเรื่องใหม่ทันที ห้ามนำเรื่องบิลด์เก่ามาปนเด็ดขาด!)]:\n"
            + "\n".join(lines)
        )

    def _record_turn(self, user_query: str, ai_response: str):
        """Saves a compacted version of the turn into persistent memory and auto-saves to disk."""
        if not user_query or not ai_response:
            return
        self.chat_history.append({
            "user": compact_text(normalize_spoken_query(user_query), max_len=150),
            "ai": compact_text(ai_response, max_len=220),
        })
        self.save_history_to_file(DEFAULT_MEMORY_PATH)
        if self.active_memory_path and os.path.abspath(self.active_memory_path) != os.path.abspath(DEFAULT_MEMORY_PATH):
            self.save_history_to_file(self.active_memory_path)

    def _prepare_augmented_query(self, query: str, active_game: str) -> str:
        """
        Normalizes spoken terms (e.g. 'แม็คโคร' -> 'Maxroll', 'ดรูนอิท' -> 'Druid'),
        attaches rolling 20-turn compact conversation history, and fetches class-aware
        live web / Maxroll build data for BOTH Gemini and Local LLM.
        """
        self.last_sources = []
        norm_query = normalize_spoken_query(query)
        context_hint = self._get_recent_context_hint(last_n=4)
        history_block = self._format_compact_history()

        parts = []
        if history_block:
            parts.append(history_block)

        # ONLY trigger web search if the CURRENT question (norm_query) needs web info!
        if should_search_web(norm_query):
            web_context, sources = fetch_web_context_and_links(
                norm_query, active_game=active_game, context_hint=context_hint
            )
            self.last_sources = sources
            if web_context:
                parts.append(f"[ข้อมูลอ้างอิงสดจากอินเทอร์เน็ตที่ค้นหามาสำหรับคำถามนี้]:\n{web_context}")

        parts.append(f"[คำถามปัจจุบันของผู้เล่น]: {norm_query}")
        parts.append(
            "[คำสั่งสำคัญ]: โฟกัสตอบเฉพาะสิ่งที่ผู้เล่นถามใน [คำถามปัจจุบันของผู้เล่น] เป็นหลัก! "
            "หากผู้เล่นแค่ทักทายหรือถามเรื่องบอส/เรื่องอื่น ห้ามพูดเรื่องบิลด์หรือยัดเยียดบิลด์จากประวัติเก่าเด็ดขาด! "
            "และห้ามพูดผลัดวันประกันพรุ่งว่า 'เดี๋ยวไปหาข้อมูลมาให้'"
        )
        return "\n\n".join(parts)

    def ask(self, query: str, image_bytes: bytes = None) -> str:
        """
        Sends a query (and optional screenshot) to the chosen brain
        (Gemini API or Local LLM) and returns the text response.
        """
        self.config_mgr.load()
        local_cfg = self.config.get("local_llm", {})
        if local_cfg.get("enabled"):
            return self._ask_local_llm(query)
        else:
            return self._ask_gemini(query, image_bytes)

    def _ask_gemini(self, query: str, image_bytes: bytes = None) -> str:
        api_key = self.config.get("gemini_api_key", "").strip() or os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return (
                "ยังไม่ได้ตั้งค่า Gemini API Key ครับท่าน "
                "กรุณากรอก API Key ในเมนูการตั้งค่า แล้วกดปุ่มบันทึกครับ"
            )

        if not GENAI_AVAILABLE:
            return "ไม่พบโมดูล google-genai ในเครื่อง กรุณาติดตั้งผ่าน pip install google-genai ครับ"

        ai_name = self.config.get("ai_name", "จาร์วิส")
        user_title = self.config.get("user_title", "ท่าน")
        active_game = self.config.get("active_game", "poe2")
        persona_style = self.config.get("persona_style", "short")
        system_instruction = get_system_instruction(
            ai_name=ai_name,
            user_title=user_title,
            active_game=active_game,
            persona_style=persona_style,
        )

        quota_status = self.config_mgr.get_quota_status()
        if quota_status["remaining"] <= 0:
            return (
                f"ขออภัยครับ{user_title} โควตาฟรีประจำวันนี้เต็มแล้ว ({quota_status['limit']} ครั้ง) "
                "ระบบจะรีเซ็ตโควตาใหม่เวลา 07:00 น. ครับ"
            )

        augmented_query = self._prepare_augmented_query(query, active_game=active_game)

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )

        contents = []
        if image_bytes:
            contents.append(
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                )
            )
        contents.append(augmented_query)

        base_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
        )

        last_err = None
        for model_name in GEMINI_FALLBACK_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=base_config,
                )
                self.config_mgr.increment_quota()
                cleaned_ans = clean_markdown_for_speech(response.text or "")
                self._record_turn(query, cleaned_ans)
                return cleaned_ans
            except Exception as e:
                last_err = e
                continue

        return f"เกิดข้อผิดพลาดในการประมวลผลของจาร์วิส: {last_err}"

    def _ask_local_llm(self, query: str) -> str:
        local_cfg = self.config.get("local_llm", {})
        server_url = local_cfg.get("server_url", "http://localhost:1234/v1")
        model = local_cfg.get("model", "qwen2.5-7b-instruct")
        ai_name = self.config.get("ai_name", "จาร์วิส")
        user_title = self.config.get("user_title", "ท่าน")
        active_game = self.config.get("active_game", "poe2")
        persona_style = self.config.get("persona_style", "short")
        system_instruction = get_system_instruction(
            ai_name=ai_name,
            user_title=user_title,
            active_game=active_game,
            persona_style=persona_style,
        )

        augmented_query = self._prepare_augmented_query(query, active_game=active_game)

        endpoint = f"{server_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": augmented_query},
            ],
            "temperature": 0.7,
        }

        try:
            res = requests.post(endpoint, json=payload, timeout=30)
            res.raise_for_status()
            data = res.json()
            cleaned_ans = clean_markdown_for_speech(data["choices"][0]["message"]["content"])
            self._record_turn(query, cleaned_ans)
            return cleaned_ans
        except Exception as e:
            return f"ไม่สามารถเชื่อมต่อไปยัง Local LLM ({server_url}) ได้: {e} กรุณาตรวจสอบว่ากด Start Server ใน LM Studio หรือยังครับ"
