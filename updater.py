import requests
from version import APP_VERSION, DEFAULT_GITHUB_REPO


def _parse_version(v_str: str) -> tuple[int, ...]:
    cleaned = v_str.strip().lstrip("vV")
    parts = []
    for p in cleaned.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def check_for_updates(github_repo: str = DEFAULT_GITHUB_REPO) -> dict:
    """
    Checks GitHub Releases API for the latest version.
    Returns dict with keys:
      - has_update (bool)
      - current_version (str)
      - latest_version (str)
      - release_url (str)
      - notes (str)
      - error (str)
    """
    repo = (github_repo or DEFAULT_GITHUB_REPO).strip().strip("/")
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        res = requests.get(
            api_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PoE-Jarvis-Companion"},
            timeout=5,
        )
        if res.status_code == 404:
            return {
                "has_update": False,
                "current_version": APP_VERSION,
                "latest_version": APP_VERSION,
                "release_url": f"https://github.com/{repo}/releases",
                "notes": "ยังไม่มี Release ใหม่บน GitHub (คุณกำลังใช้เวอร์ชันล่าสุดแล้ว)",
                "error": "",
            }
        res.raise_for_status()
        data = res.json()
        latest_tag = data.get("tag_name", APP_VERSION).lstrip("vV")
        html_url = data.get("html_url", f"https://github.com/{repo}/releases")
        body = data.get("body", "") or "อัปเดตประสิทธิภาพและฐานข้อมูลใหม่"

        is_newer = _parse_version(latest_tag) > _parse_version(APP_VERSION)
        return {
            "has_update": is_newer,
            "current_version": APP_VERSION,
            "latest_version": latest_tag,
            "release_url": html_url,
            "notes": body,
            "error": "",
        }
    except Exception as e:
        return {
            "has_update": False,
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "release_url": f"https://github.com/{repo}/releases",
            "notes": "",
            "error": str(e),
        }
