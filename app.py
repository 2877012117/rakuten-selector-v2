import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st


APP_TITLE = "乐天市场本地选品工具 V2 云部署版"
DEFAULT_PROXY_URL = ""
DEFAULT_DB_PATH = "rakuten_selector_v2.sqlite3"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

st.set_page_config(page_title=APP_TITLE, page_icon="🛒", layout="wide")


def get_secret(key: str, default: str = "") -> str:
    try:
        value = st.secrets.get(key, default)
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def to_number(value: Any, default: float = 0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def clean_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_first_image(item: Dict[str, Any]) -> str:
    for key in ["mediumImageUrls", "smallImageUrls"]:
        urls = item.get(key, [])
        if isinstance(urls, list) and urls:
            first = urls[0]
            if isinstance(first, dict):
                return first.get("imageUrl", "") or first.get("url", "")
            if isinstance(first, str):
                return first
    return ""


def normalize_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("items") or data.get("Items") or []
    normalized: List[Dict[str, Any]] = []
    for x in items:
        if isinstance(x, dict) and "Item" in x and isinstance(x["Item"], dict):
            normalized.append(x["Item"])
        elif isinstance(x, dict):
            normalized.append(x)
    return normalized


def text_to_words(text: str, min_len: int = 2) -> List[str]:
    text = clean_text(text)
    if not text:
        return []

    text = re.sub(
        r"[\[\]【】()（）{}<>《》『』「」｜|/\\,，.。;；:：!！?？+＋・★☆●○◎※♪#＃\-_=~〜～]",
        " ",
        text,
    )
    parts = re.split(r"\s+", text)
    stopwords = {
        "送料無料", "送料", "無料", "税込", "楽天", "市場", "商品", "販売", "人気", "おすすめ",
        "ランキング", "レビュー", "ポイント", "クーポン", "セール", "価格", "限定", "あり", "なし",
        "対応", "可能", "用", "の", "に", "を", "が", "と", "で", "から", "です", "ます", "する",
        "した", "して", "ため", "こちら", "これ", "それ", "あす楽",
    }

    words: List[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < min_len:
            continue
        if p in stopwords:
            continue
        if re.fullmatch(r"\d+", p):
            continue
        words.append(p)
    return words


def extract_keywords_from_rows(rows: List[Dict[str, Any]], top_n: int = 40) -> List[Tuple[str, int]]:
    counter: Counter = Counter()
    for row in rows:
        text = " ".join([
            clean_text(row.get("商品名", "")),
            clean_text(row.get("キャッチコピー", "")),
            clean_text(row.get("商品说明", ""))[:800],
        ])
        counter.update(text_to_words(text))
    return counter.most_common(top_n)


def calc_score(row: Dict[str, Any]) -> float:
    score = 0.0
    review_count = to_number(row.get("レビュー数", 0))
    review_avg = to_number(row.get("レビュー评分", 0))
    price = to_number(row.get("价格", 0))
    postage = row.get("送料無料", "")
    availability = row.get("库存状态", "")
    image_url = row.get("图片", "")
    point_rate = to_number(row.get("ポイント倍率", 0))

    score += min(review_count / 300 * 25, 25)

    if review_avg >= 4.7:
        score += 20
    elif review_avg >= 4.5:
        score += 17
    elif review_avg >= 4.2:
        score += 13
    elif review_avg >= 4.0:
        score += 9
    elif review_avg > 0:
        score += 5

    if 1980 <= price <= 6980:
        score += 20
    elif 1000 <= price <= 9980:
        score += 12
    elif price > 0:
        score += 6

    if postage == "是":
        score += 10
    if availability == "有库存":
        score += 10
    if image_url:
        score += 10
    if point_rate >= 5:
        score += 10
    elif point_rate >= 2:
        score += 5
    return round(score, 1)


def convert_items_to_df(items: List[Dict[str, Any]], source_keyword: str = "") -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        image_url = get_first_image(item)
        row = {
            "排名位置": index,
            "来源关键词": source_keyword,
            "商品名": clean_text(item.get("itemName", "")),
            "キャッチコピー": clean_text(item.get("catchcopy", "")),
            "价格": safe_int(item.get("itemPrice", 0)),
            "レビュー数": safe_int(item.get("reviewCount", 0)),
            "レビュー评分": to_number(item.get("reviewAverage", 0)),
            "送料無料": "是" if safe_int(item.get("postageFlag", 0)) == 1 else "否",
            "库存状态": "有库存" if safe_int(item.get("availability", 0)) == 1 else "无库存",
            "ポイント倍率": to_number(item.get("pointRate", 1)),
            "店铺名": clean_text(item.get("shopName", "")),
            "shopCode": item.get("shopCode", ""),
            "genreId": item.get("genreId", ""),
            "itemCode": item.get("itemCode", ""),
            "商品URL": item.get("itemUrl", ""),
            "affiliateUrl": item.get("affiliateUrl", ""),
            "图片": image_url,
            "商品说明": clean_text(item.get("itemCaption", "")),
            "抓取时间": now_str(),
        }
        row["选品分"] = calc_score(row)
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(by="选品分", ascending=False).reset_index(drop=True)
    return df


def make_excel_download(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="items")
    return output.getvalue()


# -----------------------------
# SQLite
# -----------------------------
def get_conn(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_code TEXT UNIQUE,
            item_name TEXT,
            catchcopy TEXT,
            price INTEGER,
            review_count INTEGER,
            review_average REAL,
            postage TEXT,
            availability TEXT,
            point_rate REAL,
            shop_name TEXT,
            shop_code TEXT,
            genre_id TEXT,
            item_url TEXT,
            image_url TEXT,
            item_caption TEXT,
            source_keyword TEXT,
            score REAL,
            note TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS own_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            selling_price INTEGER,
            cost_price INTEGER,
            core_selling_points TEXT,
            material TEXT,
            target_user TEXT,
            size_color TEXT,
            keywords TEXT,
            image_url TEXT,
            product_url TEXT,
            note TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT,
            snapshot_time TEXT,
            keyword TEXT,
            item_code TEXT,
            item_name TEXT,
            price INTEGER,
            review_count INTEGER,
            review_average REAL,
            rank_position INTEGER,
            score REAL,
            shop_name TEXT,
            genre_id TEXT,
            item_url TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            genre_id TEXT,
            total_count INTEGER,
            result_count INTEGER,
            avg_price REAL,
            avg_review_count REAL,
            avg_review_average REAL,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


def save_favorite(row: Dict[str, Any], note: str = "") -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO favorites (
            item_code, item_name, catchcopy, price, review_count, review_average,
            postage, availability, point_rate, shop_name, shop_code, genre_id,
            item_url, image_url, item_caption, source_keyword, score, note,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_code) DO UPDATE SET
            item_name=excluded.item_name,
            catchcopy=excluded.catchcopy,
            price=excluded.price,
            review_count=excluded.review_count,
            review_average=excluded.review_average,
            postage=excluded.postage,
            availability=excluded.availability,
            point_rate=excluded.point_rate,
            shop_name=excluded.shop_name,
            shop_code=excluded.shop_code,
            genre_id=excluded.genre_id,
            item_url=excluded.item_url,
            image_url=excluded.image_url,
            item_caption=excluded.item_caption,
            source_keyword=excluded.source_keyword,
            score=excluded.score,
            note=CASE WHEN excluded.note != '' THEN excluded.note ELSE favorites.note END,
            updated_at=excluded.updated_at
        """,
        (
            row.get("itemCode", "") or row.get("item_code", "") or row.get("商品URL", ""),
            row.get("商品名", ""),
            row.get("キャッチコピー", ""),
            safe_int(row.get("价格", 0)),
            safe_int(row.get("レビュー数", 0)),
            to_number(row.get("レビュー评分", 0)),
            row.get("送料無料", ""),
            row.get("库存状态", ""),
            to_number(row.get("ポイント倍率", 0)),
            row.get("店铺名", ""),
            row.get("shopCode", ""),
            row.get("genreId", ""),
            row.get("商品URL", ""),
            row.get("图片", ""),
            row.get("商品说明", ""),
            row.get("来源关键词", ""),
            to_number(row.get("选品分", 0)),
            note,
            now_str(),
            now_str(),
        ),
    )
    conn.commit()
    conn.close()


def delete_favorite(fav_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM favorites WHERE id=?", (fav_id,))
    conn.commit()
    conn.close()


def load_favorites() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM favorites ORDER BY updated_at DESC", conn)
    conn.close()
    return df


def save_own_product(data: Dict[str, Any], product_id: Optional[int] = None) -> None:
    conn = get_conn()
    cur = conn.cursor()
    if product_id:
        cur.execute(
            """
            UPDATE own_products SET
                product_name=?, selling_price=?, cost_price=?, core_selling_points=?,
                material=?, target_user=?, size_color=?, keywords=?, image_url=?,
                product_url=?, note=?, updated_at=?
            WHERE id=?
            """,
            (
                data.get("product_name", ""),
                safe_int(data.get("selling_price", 0)),
                safe_int(data.get("cost_price", 0)),
                data.get("core_selling_points", ""),
                data.get("material", ""),
                data.get("target_user", ""),
                data.get("size_color", ""),
                data.get("keywords", ""),
                data.get("image_url", ""),
                data.get("product_url", ""),
                data.get("note", ""),
                now_str(),
                product_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO own_products (
                product_name, selling_price, cost_price, core_selling_points,
                material, target_user, size_color, keywords, image_url,
                product_url, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("product_name", ""),
                safe_int(data.get("selling_price", 0)),
                safe_int(data.get("cost_price", 0)),
                data.get("core_selling_points", ""),
                data.get("material", ""),
                data.get("target_user", ""),
                data.get("size_color", ""),
                data.get("keywords", ""),
                data.get("image_url", ""),
                data.get("product_url", ""),
                data.get("note", ""),
                now_str(),
                now_str(),
            ),
        )
    conn.commit()
    conn.close()


def delete_own_product(product_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM own_products WHERE id=?", (product_id,))
    conn.commit()
    conn.close()


def load_own_products() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM own_products ORDER BY updated_at DESC", conn)
    conn.close()
    return df


def save_snapshots(df: pd.DataFrame, keyword: str, total_count: int = 0, genre_id: str = "") -> None:
    if df is None or df.empty:
        return
    conn = get_conn()
    cur = conn.cursor()
    snapshot_date = today_str()
    snapshot_time = now_str()
    for row in df_to_records(df):
        cur.execute(
            """
            INSERT INTO search_snapshots (
                snapshot_date, snapshot_time, keyword, item_code, item_name,
                price, review_count, review_average, rank_position, score,
                shop_name, genre_id, item_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                snapshot_time,
                keyword,
                row.get("itemCode", ""),
                row.get("商品名", ""),
                safe_int(row.get("价格", 0)),
                safe_int(row.get("レビュー数", 0)),
                to_number(row.get("レビュー评分", 0)),
                safe_int(row.get("排名位置", 0)),
                to_number(row.get("选品分", 0)),
                row.get("店铺名", ""),
                row.get("genreId", ""),
                row.get("商品URL", ""),
                now_str(),
            ),
        )

    avg_price = float(df["价格"].mean()) if "价格" in df.columns else 0
    avg_review_count = float(df["レビュー数"].mean()) if "レビュー数" in df.columns else 0
    avg_review_average = float(df["レビュー评分"].mean()) if "レビュー评分" in df.columns else 0
    cur.execute(
        """
        INSERT INTO search_history (
            keyword, genre_id, total_count, result_count, avg_price,
            avg_review_count, avg_review_average, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (keyword, genre_id, total_count, len(df), avg_price, avg_review_count, avg_review_average, now_str()),
    )
    conn.commit()
    conn.close()


def load_search_history() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM search_history ORDER BY created_at DESC", conn)
    conn.close()
    return df


def load_snapshots(keyword: Optional[str] = None) -> pd.DataFrame:
    conn = get_conn()
    if keyword:
        df = pd.read_sql_query(
            "SELECT * FROM search_snapshots WHERE keyword=? ORDER BY snapshot_time DESC",
            conn,
            params=(keyword,),
        )
    else:
        df = pd.read_sql_query("SELECT * FROM search_snapshots ORDER BY snapshot_time DESC", conn)
    conn.close()
    return df


# -----------------------------
# Rakuten Proxy
# -----------------------------
def call_proxy_search(
    proxy_url: str,
    proxy_token: str,
    keyword: str,
    genre_id: str,
    min_price: int,
    max_price: int,
    hits: int,
    page: int,
    sort: str,
    postage_only: bool,
    has_review_only: bool,
    image_only: bool,
) -> Dict[str, Any]:
    proxy_url = proxy_url.strip()
    if not proxy_url:
        raise Exception("请填写代理地址")

    params: Dict[str, Any] = {
        "format": "json",
        "formatVersion": 2,
        "hits": hits,
        "page": page,
        "sort": sort,
        "availability": 1,
        "elements": ",".join([
            "itemName", "catchcopy", "itemPrice", "itemUrl", "affiliateUrl", "itemCode", "shopName",
            "shopCode", "reviewCount", "reviewAverage", "mediumImageUrls", "smallImageUrls", "postageFlag",
            "availability", "pointRate", "genreId", "itemCaption",
        ]),
    }
    if keyword.strip():
        params["keyword"] = keyword.strip()
    if genre_id.strip():
        params["genreId"] = genre_id.strip()
    if min_price > 0:
        params["minPrice"] = int(min_price)
    if max_price > 0:
        params["maxPrice"] = int(max_price)
    if postage_only:
        params["postageFlag"] = 1
    if has_review_only:
        params["hasReviewFlag"] = 1
    if image_only:
        params["imageFlag"] = 1

    headers = {"Content-Type": "application/json"}
    if proxy_token.strip():
        headers["X-Proxy-Token"] = proxy_token.strip()

    response = requests.post(proxy_url, json=params, headers=headers, timeout=40)
    if response.status_code != 200:
        raise Exception(f"代理/API错误：{response.status_code}\n{response.text}")
    try:
        data = response.json()
    except Exception:
        raise Exception(f"返回内容不是 JSON：\n{response.text[:1200]}")
    if data.get("ok") is False:
        raise Exception(data.get("error", "代理返回错误"))
    if "error" in data:
        raise Exception(f"{data.get('error')}: {data.get('error_description')}")
    if "errors" in data:
        raise Exception(str(data["errors"]))
    return data


def extract_item_code_from_rakuten_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if ":" in value and not value.startswith("http"):
        return value.strip().strip("/")
    m = re.search(r"item\.rakuten\.co\.jp/([^/?#]+)/([^/?#]+)", value)
    if m:
        shop_code = m.group(1).strip()
        item_id = m.group(2).strip()
        if shop_code and item_id:
            return f"{shop_code}:{item_id}"
    return ""


def call_proxy_item_lookup(proxy_url: str, proxy_token: str, item_code: str) -> Dict[str, Any]:
    proxy_url = proxy_url.strip()
    item_code = item_code.strip()
    if not proxy_url:
        raise Exception("请填写代理地址")
    if not item_code:
        raise Exception("没有解析到楽天 itemCode。请确认URL类似：https://item.rakuten.co.jp/shopcode/itemid/")

    params: Dict[str, Any] = {
        "format": "json",
        "formatVersion": 2,
        "itemCode": item_code,
        "hits": 1,
        "page": 1,
        "availability": 1,
        "elements": ",".join([
            "itemName", "catchcopy", "itemPrice", "itemUrl", "affiliateUrl", "itemCode", "shopName",
            "shopCode", "reviewCount", "reviewAverage", "mediumImageUrls", "smallImageUrls", "postageFlag",
            "availability", "pointRate", "genreId", "itemCaption",
        ]),
    }
    headers = {"Content-Type": "application/json"}
    if proxy_token.strip():
        headers["X-Proxy-Token"] = proxy_token.strip()
    response = requests.post(proxy_url, json=params, headers=headers, timeout=40)
    if response.status_code != 200:
        raise Exception(f"代理/API错误：{response.status_code}\n{response.text}")
    data = response.json()
    if data.get("ok") is False:
        raise Exception(data.get("error", "代理返回错误"))
    if "error" in data:
        raise Exception(f"{data.get('error')}: {data.get('error_description')}")
    if "errors" in data:
        raise Exception(str(data["errors"]))
    return data


def rakuten_item_to_own_product(item: Dict[str, Any]) -> Dict[str, Any]:
    image_url = get_first_image(item)
    item_name = clean_text(item.get("itemName", ""))
    catchcopy = clean_text(item.get("catchcopy", ""))
    caption = clean_text(item.get("itemCaption", ""))
    keyword_source = " ".join([item_name, catchcopy])
    words = [w for w, _ in Counter(text_to_words(keyword_source)).most_common(30)]

    note_text = (
        f"楽天から导入：{now_str()}\n"
        f"shopCode: {item.get('shopCode', '')}\n"
        f"itemCode: {item.get('itemCode', '')}\n"
        f"レビュー: {item.get('reviewCount', 0)} / {item.get('reviewAverage', 0)}\n\n"
        f"商品説明摘录：\n{caption[:800]}"
    )
    return {
        "product_name": item_name,
        "selling_price": safe_int(item.get("itemPrice", 0)),
        "cost_price": 0,
        "core_selling_points": catchcopy,
        "material": "",
        "target_user": "",
        "size_color": "",
        "keywords": " ".join(words),
        "image_url": image_url,
        "product_url": item.get("itemUrl", ""),
        "note": note_text,
    }


# -----------------------------
# AI
# -----------------------------
def ai_depth_config(depth: str) -> Dict[str, Any]:
    configs = {
        "快速分析": {"model": "deepseek-chat", "max_tokens": 1200, "temperature": 0.35, "hint": "请简洁直接，重点判断是否值得做。"},
        "标准分析": {"model": "deepseek-chat", "max_tokens": 2200, "temperature": 0.3, "hint": "请给出完整分析，覆盖市场、竞争、价格、卖点、关键词。"},
        "深度分析": {"model": "deepseek-reasoner", "max_tokens": 3500, "temperature": 0.25, "hint": "请进行较深入的选品推理，明确机会、风险、打法和优先级。"},
        "极深推理": {"model": "deepseek-reasoner", "max_tokens": 5000, "temperature": 0.2, "hint": "请像电商选品负责人一样，进行结构化深度分析，给出可执行决策。"},
    }
    return configs.get(depth, configs["标准分析"])


def call_deepseek(api_key: str, messages: List[Dict[str, str]], depth: str = "标准分析", model_override: Optional[str] = None) -> str:
    api_key = api_key.strip()
    if not api_key:
        raise Exception("请填写 DeepSeek API Key")
    cfg = ai_depth_config(depth)
    payload = {
        "model": model_override or cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    if response.status_code != 200:
        raise Exception(f"DeepSeek API错误：{response.status_code}\n{response.text}")
    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise Exception(f"DeepSeek 返回格式异常：{json.dumps(data, ensure_ascii=False)[:1200]}")


def summarize_market_df(df: pd.DataFrame, keyword: str = "") -> Dict[str, Any]:
    if df is None or df.empty:
        return {}
    rows = df_to_records(df)
    keywords = extract_keywords_from_rows(rows, top_n=50)
    return {
        "keyword": keyword,
        "result_count": len(df),
        "avg_price": float(df["价格"].mean()) if "价格" in df.columns else 0,
        "median_price": float(df["价格"].median()) if "价格" in df.columns else 0,
        "min_price": int(df["价格"].min()) if "价格" in df.columns else 0,
        "max_price": int(df["价格"].max()) if "价格" in df.columns else 0,
        "avg_review_count": float(df["レビュー数"].mean()) if "レビュー数" in df.columns else 0,
        "median_review_count": float(df["レビュー数"].median()) if "レビュー数" in df.columns else 0,
        "avg_review_average": float(df["レビュー评分"].mean()) if "レビュー评分" in df.columns else 0,
        "free_shipping_rate": float((df["送料無料"] == "是").mean()) if "送料無料" in df.columns else 0,
        "top_keywords": keywords[:30],
        "top_items": rows[:12],
    }


def build_ai_market_prompt(summary: Dict[str, Any], depth: str) -> List[Dict[str, str]]:
    cfg = ai_depth_config(depth)
    system = "你是日本楽天市場选品和关键词运营专家。请用中文分析，但关键词保持日语。输出要具体、可执行。"
    user = f"""
请基于以下楽天市場搜索结果摘要，进行选品分析。

分析深度要求：{cfg['hint']}

请按以下结构输出：
1. 结论：这个品是否值得做，用 1-5 星表示
2. 市场需求判断
3. 竞争强度判断
4. 价格带建议
5. レビュー门槛
6. 可切入差异点：至少 5 条
7. 标题关键词建议：核心词、属性词、场景词、人群词、长尾词
8. 主图卖点建议：适合楽天主图的 3-5 个日语短句
9. 风险提醒
10. 最终行动建议

数据摘要：
{json.dumps(summary, ensure_ascii=False, indent=2)[:18000]}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_ai_keyword_prompt(summary: Dict[str, Any], product_info: str = "") -> List[Dict[str, str]]:
    system = "你是日本楽天市場爆款关键词专家。请用中文解释分类，关键词本身使用自然日语。"
    user = f"""
请根据以下楽天竞品数据，为我生成爆款关键词建议。

我的商品信息：
{product_info or '未提供'}

请输出：
1. 核心大词 10 个
2. 高转化长尾词 20 个
3. 人群词
4. 场景词
5. 风格词
6. 功能/材质词
7. 季节词
8. RPP广告候选词
9. 楽天商品标题组合 3 组
10. 楽天关键词栏组合 3 组，每组控制在 147 个日文字符左右
11. 不建议使用/需要谨慎的词

竞品数据摘要：
{json.dumps(summary, ensure_ascii=False, indent=2)[:18000]}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_ai_compare_prompt(compare_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    system = "你是楽天市場竞品对比和差异化定位专家。请给出落地建议。"
    user = f"""
请对我的商品和收藏竞品做对比分析。

请输出：
1. 价格档位判断
2. 综合竞争力评分
3. 共同词/共同卖点说明
4. 竞品独有优势
5. 我方独有优势
6. 我方应该补充的关键词
7. 我方应该避开的红海表达
8. 差异化建议：标题、主图、详情页、价格、套装内容、售后风险
9. 最终改进优先级

对比数据：
{json.dumps(compare_payload, ensure_ascii=False, indent=2)[:20000]}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# -----------------------------
# Compare helpers
# -----------------------------
def own_product_to_text(row: pd.Series) -> str:
    parts = [row.get("product_name", ""), row.get("core_selling_points", ""), row.get("material", ""), row.get("target_user", ""), row.get("size_color", ""), row.get("keywords", ""), row.get("note", "")]
    return " ".join([clean_text(x) for x in parts if clean_text(x)])


def favorite_to_text(row: pd.Series) -> str:
    parts = [row.get("item_name", ""), row.get("catchcopy", ""), row.get("item_caption", ""), row.get("note", "")]
    return " ".join([clean_text(x) for x in parts if clean_text(x)])


def compare_words(own_text: str, competitor_texts: List[str]) -> Dict[str, Any]:
    own_words = set(text_to_words(own_text))
    comp_sets = [set(text_to_words(t)) for t in competitor_texts if t]
    if comp_sets:
        comp_union = set().union(*comp_sets)
        comp_intersection = set.intersection(*comp_sets) if len(comp_sets) > 1 else comp_sets[0]
    else:
        comp_union = set()
        comp_intersection = set()
    return {
        "共同词_我方与竞品交集": sorted(list(own_words & comp_union))[:80],
        "我方独有词": sorted(list(own_words - comp_union))[:80],
        "竞品独有词": sorted(list(comp_union - own_words))[:120],
        "竞品共同词": sorted(list(comp_intersection))[:80],
    }


def price_band(price: float) -> str:
    if price <= 0:
        return "未填写"
    if price < 1980:
        return "低价引流档"
    if price <= 3980:
        return "主流低中价档"
    if price <= 6980:
        return "主力利润档"
    if price <= 9980:
        return "高客单档"
    return "高价/礼品档"


def build_compare_payload(own_row: pd.Series, fav_df: pd.DataFrame) -> Dict[str, Any]:
    own_text = own_product_to_text(own_row)
    competitor_texts = [favorite_to_text(row) for _, row in fav_df.iterrows()]
    word_result = compare_words(own_text, competitor_texts)
    own_price = safe_int(own_row.get("selling_price", 0))
    comp_prices = fav_df["price"].dropna().astype(float).tolist() if not fav_df.empty else []
    comp_reviews = fav_df["review_count"].dropna().astype(float).tolist() if not fav_df.empty else []
    comp_scores = fav_df["score"].dropna().astype(float).tolist() if not fav_df.empty else []
    return {
        "my_product": {
            "name": own_row.get("product_name", ""),
            "selling_price": own_price,
            "price_band": price_band(own_price),
            "cost_price": safe_int(own_row.get("cost_price", 0)),
            "core_selling_points": own_row.get("core_selling_points", ""),
            "material": own_row.get("material", ""),
            "target_user": own_row.get("target_user", ""),
            "size_color": own_row.get("size_color", ""),
            "keywords": own_row.get("keywords", ""),
            "note": own_row.get("note", ""),
        },
        "competitor_summary": {
            "count": len(fav_df),
            "avg_price": sum(comp_prices) / len(comp_prices) if comp_prices else 0,
            "median_price": float(pd.Series(comp_prices).median()) if comp_prices else 0,
            "avg_review_count": sum(comp_reviews) / len(comp_reviews) if comp_reviews else 0,
            "avg_score": sum(comp_scores) / len(comp_scores) if comp_scores else 0,
        },
        "word_analysis": word_result,
        "competitors": fav_df.head(20).fillna("").to_dict(orient="records") if not fav_df.empty else [],
    }


# -----------------------------
# Sidebar
# -----------------------------
def sidebar_settings() -> Dict[str, Any]:
    with st.sidebar:
        st.title("🛒 Rakuten Selector V2 云部署版")
        page = st.radio(
            "功能菜单",
            ["商品搜索", "收藏商品", "自有商品", "对比分析", "AI选品分析", "AI爆款关键词", "楽天联想词", "趋势追踪"],
        )

        st.divider()
        st.header("云端配置状态")
        secret_proxy_url = get_secret("RAKUTEN_PROXY_URL")
        secret_proxy_token = get_secret("RAKUTEN_PROXY_TOKEN")
        secret_deepseek_key = get_secret("DEEPSEEK_API_KEY")

        if secret_proxy_url and secret_proxy_token:
            proxy_url = secret_proxy_url
            proxy_token = secret_proxy_token
            st.success("乐天代理已从 Secrets 读取")
        else:
            st.warning("未检测到乐天代理 Secrets，本地测试时请手动填写。")
            proxy_url = st.text_input("乐天代理地址，本地备用", value=st.session_state.get("proxy_url", DEFAULT_PROXY_URL))
            proxy_token = st.text_input("代理密码，本地备用", value=st.session_state.get("proxy_token", ""), type="password")

        st.session_state["proxy_url"] = proxy_url
        st.session_state["proxy_token"] = proxy_token

        st.divider()
        st.header("AI 设置")
        if secret_deepseek_key:
            deepseek_api_key = secret_deepseek_key
            st.success("DeepSeek API Key 已从 Secrets 读取")
            st.caption("公开部署且没有登录密码时，所有访问者都可以使用你的 DeepSeek 额度。")
        else:
            deepseek_api_key = st.text_input("DeepSeek API Key", value=st.session_state.get("deepseek_api_key", ""), type="password")
        st.session_state["deepseek_api_key"] = deepseek_api_key

        depth = st.selectbox("DeepSeek 分析深度", ["快速分析", "标准分析", "深度分析", "极深推理"], index=1)
        model_override = st.selectbox("模型选择", ["自动匹配深度", "deepseek-chat", "deepseek-reasoner"], index=0)
        selected_model = None if model_override == "自动匹配深度" else model_override
        st.divider()
        st.caption("数据库文件：" + str(Path(DEFAULT_DB_PATH).resolve()))
    return {"page": page, "proxy_url": proxy_url, "proxy_token": proxy_token, "deepseek_api_key": deepseek_api_key, "depth": depth, "selected_model": selected_model}


# -----------------------------
# Pages
# -----------------------------
def page_search(settings: Dict[str, Any]) -> None:
    st.title("🛒 商品搜索")
    st.caption("通过 Cloudflare Worker 代理调用楽天商品検索API，支持收藏、快照保存、Excel导出。")

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            keyword = st.text_input("关键词", value=st.session_state.get("last_keyword", "キッズ ダンス衣装"))
        with c2:
            genre_id = st.text_input("ジャンルID", value="")
        with c3:
            min_price = st.number_input("最低价格", min_value=0, value=0, step=100)
        with c4:
            max_price = st.number_input("最高价格", min_value=0, value=0, step=100)
        c5, c6, c7, c8 = st.columns([1, 1, 2, 2])
        with c5:
            hits = st.slider("每页数量", min_value=1, max_value=30, value=30)
        with c6:
            page = st.number_input("页码", min_value=1, max_value=100, value=1, step=1)
        with c7:
            sort_options = {
                "乐天标准": "standard", "レビュー数 多 → 少": "-reviewCount", "レビュー评分 高 → 低": "-reviewAverage",
                "价格 低 → 高": "+itemPrice", "价格 高 → 低": "-itemPrice", "更新时间 新 → 旧": "-updateTimestamp",
                "アフィリエイト料率 高 → 低": "-affiliateRate",
            }
            sort_label = st.selectbox("排序", list(sort_options.keys()))
            sort = sort_options[sort_label]
        with c8:
            postage_only = st.checkbox("只看送料無料")
            has_review_only = st.checkbox("只看有レビュー")
            image_only = st.checkbox("只看有图片", value=True)
        search_button = st.button("开始搜索", type="primary")

    if search_button:
        if not keyword.strip() and not genre_id.strip():
            st.error("关键词和ジャンルID至少填一个。")
            return
        with st.spinner("正在搜索楽天商品数据..."):
            try:
                data = call_proxy_search(settings["proxy_url"], settings["proxy_token"], keyword, genre_id, min_price, max_price, hits, page, sort, postage_only, has_review_only, image_only)
                items = normalize_items(data)
                df = convert_items_to_df(items, source_keyword=keyword)
                total_count = safe_int(data.get("count") or data.get("Count") or 0)
                st.session_state["search_df"] = df
                st.session_state["last_keyword"] = keyword
                st.session_state["last_total_count"] = total_count
                st.session_state["last_raw_data"] = data
                save_snapshots(df, keyword=keyword, total_count=total_count, genre_id=genre_id)
                st.success(f"搜索完成：约 {total_count:,} 件，当前显示 {len(df)} 件。已保存趋势快照。")
            except Exception as e:
                st.error(str(e))
                return

    df = st.session_state.get("search_df", pd.DataFrame())
    if df is None or df.empty:
        st.info("输入关键词后点击开始搜索。")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("显示商品数", len(df))
    c2.metric("平均价格", f"{int(df['价格'].mean()):,} 円")
    c3.metric("レビュー均值", f"{df['レビュー数'].mean():.0f}")
    c4.metric("评分均值", f"{df['レビュー评分'].mean():.2f}")
    c5.metric("最高选品分", f"{df['选品分'].max():.1f}")

    show_cols = ["选品分", "排名位置", "商品名", "价格", "レビュー数", "レビュー评分", "送料無料", "ポイント倍率", "店铺名", "genreId", "itemCode", "商品URL"]
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
    st.download_button("下载当前搜索结果 Excel", data=make_excel_download(df), file_name=f"rakuten_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.subheader("Top 商品预览与收藏")
    for idx, row in df.head(12).iterrows():
        with st.container(border=True):
            col_img, col_info, col_action = st.columns([1, 4, 1])
            with col_img:
                if row.get("图片", ""):
                    st.image(row.get("图片"), width=150)
                else:
                    st.write("无图片")
            with col_info:
                st.markdown(f"### {row.get('商品名', '')}")
                st.write(f"价格：{row.get('价格')} 円 ｜ レビュー：{row.get('レビュー数')} / {row.get('レビュー评分')} ｜ 选品分：{row.get('选品分')}")
                st.write(f"店铺：{row.get('店铺名')} ｜ genreId：{row.get('genreId')}")
                if row.get("キャッチコピー"):
                    st.caption(row.get("キャッチコピー"))
                if row.get("商品URL"):
                    st.link_button("打开商品页面", row.get("商品URL"))
            with col_action:
                note = st.text_input("收藏备注", key=f"fav_note_{idx}")
                if st.button("收藏", key=f"fav_btn_{idx}"):
                    save_favorite(row.to_dict(), note=note)
                    st.success("已收藏")


def page_favorites() -> None:
    st.title("⭐ 收藏商品 / 竞品库")
    fav_df = load_favorites()
    if fav_df.empty:
        st.info("还没有收藏商品。请先到“商品搜索”页收藏竞品。")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("收藏数", len(fav_df))
    c2.metric("平均价格", f"{int(fav_df['price'].mean()):,} 円")
    c3.metric("平均レビュー", f"{fav_df['review_count'].mean():.0f}")
    c4.metric("平均选品分", f"{fav_df['score'].mean():.1f}")
    st.dataframe(fav_df[["id", "score", "item_name", "price", "review_count", "review_average", "shop_name", "source_keyword", "item_url", "note"]], use_container_width=True, hide_index=True)
    with st.expander("删除收藏商品"):
        fav_id = st.number_input("输入要删除的收藏ID", min_value=0, value=0, step=1)
        if st.button("删除收藏") and fav_id > 0:
            delete_favorite(int(fav_id))
            st.success("已删除，刷新页面后生效。")
            st.rerun()


def page_own_products() -> None:
    st.title("📦 自有商品")
    st.caption("支持手动添加，也支持直接粘贴楽天商品URL / itemCode 导入。")
    proxy_url = st.session_state.get("proxy_url", "")
    proxy_token = st.session_state.get("proxy_token", "")

    with st.container(border=True):
        st.subheader("从楽天商品URL导入")
        st.caption("支持格式：https://item.rakuten.co.jp/shopcode/itemid/ ，也支持直接输入 shopCode:itemId。")
        import_value = st.text_input("楽天商品URL / itemCode", value="", placeholder="例：https://item.rakuten.co.jp/shopcode/itemid/ 或 shopcode:itemid")
        col_a, col_b = st.columns([1, 3])
        with col_a:
            import_btn = st.button("读取楽天商品", type="primary")
        with col_b:
            parsed_code = extract_item_code_from_rakuten_url(import_value)
            if import_value:
                st.caption(f"解析到 itemCode：{parsed_code or '未解析到'}")
        if import_btn:
            item_code = extract_item_code_from_rakuten_url(import_value)

            if not item_code:
                st.error("没有解析到 itemCode。请确认URL类似：https://item.rakuten.co.jp/shopcode/itemid/")
            else:
                try:
                    data = call_proxy_item_lookup(proxy_url, proxy_token, item_code)
                    items = normalize_items(data)

                    if not items:
                        raise Exception("API没有返回商品数据")

                    imported = rakuten_item_to_own_product(items[0])
                    st.session_state["own_imported_product"] = imported
                    st.success("已读取楽天商品。请检查下方表单后保存。")

                except Exception as e:
                    # API查不到时，允许URL手动导入，避免 itemCode is not valid 直接卡死。
                    shop_code = ""
                    item_id = ""

                    if ":" in item_code:
                        shop_code, item_id = item_code.split(":", 1)

                    fallback = {
                        "product_name": item_id or item_code,
                        "selling_price": 0,
                        "cost_price": 0,
                        "core_selling_points": "",
                        "material": "",
                        "target_user": "",
                        "size_color": "",
                        "keywords": (item_id or item_code).replace("-", " "),
                        "image_url": "",
                        "product_url": import_value.strip(),
                        "note": (
                            f"楽天URL手动导入：{now_str()}\n"
                            f"API未能直接读取此商品。\n"
                            f"错误信息：{str(e)}\n\n"
                            f"shopCode: {shop_code}\n"
                            f"itemId: {item_id}\n"
                            f"itemCode: {item_code}\n"
                        ),
                    }

                    st.session_state["own_imported_product"] = fallback
                    st.warning("楽天API没有读取到该商品，已进入手动导入模式。请补充商品名、价格和卖点后保存。")
        imported_product = st.session_state.get("own_imported_product")
        if imported_product:
            st.markdown("**导入预览**")
            c_img, c_info = st.columns([1, 4])
            with c_img:
                if imported_product.get("image_url"):
                    st.image(imported_product.get("image_url"), width=140)
            with c_info:
                st.write(imported_product.get("product_name", ""))
                st.write(f"销售价：{imported_product.get('selling_price', 0)} 円")
                if imported_product.get("product_url"):
                    st.link_button("打开原商品", imported_product.get("product_url"))

    with st.container(border=True):
        st.subheader("添加/更新自有商品")
        imported = st.session_state.get("own_imported_product", {}) or {}
        edit_id = st.number_input("更新已有商品ID，不更新则填 0", min_value=0, value=0, step=1)
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            product_name = st.text_input("商品名", value=imported.get("product_name", ""))
        with c2:
            selling_price = st.number_input("销售价 円", min_value=0, value=safe_int(imported.get("selling_price", 0)), step=100)
        with c3:
            cost_price = st.number_input("成本价 円", min_value=0, value=safe_int(imported.get("cost_price", 0)), step=100)
        core_selling_points = st.text_area("核心卖点", value=imported.get("core_selling_points", ""), height=90, placeholder="例：UPF50+、接触冷感、男女兼用、軽量、速乾、団体注文...")
        c4, c5 = st.columns(2)
        with c4:
            material = st.text_input("材质/面料", value=imported.get("material", ""))
            target_user = st.text_input("目标人群", value=imported.get("target_user", ""))
        with c5:
            size_color = st.text_input("尺码/颜色", value=imported.get("size_color", ""))
            keywords = st.text_input("已有关键词", value=imported.get("keywords", ""))
        image_url = st.text_input("图片URL，可不填", value=imported.get("image_url", ""))
        product_url = st.text_input("商品URL，可不填", value=imported.get("product_url", ""))
        note = st.text_area("备注", value=imported.get("note", ""), height=120)
        col_save, col_clear = st.columns([1, 3])
        with col_save:
            save_btn = st.button("保存自有商品", type="primary")
        with col_clear:
            if st.button("清空导入内容"):
                st.session_state["own_imported_product"] = {}
                st.rerun()
        if save_btn:
            data = {
                "product_name": product_name,
                "selling_price": selling_price,
                "cost_price": cost_price,
                "core_selling_points": core_selling_points,
                "material": material,
                "target_user": target_user,
                "size_color": size_color,
                "keywords": keywords,
                "image_url": image_url,
                "product_url": product_url,
                "note": note,
            }
            save_own_product(data, product_id=int(edit_id) if edit_id else None)
            st.session_state["own_imported_product"] = {}
            st.success("已保存。")
            st.rerun()

    own_df = load_own_products()
    st.subheader("自有商品列表")
    if own_df.empty:
        st.info("还没有自有商品。")
    else:
        st.dataframe(own_df, use_container_width=True, hide_index=True)
        with st.expander("删除自有商品"):
            product_id = st.number_input("输入要删除的商品ID", min_value=0, value=0, step=1, key="delete_own_id")
            if st.button("删除自有商品") and product_id > 0:
                delete_own_product(int(product_id))
                st.success("已删除。")
                st.rerun()


def page_compare(settings: Dict[str, Any]) -> None:
    st.title("⚖️ 商品对比分析")
    own_df = load_own_products()
    fav_df = load_favorites()
    if own_df.empty:
        st.warning("请先添加自有商品。")
        return
    if fav_df.empty:
        st.warning("请先收藏竞品。")
        return
    own_options = {f"#{row['id']} {row['product_name']}": row["id"] for _, row in own_df.iterrows()}
    selected_own_label = st.selectbox("选择自有商品", list(own_options.keys()))
    own_row = own_df[own_df["id"] == own_options[selected_own_label]].iloc[0]
    fav_options = {f"#{row['id']} {str(row['item_name'])[:60]}": row["id"] for _, row in fav_df.iterrows()}
    selected_labels = st.multiselect("竞品", list(fav_options.keys()), default=list(fav_options.keys())[:5])
    selected_ids = [fav_options[label] for label in selected_labels]
    selected_fav_df = fav_df[fav_df["id"].isin(selected_ids)].copy()
    if selected_fav_df.empty:
        st.info("请选择至少一个竞品。")
        return
    payload = build_compare_payload(own_row, selected_fav_df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("我的售价档", payload["my_product"]["price_band"])
    c2.metric("竞品平均价", f"{int(payload['competitor_summary']['avg_price']):,} 円")
    c3.metric("竞品平均レビュー", f"{payload['competitor_summary']['avg_review_count']:.0f}")
    c4.metric("竞品平均选品分", f"{payload['competitor_summary']['avg_score']:.1f}")
    st.subheader("共同词 / 独有词")
    word_analysis = payload["word_analysis"]
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**共同词（我方与竞品交集）**")
        st.write(" / ".join(word_analysis["共同词_我方与竞品交集"][:50]) or "暂无")
        st.markdown("**我方独有词**")
        st.write(" / ".join(word_analysis["我方独有词"][:60]) or "暂无")
    with col2:
        st.markdown("**竞品共同词**")
        st.write(" / ".join(word_analysis["竞品共同词"][:60]) or "暂无")
        st.markdown("**竞品独有词**")
        st.write(" / ".join(word_analysis["竞品独有词"][:80]) or "暂无")
    st.dataframe(selected_fav_df[["id", "item_name", "price", "review_count", "review_average", "score", "shop_name", "item_url"]], use_container_width=True, hide_index=True)
    if st.button("用 AI 生成差异化建议", type="primary"):
        with st.spinner("AI 正在对比分析..."):
            try:
                result = call_deepseek(settings["deepseek_api_key"], build_ai_compare_prompt(payload), settings["depth"], settings["selected_model"])
                st.session_state["compare_ai_result"] = result
            except Exception as e:
                st.error(str(e))
    if st.session_state.get("compare_ai_result"):
        st.subheader("AI 差异化建议")
        st.markdown(st.session_state["compare_ai_result"])


def page_ai_analysis(settings: Dict[str, Any]) -> None:
    st.title("🧠 AI 选品数据分析")
    df = st.session_state.get("search_df", pd.DataFrame())
    keyword = st.session_state.get("last_keyword", "")
    if df is None or df.empty:
        st.warning("请先到“商品搜索”页搜索商品。")
        return
    summary = summarize_market_df(df, keyword=keyword)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("结果数", summary.get("result_count", 0))
    c2.metric("中位价格", f"{int(summary.get('median_price', 0)):,} 円")
    c3.metric("平均レビュー", f"{summary.get('avg_review_count', 0):.0f}")
    c4.metric("送料無料占比", f"{summary.get('free_shipping_rate', 0) * 100:.0f}%")
    with st.expander("高频词"):
        st.write(" / ".join([f"{w}({c})" for w, c in summary.get("top_keywords", [])[:50]]))
    user_note = st.text_area("补充你的判断/供应链信息，可选", height=100)
    if st.button("开始 AI 选品分析", type="primary"):
        with st.spinner("AI 正在分析市场数据..."):
            try:
                if user_note.strip():
                    summary["user_note"] = user_note.strip()
                result = call_deepseek(settings["deepseek_api_key"], build_ai_market_prompt(summary, settings["depth"]), settings["depth"], settings["selected_model"])
                st.session_state["ai_market_result"] = result
            except Exception as e:
                st.error(str(e))
    if st.session_state.get("ai_market_result"):
        st.subheader("AI 选品分析结果")
        st.markdown(st.session_state["ai_market_result"])


def page_ai_keywords(settings: Dict[str, Any]) -> None:
    st.title("🔥 AI 爆款关键词建议")
    df = st.session_state.get("search_df", pd.DataFrame())
    keyword = st.session_state.get("last_keyword", "")
    if df is None or df.empty:
        st.warning("请先到“商品搜索”页搜索竞品。")
        return
    summary = summarize_market_df(df, keyword=keyword)
    product_info = st.text_area("我的商品信息，可选", height=120)
    st.subheader("程序自动提取的高频词")
    st.write(" / ".join([f"{w}({c})" for w, c in summary.get("top_keywords", [])[:80]]))
    if st.button("生成爆款关键词建议", type="primary"):
        with st.spinner("AI 正在生成关键词..."):
            try:
                result = call_deepseek(settings["deepseek_api_key"], build_ai_keyword_prompt(summary, product_info), settings["depth"], settings["selected_model"])
                st.session_state["ai_keyword_result"] = result
            except Exception as e:
                st.error(str(e))
    if st.session_state.get("ai_keyword_result"):
        st.subheader("AI 关键词结果")
        st.markdown(st.session_state["ai_keyword_result"])


def page_suggest() -> None:
    st.title("🔎 楽天实时联想词搜索")
    st.caption("先提供稳定的手动采集入口。后面如果你确定一个可用的サジェスト接口，可以再接入 Worker 自动请求。")
    base_keyword = st.text_input("输入主词", value="キッズ ダンス衣装")
    if base_keyword.strip():
        rakuten_search_url = f"https://search.rakuten.co.jp/search/mall/{requests.utils.quote(base_keyword.strip())}/"
        st.link_button("打开楽天搜索页，查看搜索框联想词", rakuten_search_url)
    raw_suggest = st.text_area("把你在楽天搜索框看到的联想词粘贴到这里，一行一个", height=180)
    if raw_suggest.strip():
        suggestions = [x.strip() for x in raw_suggest.splitlines() if x.strip()]
        df_suggest = pd.DataFrame({"联想词": suggestions})
        st.dataframe(df_suggest, use_container_width=True, hide_index=True)
        counter = Counter()
        for s in suggestions:
            counter.update(text_to_words(s, min_len=1))
        st.write(" / ".join([f"{w}({c})" for w, c in counter.most_common(80)]))
        st.download_button("下载联想词 CSV", data=df_suggest.to_csv(index=False).encode("utf-8-sig"), file_name=f"rakuten_suggest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv")


def page_trends() -> None:
    st.title("📈 趋势追踪")
    history_df = load_search_history()
    snapshots_df = load_snapshots()
    if history_df.empty or snapshots_df.empty:
        st.info("还没有趋势数据。请先在商品搜索页搜索几次。")
        return
    st.subheader("搜索历史")
    st.dataframe(history_df.head(100), use_container_width=True, hide_index=True)
    keywords = sorted(snapshots_df["keyword"].dropna().unique().tolist())
    selected_keyword = st.selectbox("选择关键词", ["全部"] + keywords)
    trend_df = snapshots_df if selected_keyword == "全部" else snapshots_df[snapshots_df["keyword"] == selected_keyword]
    st.subheader("快照数据")
    st.dataframe(trend_df.head(300), use_container_width=True, hide_index=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("快照记录数", len(trend_df))
    c2.metric("覆盖商品数", trend_df["item_code"].nunique())
    c3.metric("平均价格", f"{trend_df['price'].mean():.0f} 円")
    c4.metric("平均レビュー", f"{trend_df['review_count'].mean():.0f}")

    trend_clean = trend_df.dropna(subset=["item_code"]).copy()
    trend_clean = trend_clean[trend_clean["item_code"] != ""]
    if not trend_clean.empty:
        changes = []
        for item_code, g in trend_clean.groupby("item_code"):
            g = g.sort_values("snapshot_time")
            if len(g) < 2:
                continue
            first = g.iloc[0]
            last = g.iloc[-1]
            changes.append({
                "item_code": item_code,
                "商品名": last.get("item_name", ""),
                "关键词": last.get("keyword", ""),
                "最早时间": first.get("snapshot_time", ""),
                "最新时间": last.get("snapshot_time", ""),
                "价格变化": safe_int(last.get("price", 0)) - safe_int(first.get("price", 0)),
                "レビュー增长": safe_int(last.get("review_count", 0)) - safe_int(first.get("review_count", 0)),
                "排名变化": safe_int(first.get("rank_position", 0)) - safe_int(last.get("rank_position", 0)),
                "最新价格": safe_int(last.get("price", 0)),
                "最新レビュー": safe_int(last.get("review_count", 0)),
                "最新排名": safe_int(last.get("rank_position", 0)),
                "商品URL": last.get("item_url", ""),
            })
        changes_df = pd.DataFrame(changes)
        if not changes_df.empty:
            st.subheader("レビュー增长榜")
            st.dataframe(changes_df.sort_values("レビュー增长", ascending=False).head(50), use_container_width=True, hide_index=True)
            st.subheader("排名上升榜")
            st.dataframe(changes_df.sort_values("排名变化", ascending=False).head(50), use_container_width=True, hide_index=True)
        else:
            st.info("目前每个商品只有一次快照。多跑几天后会出现增长榜。")
    daily = trend_df.groupby("snapshot_date").agg(平均价格=("price", "mean"), 平均レビュー=("review_count", "mean"), 平均评分=("review_average", "mean"), 记录数=("id", "count")).reset_index()
    st.line_chart(daily.set_index("snapshot_date")[["平均价格", "平均レビュー", "平均评分"]])
    st.download_button("下载趋势快照 CSV", data=trend_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"rakuten_trends_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv")


def main() -> None:
    init_db()
    settings = sidebar_settings()
    page = settings["page"]
    if page == "商品搜索":
        page_search(settings)
    elif page == "收藏商品":
        page_favorites()
    elif page == "自有商品":
        page_own_products()
    elif page == "对比分析":
        page_compare(settings)
    elif page == "AI选品分析":
        page_ai_analysis(settings)
    elif page == "AI爆款关键词":
        page_ai_keywords(settings)
    elif page == "楽天联想词":
        page_suggest()
    elif page == "趋势追踪":
        page_trends()


if __name__ == "__main__":
    main()
