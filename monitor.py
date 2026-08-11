#!/usr/bin/env python3
"""
بوت مراقبة أخبار كرة القدم - نسخة GitHub Actions (بدون أي جهاز شخصي)
======================================================================
المصدر: RSS Feeds (مجانية)
التصنيف: كلمات مفتاحية (بدون AI، مجاني وحتمي 100%)
الإشعار: Telegram Bot API (مجاني) — يُرسل للشات الشخصي وللقناة معاً
التخزين: ملف JSON داخل المستودع نفسه (يُحدَّث ويُحفظ عبر git commit تلقائي)
"""

import json
import os
import sys
import time
import logging
import feedparser
import requests

# ============================================================
# الإعدادات
# ============================================================

RSS_FEEDS = [
    # مصادر عربية بالكامل عبر Google News RSS (يجمع من عشرات المواقع:
    # كووورة، يلاكورة، الجزيرة، أون تايم سبورت، إلخ). أوثق من الاعتماد
    # على موقع عربي واحد لأن مواقع مثل FilGoal تحظر الطلبات الآلية
    # (403 Forbidden) بينما Google News لا يحظرها.
    # "when:1d" يقصر النتائج على آخر 24 ساعة فقط (يمنع ظهور أخبار قديمة).
    "https://news.google.com/rss/search?q=%D8%A3%D8%AE%D8%A8%D8%A7%D8%B1%20%D9%83%D8%B1%D8%A9%20%D8%A7%D9%84%D9%82%D8%AF%D9%85%20when:1d&hl=ar&gl=EG&ceid=EG:ar",
    "https://news.google.com/rss/search?q=%D8%A7%D9%84%D9%85%D9%86%D8%AA%D8%AE%D8%A8%D8%A7%D8%AA%20%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9%20%D9%83%D8%B1%D8%A9%20%D8%A7%D9%84%D9%82%D8%AF%D9%85%20when:1d&hl=ar&gl=EG&ceid=EG:ar",
]

STATE_FILE = "processed_articles.json"

# التوكن والـ chat id يُقرآن من متغيرات البيئة (GitHub Secrets) وليس من الكود مباشرة
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
MAX_ARTICLES_PER_RUN = 12  # يمنع تجاوز حد سرعة Telegram ومهلة الـ workflow

# قواعد التصنيف بالكلمات المفتاحية (يمكن التوسع فيها بسهولة)
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


# ============================================================
# التخزين (state) — ملف JSON
# ============================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"تعذّرت قراءة ملف الحالة، سيُبدأ من جديد: {e}")
    return {"processed_links": []}


def save_state(state):
    # نحتفظ فقط بآخر 500 رابط لمنع نمو الملف بلا حدود
    state["processed_links"] = state["processed_links"][-500:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# جلب المقالات الجديدة
# ============================================================

def fetch_new_articles(processed_links):
    new_articles = []
    processed_set = set(processed_links)

    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootballNewsBot/1.0)"}

    for feed_url in RSS_FEEDS:
        try:
            # نستخدم requests مع مهلة زمنية صريحة بدل تمرير الرابط مباشرة
            # لـ feedparser، لأن feedparser.parse(url) لا يملك مهلة افتراضية
            # وقد يعلّق التنفيذ لوقت طويل إذا لم يرد الخادم.
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


# ============================================================
# التصنيف بالكلمات المفتاحية (بدون AI — حتمي وسريع ومجاني)
# ============================================================

def classify(title, summary):
    text = f"{title} {summary}".lower()
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw.lower() in text:
                return category
    return DEFAULT_CATEGORY


# ============================================================
# إشعار Telegram
# ============================================================

def send_telegram_notification(title, link, category, chat_id):
    """يرسل رسالة تليجرام لأي chat_id (شات شخصي أو قناة)."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        log.warning("لم يتم ضبط TELEGRAM_BOT_TOKEN أو chat_id — تخطي الإشعار")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    text = f"⚽ التصنيف: {category}\n\n{title}\n\n{link}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                # تجاوزنا حد سرعة Telegram — نحترم القيمة اللي بيطلبها هو بالظبط
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                log.warning(f"Telegram طلب الانتظار {retry_after} ثانية (rate limit)")
                time.sleep(retry_after + 1)
                continue
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            log.warning(f"محاولة {attempt}/{MAX_RETRIES} فشلت في إرسال إشعار Telegram: {e}")
            time.sleep(attempt * 2)

    log.error(f"فشل إرسال الإشعار نهائياً للمقال: {title} (chat_id={chat_id})")
    return False


# ============================================================
# التشغيل الرئيسي
# ============================================================

def main():
    log.info("بدء دورة فحص جديدة")
    state = load_state()

    new_articles = fetch_new_articles(state["processed_links"])
    log.info(f"عدد المقالات الجديدة: {len(new_articles)}")

    # نحد عدد الأخبار المُرسلة في التشغيلة الواحدة، عشان ما نضربش حد سرعة
    # Telegram ولا نتخطى مهلة الـ workflow. أي أخبار زيادة عن الحد هتتبعت
    # تلقائياً في التشغيلة الجاية (بعد 5 دقايق) لأنها هتفضل غير معالَجة.
    articles_to_send = new_articles[:MAX_ARTICLES_PER_RUN]
    if len(new_articles) > MAX_ARTICLES_PER_RUN:
        log.info(
            f"تم تقييد الإرسال إلى {MAX_ARTICLES_PER_RUN} مقال في هذه الدورة؛ "
            f"الباقي ({len(new_articles) - MAX_ARTICLES_PER_RUN}) سيُرسل في الدورات القادمة"
        )

    for i, article in enumerate(articles_to_send):
        category = classify(article["title"], article["summary"])

        # إرسال للشات الشخصي (للمتابعة والتجربة)
        sent = send_telegram_notification(article["title"], article["link"], category, TELEGRAM_CHAT_ID)

        # إرسال لقناة تليجرام العامة (المنتج النهائي للجمهور)
        if TELEGRAM_CHANNEL_ID:
            send_telegram_notification(article["title"], article["link"], category, TELEGRAM_CHANNEL_ID)

        state["processed_links"].append(article["link"])

        log.info(
            f"مقال: '{article['title'][:60]}...' | التصنيف: {category} | "
            f"الإشعار: {'أُرسل' if sent else 'فشل'}"
        )

        # تهدئة بسيطة بين كل رسالة والتانية لاحترام حد سرعة Telegram
        # (Telegram يسمح تقريبًا برسالة واحدة كل ثانية لنفس المحادثة)
        if i < len(articles_to_send) - 1:
            time.sleep(1.2)

    save_state(state)
    log.info("انتهت دورة الفحص")


if __name__ == "__main__":
    main()
    
