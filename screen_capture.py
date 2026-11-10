import io
import mss
from PIL import Image

class ScreenCaptureEngine:
    def __init__(self):
        try:
            self.sct = mss.MSS()
        except AttributeError:
            self.sct = mss.mss()

    def capture_screen_bytes(self, max_dimension: int = 1280, quality: int = 80) -> bytes:
        """
        Captures the primary monitor screen, resizes if needed,
        and returns compressed JPEG bytes ready for Gemini Vision API.
        Takes ~30-50ms.
        """
        try:
            # Monitor 1 is typically the primary monitor (Monitor 0 is all monitors combined)
            monitor = self.sct.monitors[1] if len(self.sct.monitors) > 1 else self.sct.monitors[0]
            sct_img = self.sct.grab(monitor)

            # Convert to PIL Image (RGB)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            # Resize keeping aspect ratio if larger than max_dimension
            width, height = img.size
            if max(width, height) > max_dimension:
                scale = max_dimension / max(width, height)
                new_size = (int(width * scale), int(height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Encode as JPEG bytes in memory (no disk I/O overhead)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            return buffer.getvalue()
        except Exception as e:
            print(f"[ScreenCaptureEngine Error] {e}")
            return None

    def save_test_image(self, filename="test_capture.jpg"):
        data = self.capture_screen_bytes()
        if data:
            with open(filename, "wb") as f:
                f.write(data)
            print(f"✅ Saved screenshot to {filename} ({len(data) // 1024} KB)")
            return True
        return False

if __name__ == "__main__":
    cap = ScreenCaptureEngine()
    cap.save_test_image()
