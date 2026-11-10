# 🛡️ PoE Jarvis Companion (รองรับทั้ง PoE 1 และ PoE 2)

ผู้ช่วยอัจฉริยะส่วนตัวสั่งการด้วยเสียง สำหรับเกม **Path of Exile 1** และ **Path of Exile 2**

---

## 🌟 ฟีเจอร์เด่นในโปรแกรม (Phase 1, 2, 3)

* 🎮 **รองรับทั้งสองภาค:** สลับโหมดความรู้ระหว่าง **Path of Exile 1** และ **Path of Exile 2** ได้ด้วยคลิกเดียว
* 🧠 **สมองไฮบริด (Hybrid Brain):** 
  * เชื่อมต่อ **Google Gemini 2.0 Flash** พร้อม Google Search Grounding ดึงข้อมูลสดจาก `poedb.tw` และ `poe2db.tw`
  * หรือเลือกเล่นแบบออฟไลน์ด้วย **Local LLM** (LM Studio / Ollama)
* 📊 **ระบบนับโควตารายวัน:** แสดงสถานะโควตาฟรี `X / 1,500 ครั้ง` ชัดเจน และรีเซ็ตอัตโนมัติทุกเช้า 07:00 น. (เวลาไทย)
* 🔊 **ระบบเสียงไทยธรรมชาติ (Edge-TTS):** ปรับความทุ้มแหลม (Pitch) และความเร็วได้ พร้อมปุ่ม **"ทดสอบฟังเสียง (Preview)"**
* 📸 **ระบบมองหน้าจออัจฉริยะ:** แคปภาพหน้าจอเกมความละเอียดสูงในเวลาเพียง ~100ms เฉพาะเมื่อมีคีย์เวิร์ด (เช่น *"บอสตัวนี้"*, *"ดูหน้าจอ"*, *"ธนูนี้"*)
* 📖 **คู่มือในตัวแบบ Step-by-Step:** มีปุ่มสอนวิธีขอ Gemini API Key ฟรี 3 ขั้นตอน และคู่มือตั้งค่า LM Studio สำหรับเพื่อนๆ ทุกคน

---

## 🚀 วิธีการเปิดใช้งาน

### 1. เปิดหน้าต่างโปรแกรม (GUI Dashboard)
```bash
./.venv/bin/python main.py
```
*(จะมีหน้าต่าง Dashboard สี Dark Mode สวยงามขึ้นมาให้ใช้งาน)*

### 2. หรือใช้งานผ่าน Command Line (CLI Mode)
```bash
./.venv/bin/python main.py --cli
```

---

## 📁 โครงสร้างไฟล์ในโปรเจกต์

* `main.py` : ตัวเปิดโปรแกรมหลัก (GUI Dashboard & CLI Mode)
* `ui_app.py` : หน้าต่างแอปพลิเคชันสวยงามด้วย CustomTkinter
* `companion.py` : ตัวประมวลผลหลัก (เสียง $\rightarrow$ แคปจอ $\rightarrow$ ถาม Gemini $\rightarrow$ ตอบกลับ)
* `voice_listener.py` : ตัวดักฟังไมโครโฟน, Wake Word, และ Silence Timeout
* `screen_capture.py` : ระบบจับภาพหน้าจอเกมความเร็วสูง (~100ms)
* `brain.py` : ตัวเชื่อมต่อสมอง Gemini 2.0 Flash / Local LLM
* `tts_engine.py` : ระบบสังเคราะห์เสียงภาษาไทยธรรมชาติ พร้อมปุ่มเทสต์เสียง
* `knowledge_prompts.py` : คำสั่ง System Prompts กำหนดบุคลิก Jarvis และแยกความรู้ PoE 1 vs PoE 2
* `config_manager.py` : ตัวบันทึกการตั้งค่า และนับโควตารายวัน 1,500 ครั้ง
* `config.json` : ไฟล์เก็บการตั้งค่าส่วนบุคคลของเครื่อง
