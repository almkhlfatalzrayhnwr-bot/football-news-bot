#!/usr/bin/env python3
"""
بوت مراقبة أخبار كرة القدم - نسخة GitHub Actions (بدون أي جهاز شخصي)
======================================================================
المصدر: RSS Feeds (مجانية)
التصنيف: كلمات مفتاحية (بدون AI، مجاني وحتمي 100%)
توليد المقالات: Google Gemini API (مجاني - نموذج Flash)
الإشعار: Telegram Bot API (مجاني) — صورة + مقال كامل للقناة، إشعار مختصر للشات الشخصي
التخزين: ملف JSON داخل المستودع نفسه (يُحدَّث ويُحفظ عبر git commit تلقائي)
"""

import json
import os
import sys
import time
import logging
import feedparser
import requests

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=%D8%A3%D8%AE%D8%A8%D8%A7%D8%B1%20%D9%83%D8%B1%D8%A9%20%D8%A7%D9%84%D9%82%D8%AF%D9%85%20when:1d&hl=ar&gl=EG&ceid=EG:ar",
    "https://news.google.com/rss/search?q=%D8%A7%D9%84%D9%85%D9%86%D8%AA%D8%AE%D8%A8%D8%A7%D8%AA%20%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9%20%D9%83%D8%B1%D8%A9%20%D8%A7%D9%84%D9%82%D8%AF%D9%85%20when:1d&hl=ar&gl=EG&ceid=EG:ar",
]

STATE_FILE = "processed_articles.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
MAX_ARTICLES_PER_RUN = 4
TELEGRAM_MAX_LEN = 4000

CATEGORY_RULES = {
    "نتيجة": ["فاز", "خسر", "تعادل", "هدف", "نتيجة", "beat", "win", "draw", "score", "result"],
    "انتقالات": ["انتقال", "صفقة", "تعاقد", "يوقع", "ينتقل", "transfer", "signing", "sign for", "deal"],
    "إصابة": ["إصابة", "يغيب", "غاب", "injury", "injured", "out for", "sidelined"],
}
DEFAULT_CATEGORY = "أخرى"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("football_monitor")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"تعذّرت قراءة ملف الحالة، سيُبدأ من جديد: {e}")
    return {"processed_links": []}


def save_state(state):
    state["processed_links"] = state["processed_links"][-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_new_articles(processed_links):
    new_articles = []
    processed_set = set(processed_links)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballNewsBot/1.0)"}

    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)

            if parsed.bozo:
                log.warning(f"تحذير عند قراءة {feed_url}: {parsed.bozo_exception}")
            for entry in parsed.entries:
                link = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                if link and link not in processed_set:
                    new_articles.append({"link": link, "title": title, "summary": summary})
        except requests.exceptions.RequestException as e:
            log.error(f"فشل جلب {feed_url} (مهلة/اتصال): {e}")
        except Exception as e:
            log.error(f"فشل جلب {feed_url}: {e}")

    return new_articles


def classify(title, summary):
    text = f"{title} {summary}".lower()
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw.lower() in text:
                return category
    return DEFAULT_CATEGORY


def fetch_article_image(link):
    """يفتح صفحة المقال الأصلية ويستخرج صورته الرئيسية من og:image (أو twitter:image احتياطياً)."""
    import re
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballNewsBot/1.0)"}
        resp = requests.get(link, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if not resp.ok:
            return None

        html = resp.text[:200000]

        for prop in ["og:image", "twitter:image"]:
            match = re.search(
                rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']',
                    html, re.IGNORECASE,
                )
            if match:
                image_url = match.group(1)
                if image_url.startswith("http"):
                    return image_url
        return None
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None


def escape_html(text):
    """يهرّب رموز HTML الخاصة (& < >) عشان تليجرام يقبل النص بصيغة HTML بأمان."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_html(text):
    """يشيل أكواد HTML الخام اللي أحياناً بتوصل جوه حقل summary من Google News RSS."""
    import re
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def strip_markdown(text):
    """يشيل رموز Markdown الشائعة اللي أحياناً بيرجعها Gemini رغم التعليمات."""
    for ch in ["**", "__", "##", "###", "`"]:
        text = text.replace(ch, "")
    lines = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("* ") or stripped.startswith("- "):
            stripped = stripped[2:]
        lines.append(stripped if stripped != line.lstrip() else line)
    return "\n".join(lines).replace("*", "").strip()


GEMINI_MIN_INTERVAL = 12  # ثانية بين كل استدعاء وآخر، هامش أمان أكبر تحت حد 10 طلبات/دقيقة
_last_gemini_call_time = [0]


def _wait_for_gemini_rate_limit():
    elapsed = time.time() - _last_gemini_call_time[0]
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)
    _last_gemini_call_time[0] = time.time()


def generate_article(title, summary, category):
    """
    يبعت العنوان والملخص لـ Gemini ويرجّع مقال عربي مُحرَّر بأسلوب صحفي.
    عند أي فشل، يرجع نسخة احتياطية بسيطة من العنوان + الملخص.
    """
    summary = strip_html(summary)

    title_core = title.split(" - ")[0].strip()
    if summary.strip().startswith(title_core) or title_core in summary[:len(title_core) + 20]:
        fallback_text = title.strip()
    else:
        fallback_text = f"{title}\n\n{summary}".strip()

    if not GEMINI_API_KEY:
        log.warning("لم يتم ضبط GEMINI_API_KEY — سيُستخدم نص مختصر بدل المقال الكامل")
        return fallback_text

    prompt = (
        "أنت محرر أخبار رياضية محترف. اكتب مقالاً إخبارياً كاملاً بالعربية الفصحى، "
        "لا يقل عن 120 كلمة ولا يزيد عن 200 كلمة، عن الخبر التالي بناءً على العنوان "
        "والملخص المتاحين. وسّع في الصياغة الصحفية (سياق، أهمية الخبر، تفاصيل منطقية "
        "مبنية على المعطاة) بدون اختراع أرقام أو تصريحات أو تفاصيل غير موجودة في المصدر.\n\n"
        f"التصنيف: {category}\n"
        f"العنوان الأصلي: {title}\n"
        f"الملخص: {summary}\n\n"
        "قواعد صارمة للمخرجات:\n"
        "- ابدأ بعنوان جذاب في سطر منفصل، ثم سطر فارغ، ثم نص المقال كاملاً.\n"
        "- ممنوع استخدام أي رموز تنسيق Markdown نهائياً (لا نجوم **، لا شرطات -، لا عناوين #).\n"
        "- اكتب نص عادي فقط بدون أي رموز زخرفية.\n"
        "- لا تكتب أي مقدمات مثل 'بالتأكيد' أو 'إليك المقال' أو 'هذا مقال عن'.\n"
        "- لا تقتطع الجملة الأولى؛ ابدأ العنوان من أول كلمة كاملة."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _wait_for_gemini_rate_limit()
            resp = requests.post(GEMINI_URL, json=payload, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 429:
                log.warning(f"Gemini: تجاوز حد الاستخدام (429)، محاولة {attempt}/{MAX_RETRIES}")
                time.sleep(attempt * 5)
                continue

            if not resp.ok:
                log.warning(f"Gemini فشل (محاولة {attempt}/{MAX_RETRIES}): {resp.status_code} - {resp.text[:300]}")
                time.sleep(attempt * 2)
                continue

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                log.warning(f"Gemini رجع بدون نتائج: {data}")
                continue

            parts = candidates[0].get("content", {}).get("parts", [])
            article_text = "".join(p.get("text", "") for p in parts).strip()
            finish_reason = candidates[0].get("finishReason", "")

            if finish_reason == "MAX_TOKENS" and len(article_text) < 200:
                log.warning(f"Gemini قطع الرد قبل اكتماله (MAX_TOKENS)، محاولة {attempt}/{MAX_RETRIES}")
                continue

            if article_text:
                return strip_markdown(article_text)[:TELEGRAM_MAX_LEN]

        except requests.exceptions.RequestException as e:
            log.warning(f"محاولة {attempt}/{MAX_RETRIES} فشلت في الاتصال بـ Gemini: {e}")
            time.sleep(attempt * 2)
        except (ValueError, KeyError) as e:
            log.warning(f"تعذّر تحليل رد Gemini: {e}")

    log.error(f"فشل توليد المقال نهائياً عبر Gemini، استخدام النسخة المختصرة: {title}")
    return fallback_text


TELEGRAM_CAPTION_MAX_LEN = 1000


def send_telegram_photo(image_url, caption, chat_id, parse_mode=None):
    """يرسل صورة مع نص مرفق (caption) لأي chat_id. يرجع True/False حسب النجاح."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": chat_id, "photo": image_url, "caption": caption}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                log.warning(f"Telegram طلب الانتظار {retry_after} ثانية (rate limit)")
                time.sleep(retry_after + 1)
                continue

            if not resp.ok:
                log.warning(
                    f"إرسال الصورة فشل (محاولة {attempt}/{MAX_RETRIES}, chat_id={chat_id}): "
                    f"{resp.status_code} - {resp.text}"
                )
                time.sleep(attempt * 2)
                continue

            return True
        except requests.exceptions.RequestException as e:
            log.warning(f"محاولة {attempt}/{MAX_RETRIES} فشلت في إرسال الصورة: {e}")
            time.sleep(attempt * 2)

    return False


def send_telegram_notification(text, chat_id, parse_mode=None):
    """يرسل نص جاهز لأي chat_id (شات شخصي أو قناة). parse_mode='HTML' لدعم الروابط القصيرة."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        log.warning("لم يتم ضبط TELEGRAM_BOT_TOKEN أو chat_id — تخطي الإشعار")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
        payload["disable_web_page_preview"] = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                log.warning(f"Telegram طلب الانتظار {retry_after} ثانية (rate limit)")
                time.sleep(retry_after + 1)
                continue

            if not resp.ok:
                log.warning(
                    f"محاولة {attempt}/{MAX_RETRIES} فشلت (chat_id={chat_id}): "
                    f"{resp.status_code} - {resp.text}"
                )
                time.sleep(attempt * 2)
                continue

            return True
        except requests.exceptions.RequestException as e:
            log.warning(f"محاولة {attempt}/{MAX_RETRIES} فشلت في إرسال إشعار Telegram: {e}")
            time.sleep(attempt * 2)

    log.error(f"فشل إرسال الإشعار نهائياً (chat_id={chat_id})")
    return False


def main():
    log.info("بدء دورة فحص جديدة")
    state = load_state()

    new_articles = fetch_new_articles(state["processed_links"])
    log.info(f"عدد المقالات الجديدة: {len(new_articles)}")

    articles_to_send = new_articles[:MAX_ARTICLES_PER_RUN]
    if len(new_articles) > MAX_ARTICLES_PER_RUN:
        log.info(
            f"تم تقييد الإرسال إلى {MAX_ARTICLES_PER_RUN} مقال في هذه الدورة؛ "
            f"الباقي ({len(new_articles) - MAX_ARTICLES_PER_RUN}) سيُرسل في الدورات القادمة"
        )

    for i, article in enumerate(articles_to_send):
        category = classify(article["title"], article["summary"])

        full_article = generate_article(article["title"], article["summary"], category)
        channel_text_html = (
            f"⚽ {escape_html(category)}\n\n"
            f"{escape_html(full_article)}\n\n"
            f'<a href="{article["link"]}">🔗 اقرأ المزيد</a>'
        )
        channel_text_html = channel_text_html[:TELEGRAM_MAX_LEN]

        personal_text = f"⚽ التصنيف: {category}\n\n{article['title']}\n\n{article['link']}"

        sent = send_telegram_notification(personal_text, TELEGRAM_CHAT_ID)

        if TELEGRAM_CHANNEL_ID:
            image_url = fetch_article_image(article["link"])
            link_html = f'<a href="{article["link"]}">🔗 اقرأ المزيد</a>'

            if image_url:
                caption_html = f"⚽ {escape_html(category)}\n\n{escape_html(full_article)}\n\n{link_html}"
                if len(caption_html) > TELEGRAM_CAPTION_MAX_LEN:
                    short_caption = f"⚽ {escape_html(category)}\n\n{escape_html(full_article)}"
                    short_caption = short_caption[:TELEGRAM_CAPTION_MAX_LEN - len(link_html) - 5]
                    caption_html = f"{short_caption}...\n\n{link_html}"

                photo_sent = send_telegram_photo(image_url, caption_html, TELEGRAM_CHANNEL_ID, parse_mode="HTML")
                if not photo_sent:
                    send_telegram_notification(channel_text_html, TELEGRAM_CHANNEL_ID, parse_mode="HTML")
            else:
                send_telegram_notification(channel_text_html, TELEGRAM_CHANNEL_ID, parse_mode="HTML")

        state["processed_links"].append(article["link"])

        log.info(
            f"مقال: '{article['title'][:60]}...' | التصنيف: {category} | "
            f"الإشعار: {'أُرسل' if sent else 'فشل'}"
        )

        if i < len(articles_to_send) - 1:
            time.sleep(1.2)

    save_state(state)
    log.info("انتهت دورة الفحص")


if __name__ == "__main__":
    main()
