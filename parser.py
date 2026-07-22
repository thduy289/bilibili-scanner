import html
import re
from bs4 import BeautifulSoup

def parse_media_info(html_content: str, media_id: int) -> dict | None:
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, "lxml")
    result = None

    try:
        # 1. Bỏ qua nếu là trang lỗi hoặc bị gỡ
        if soup.select_one(".bstar-error"):
            print(f"  [ID {media_id}] ❌ Trang báo lỗi")
            return None

        if "This video has been removed" in html_content or "Video này đã bị gỡ" in html_content:
            print(f"  [ID {media_id}] ❌ Video đã bị gỡ")
            return None

        # 2. Tìm Region
        region = ""
        labels = soup.select(".detail-table__label")
        for label in labels:
            label_text = label.get_text(strip=True).lower()
            if "region" in label_text:
                next_elem = label.find_next_sibling()
                if next_elem:
                    region = next_elem.get_text(strip=True)
                break

        if not region:
            reg_match = re.search(r'Region["\s:]+([^"<&\n]+)', html_content, re.IGNORECASE)
            if reg_match:
                region = reg_match.group(1).strip()

        if not region:
            print(f"  [ID {media_id}] ⚠️ Không tìm thấy Region")
            return None

        if "japan" not in region.lower():
            print(f"  [ID {media_id}] ⏭️ Bỏ qua vì Region là '{region}'")
            return None

        # 3. Tìm Title
        title_elem = soup.select_one(".detail-header__title")
        title = title_elem.get_text(strip=True) if title_elem else f"Media {media_id}"
        title = html.unescape(title)

        # 4. Tìm Image Cover
        img_elem = soup.select_one(".media-info__cover img")
        image_url = ""
        if img_elem:
            image_url = img_elem.get("src") or img_elem.get("data-src") or ""

        url = f"https://www.bilibili.tv/en/media/{media_id}"

        print(f"  [ID {media_id}] 🎉 TÌM THẤY PHIM JAPAN: '{title}'")

        result = {
            "id": media_id,
            "title": title,
            "image": image_url,
            "url": url
        }
    finally:
        # GIẢI PHÓNG RAM NGAY LẬP TỨC: Tiêu hủy cây DOM của BeautifulSoup
        soup.decompose()

    return result