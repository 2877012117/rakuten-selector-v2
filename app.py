import re
import sqlite3
from collections import Counter
from datetime import datetime, date
from io import BytesIO
from typing import Any, Dict, List, Optional
import pandas as pd
import requests
import streamlit as st

APP_TITLE = "Rakuten Selector V2 API Ranking"
DEFAULT_PROXY_URL = ""
DEFAULT_DB_PATH = "rakuten_selector_v2_api_ranking.sqlite3"

st.set_page_config(page_title=APP_TITLE, page_icon="🛒", layout="wide")

def get_secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key, default)
        return str(v) if v is not None else default
    except Exception:
        return default

def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def today_str(): return date.today().strftime("%Y-%m-%d")

def to_number(value: Any, default: float = 0) -> float:
    try:
        if value is None or value == "": return default
        return float(value)
    except Exception:
        return default

def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "": return default
        return int(float(value))
    except Exception:
        return default

def clean_text(text: Any) -> str:
    if text is None: return ""
    text = re.sub(r"<[^>]+>", " ", str(text)).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()

def text_to_words(text: str, min_len: int = 2) -> List[str]:
    text = clean_text(text)
    text = re.sub(r"[【】\[\]（）(){}<>《》「」『』｜|/\\,，.。;；:：!！?？+＋・★☆●○◎※♪#＃\-_＝=~〜～]", " ", text)
    parts = re.split(r"\s+", text)
    stopwords = {"送料無料","送料","無料","税込","楽天","市場","商品","人気","おすすめ","ランキング","レビュー","ポイント","クーポン","セール","価格","限定","あり","なし","対応","可能","用","の","に","を","が","と","で","から","です","ます","する","した","して","ため","こちら","これ"}
    return [p.strip() for p in parts if len(p.strip()) >= min_len and p.strip() not in stopwords and not re.fullmatch(r"\d+", p.strip())]

def get_first_image(item: Dict[str, Any]) -> str:
    for key in ["mediumImageUrls", "smallImageUrls"]:
        urls = item.get(key, [])
        if isinstance(urls, list) and urls:
            first = urls[0]
            if isinstance(first, dict): return first.get("imageUrl", "") or first.get("url", "")
            if isinstance(first, str): return first
    for key in ["imageUrl", "mediumImageUrl", "smallImageUrl"]:
        if item.get(key): return item.get(key)
    return ""

def normalize_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("items") or data.get("Items") or []
    out=[]
    for x in items:
        if isinstance(x, dict) and "Item" in x and isinstance(x["Item"], dict): out.append(x["Item"])
        elif isinstance(x, dict): out.append(x)
    return out

def extract_item_code_from_rakuten_url(value: str) -> str:
    value=(value or "").strip()
    if not value: return ""
    if ":" in value and not value.startswith("http"): return value.strip().strip("/")
    m=re.search(r"item\.rakuten\.co\.jp/([^/?#]+)/([^/?#]+)", value)
    return f"{m.group(1).strip()}:{m.group(2).strip()}" if m else ""

def get_conn():
    conn=sqlite3.connect(DEFAULT_DB_PATH, check_same_thread=False)
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS favorites (id INTEGER PRIMARY KEY AUTOINCREMENT,item_code TEXT UNIQUE,item_name TEXT,catchcopy TEXT,price INTEGER,review_count INTEGER,review_average REAL,postage TEXT,availability TEXT,point_rate REAL,shop_name TEXT,shop_code TEXT,genre_id TEXT,item_url TEXT,image_url TEXT,item_caption TEXT,source_keyword TEXT,score REAL,note TEXT,created_at TEXT,updated_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS own_products (id INTEGER PRIMARY KEY AUTOINCREMENT,product_name TEXT,selling_price INTEGER,cost_price INTEGER,core_selling_points TEXT,material TEXT,target_user TEXT,size_color TEXT,keywords TEXT,image_url TEXT,product_url TEXT,note TEXT,created_at TEXT,updated_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS search_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,snapshot_date TEXT,snapshot_time TEXT,keyword TEXT,item_code TEXT,item_name TEXT,price INTEGER,review_count INTEGER,review_average REAL,rank_position INTEGER,score REAL,shop_name TEXT,genre_id TEXT,item_url TEXT,created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ranking_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,snapshot_date TEXT,snapshot_time TEXT,period TEXT,genre_id TEXT,sex INTEGER,age INTEGER,rank_position INTEGER,item_code TEXT,item_name TEXT,price INTEGER,review_count INTEGER,review_average REAL,shop_name TEXT,genre_id_item TEXT,item_url TEXT,image_url TEXT,created_at TEXT)""")
    conn.commit(); conn.close()

def save_favorite(row: Dict[str, Any], note: str = ""):
    conn=get_conn()
    conn.execute("""INSERT INTO favorites (item_code,item_name,catchcopy,price,review_count,review_average,postage,availability,point_rate,shop_name,shop_code,genre_id,item_url,image_url,item_caption,source_keyword,score,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(item_code) DO UPDATE SET item_name=excluded.item_name,catchcopy=excluded.catchcopy,price=excluded.price,review_count=excluded.review_count,review_average=excluded.review_average,postage=excluded.postage,availability=excluded.availability,point_rate=excluded.point_rate,shop_name=excluded.shop_name,shop_code=excluded.shop_code,genre_id=excluded.genre_id,item_url=excluded.item_url,image_url=excluded.image_url,item_caption=excluded.item_caption,source_keyword=excluded.source_keyword,score=excluded.score,note=CASE WHEN excluded.note!='' THEN excluded.note ELSE favorites.note END,updated_at=excluded.updated_at""",(
        row.get("itemCode","") or row.get("商品コード","") or row.get("item_code","") or row.get("商品URL",""), row.get("商品名","") or row.get("item_name",""), row.get("キャッチコピー","") or row.get("catchcopy",""), safe_int(row.get("价格", row.get("price",0))), safe_int(row.get("レビュー数", row.get("review_count",0))), to_number(row.get("レビュー评分", row.get("review_average",0))), row.get("送料無料", row.get("postage","")), row.get("库存状态", row.get("availability","")), to_number(row.get("ポイント倍率", row.get("point_rate",0))), row.get("店铺名", row.get("shop_name","")), row.get("shopCode", row.get("shop_code","")), row.get("genreId", row.get("genre_id","")), row.get("商品URL", row.get("item_url","")), row.get("图片", row.get("image_url","")), row.get("商品说明", row.get("item_caption","")), row.get("来源关键词", row.get("source_keyword","")), to_number(row.get("选品分", row.get("score",0))), note, now_str(), now_str()))
    conn.commit(); conn.close()

def load_favorites():
    conn=get_conn(); df=pd.read_sql_query("SELECT * FROM favorites ORDER BY updated_at DESC", conn); conn.close(); return df

def delete_favorite(fav_id:int):
    conn=get_conn(); conn.execute("DELETE FROM favorites WHERE id=?",(fav_id,)); conn.commit(); conn.close()

def save_own_product(data:Dict[str,Any], product_id:Optional[int]=None):
    conn=get_conn()
    if product_id:
        conn.execute("""UPDATE own_products SET product_name=?,selling_price=?,cost_price=?,core_selling_points=?,material=?,target_user=?,size_color=?,keywords=?,image_url=?,product_url=?,note=?,updated_at=? WHERE id=?""",(data.get("product_name",""),safe_int(data.get("selling_price",0)),safe_int(data.get("cost_price",0)),data.get("core_selling_points",""),data.get("material",""),data.get("target_user",""),data.get("size_color",""),data.get("keywords",""),data.get("image_url",""),data.get("product_url",""),data.get("note",""),now_str(),product_id))
    else:
        conn.execute("""INSERT INTO own_products (product_name,selling_price,cost_price,core_selling_points,material,target_user,size_color,keywords,image_url,product_url,note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",(data.get("product_name",""),safe_int(data.get("selling_price",0)),safe_int(data.get("cost_price",0)),data.get("core_selling_points",""),data.get("material",""),data.get("target_user",""),data.get("size_color",""),data.get("keywords",""),data.get("image_url",""),data.get("product_url",""),data.get("note",""),now_str(),now_str()))
    conn.commit(); conn.close()

def load_own_products():
    conn=get_conn(); df=pd.read_sql_query("SELECT * FROM own_products ORDER BY updated_at DESC", conn); conn.close(); return df

def save_search_snapshots(df: pd.DataFrame, keyword: str):
    if df.empty: return
    conn=get_conn()
    for _,row in df.iterrows():
        conn.execute("""INSERT INTO search_snapshots (snapshot_date,snapshot_time,keyword,item_code,item_name,price,review_count,review_average,rank_position,score,shop_name,genre_id,item_url,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(today_str(),now_str(),keyword,row.get("itemCode",""),row.get("商品名",""),safe_int(row.get("价格",0)),safe_int(row.get("レビュー数",0)),to_number(row.get("レビュー评分",0)),safe_int(row.get("排名位置",0)),to_number(row.get("选品分",0)),row.get("店铺名",""),row.get("genreId",""),row.get("商品URL",""),now_str()))
    conn.commit(); conn.close()

def save_ranking_snapshots(df: pd.DataFrame, period:str, genre_id:str, sex:int=0, age:int=0):
    if df.empty: return
    conn=get_conn()
    for _,row in df.iterrows():
        conn.execute("""INSERT INTO ranking_snapshots (snapshot_date,snapshot_time,period,genre_id,sex,age,rank_position,item_code,item_name,price,review_count,review_average,shop_name,genre_id_item,item_url,image_url,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(today_str(),now_str(),period,genre_id,sex,age,safe_int(row.get("排名",0)),row.get("itemCode",""),row.get("商品名",""),safe_int(row.get("价格",0)),safe_int(row.get("レビュー数",0)),to_number(row.get("レビュー评分",0)),row.get("店铺名",""),row.get("genreId",""),row.get("商品URL",""),row.get("图片",""),now_str()))
    conn.commit(); conn.close()

def load_ranking_snapshots():
    conn=get_conn(); df=pd.read_sql_query("SELECT * FROM ranking_snapshots ORDER BY snapshot_time DESC", conn); conn.close(); return df

def infer_ranking_url(proxy_url:str)->str:
    proxy_url=(proxy_url or "").strip()
    if proxy_url.endswith("/ichiba/search"): return proxy_url.replace("/ichiba/search","/ichiba/ranking")
    return proxy_url.rstrip("/")+"/ichiba/ranking"

def call_proxy(proxy_url:str, token:str, payload:Dict[str,Any])->Dict[str,Any]:
    headers={"Content-Type":"application/json"}
    if token: headers["X-Proxy-Token"]=token
    res=requests.post(proxy_url,json=payload,headers=headers,timeout=60)
    if res.status_code!=200: raise Exception(f"代理/API错误：{res.status_code}\n{res.text[:1200]}")
    data=res.json()
    if data.get("ok") is False: raise Exception(data.get("error","代理返回错误"))
    if "error" in data: raise Exception(f"{data.get('error')}: {data.get('error_description')}")
    if "errors" in data: raise Exception(str(data["errors"]))
    return data

def call_search(proxy_url,token,keyword,genre_id,min_price,max_price,hits,page,sort,postage_only,has_review_only,image_only):
    payload={"format":"json","formatVersion":2,"hits":hits,"page":page,"sort":sort,"availability":1,"elements":",".join(["itemName","catchcopy","itemPrice","itemUrl","affiliateUrl","itemCode","shopName","shopCode","reviewCount","reviewAverage","mediumImageUrls","smallImageUrls","postageFlag","availability","pointRate","genreId","itemCaption"])}
    if keyword.strip(): payload["keyword"]=keyword.strip()
    if genre_id.strip(): payload["genreId"]=genre_id.strip()
    if min_price>0: payload["minPrice"]=min_price
    if max_price>0: payload["maxPrice"]=max_price
    if postage_only: payload["postageFlag"]=1
    if has_review_only: payload["hasReviewFlag"]=1
    if image_only: payload["imageFlag"]=1
    return call_proxy(proxy_url,token,payload)

def call_ranking(proxy_url:str, token:str, period:str, genre_id:str, sex:int, age:int):
    ranking_url=infer_ranking_url(proxy_url)
    payload={"format":"json","formatVersion":2,"elements":",".join(["rank","itemName","itemPrice","itemUrl","itemCode","shopName","shopCode","reviewCount","reviewAverage","mediumImageUrls","smallImageUrls","genreId","catchcopy"])}
    if period=="realtime": payload["period"]="realtime"
    payload["genreId"] = genre_id or "0"
    if payload["genreId"] in ("","0"):
        if sex: payload["sex"]=sex
        if age: payload["age"]=age
    return call_proxy(ranking_url,token,payload)

def call_item_lookup(proxy_url:str, token:str, item_code:str):
    payload={"format":"json","formatVersion":2,"itemCode":item_code,"hits":1,"page":1,"availability":1,"elements":",".join(["itemName","catchcopy","itemPrice","itemUrl","affiliateUrl","itemCode","shopName","shopCode","reviewCount","reviewAverage","mediumImageUrls","smallImageUrls","postageFlag","availability","pointRate","genreId","itemCaption"])}
    return call_proxy(proxy_url,token,payload)

def calc_score(row:Dict[str,Any])->float:
    score=0.0; rc=to_number(row.get("レビュー数",0)); rv=to_number(row.get("レビュー评分",0)); price=to_number(row.get("价格",0))
    score+=min(rc/300*25,25)
    if rv>=4.7: score+=20
    elif rv>=4.5: score+=17
    elif rv>=4.2: score+=13
    elif rv>=4.0: score+=9
    elif rv>0: score+=5
    if 1980<=price<=6980: score+=20
    elif 1000<=price<=9980: score+=12
    elif price>0: score+=6
    if row.get("送料無料")=="是": score+=10
    if row.get("库存状态")=="有库存": score+=10
    if row.get("图片"): score+=10
    return round(score,1)

def convert_items_to_df(items:List[Dict[str,Any]], keyword:str=""):
    rows=[]
    for idx,item in enumerate(items,start=1):
        row={"排名位置":idx,"来源关键词":keyword,"商品名":clean_text(item.get("itemName","")),"キャッチコピー":clean_text(item.get("catchcopy","")),"价格":safe_int(item.get("itemPrice",0)),"レビュー数":safe_int(item.get("reviewCount",0)),"レビュー评分":to_number(item.get("reviewAverage",0)),"送料無料":"是" if safe_int(item.get("postageFlag",0))==1 else "否","库存状态":"有库存" if safe_int(item.get("availability",0))==1 else "无库存","ポイント倍率":to_number(item.get("pointRate",1)),"店铺名":clean_text(item.get("shopName","")),"shopCode":item.get("shopCode",""),"genreId":item.get("genreId",""),"itemCode":item.get("itemCode",""),"商品URL":item.get("itemUrl",""),"affiliateUrl":item.get("affiliateUrl",""),"图片":get_first_image(item),"商品说明":clean_text(item.get("itemCaption","")),"抓取时间":now_str()}
        row["选品分"]=calc_score(row); rows.append(row)
    df=pd.DataFrame(rows)
    if not df.empty: df=df.sort_values(by="选品分",ascending=False).reset_index(drop=True)
    return df

def convert_ranking_to_df(items:List[Dict[str,Any]]):
    rows=[]
    for idx,item in enumerate(items,start=1):
        rows.append({"排名":safe_int(item.get("rank",idx)) or idx,"商品名":clean_text(item.get("itemName","")),"キャッチコピー":clean_text(item.get("catchcopy","")),"价格":safe_int(item.get("itemPrice",0)),"レビュー数":safe_int(item.get("reviewCount",0)),"レビュー评分":to_number(item.get("reviewAverage",0)),"店铺名":clean_text(item.get("shopName","")),"shopCode":item.get("shopCode",""),"genreId":item.get("genreId",""),"itemCode":item.get("itemCode",""),"商品URL":item.get("itemUrl",""),"图片":get_first_image(item),"抓取时间":now_str()})
    df=pd.DataFrame(rows)
    if not df.empty and "排名" in df.columns: df=df.sort_values("排名").reset_index(drop=True)
    return df

def make_excel_download(df:pd.DataFrame)->bytes:
    out=BytesIO()
    with pd.ExcelWriter(out,engine="openpyxl") as writer: df.to_excel(writer,index=False,sheet_name="items")
    return out.getvalue()

def top_keywords_from_df(df:pd.DataFrame,col:str="商品名"):
    if df.empty or col not in df.columns: return pd.DataFrame(columns=["keyword","count"])
    c=Counter()
    for text in df[col].astype(str).tolist(): c.update(text_to_words(text))
    return pd.DataFrame(c.most_common(100),columns=["keyword","count"])

def rakuten_item_to_own_product(item:Dict[str,Any])->Dict[str,Any]:
    item_name=clean_text(item.get("itemName","")); catchcopy=clean_text(item.get("catchcopy","")); caption=clean_text(item.get("itemCaption",""))
    words=[w for w,_ in Counter(text_to_words(" ".join([item_name,catchcopy]))).most_common(30)]
    note_text=(f"楽天から导入：{now_str()}\n" f"shopCode: {item.get('shopCode','')}\n" f"itemCode: {item.get('itemCode','')}\n" f"レビュー: {item.get('reviewCount',0)} / {item.get('reviewAverage',0)}\n\n" f"商品説明摘录：\n{caption[:800]}")
    return {"product_name":item_name,"selling_price":safe_int(item.get("itemPrice",0)),"cost_price":0,"core_selling_points":catchcopy,"material":"","target_user":"","size_color":"","keywords":" ".join(words),"image_url":get_first_image(item),"product_url":item.get("itemUrl",""),"note":note_text}

def sidebar_settings():
    st.sidebar.title("🛒 Rakuten Selector V2")
    st.sidebar.caption("API Ranking 版")
    proxy_url=get_secret("RAKUTEN_PROXY_URL"); proxy_token=get_secret("RAKUTEN_PROXY_TOKEN")
    st.sidebar.header("云端配置状态")
    if proxy_url and proxy_token: st.sidebar.success("乐天代理已从 Secrets 读取")
    else:
        st.sidebar.warning("未检测到代理 Secrets，本地测试请手动填写。")
        proxy_url=st.sidebar.text_input("乐天代理地址",value=DEFAULT_PROXY_URL)
        proxy_token=st.sidebar.text_input("代理密码",type="password")
    return {"proxy_url":proxy_url,"proxy_token":proxy_token}

def page_search(settings):
    st.title("🛒 商品搜索")
    with st.container(border=True):
        c1,c2,c3,c4=st.columns([2,1,1,1])
        with c1: keyword=st.text_input("关键词",value=st.session_state.get("last_keyword","キッズ ダンス衣装"))
        with c2: genre_id=st.text_input("ジャンルID",value="")
        with c3: min_price=st.number_input("最低价格",min_value=0,value=0,step=100)
        with c4: max_price=st.number_input("最高价格",min_value=0,value=0,step=100)
        c5,c6,c7,c8=st.columns([1,1,2,2])
        with c5: hits=st.slider("每页数量",min_value=1,max_value=30,value=30)
        with c6: page=st.number_input("页码",min_value=1,max_value=100,value=1,step=1)
        with c7:
            opts={"乐天标准":"standard","レビュー数 多 → 少":"-reviewCount","レビュー评分 高 → 低":"-reviewAverage","价格 低 → 高":"+itemPrice","价格 高 → 低":"-itemPrice"}
            sort=opts[st.selectbox("排序",list(opts.keys()))]
        with c8:
            postage_only=st.checkbox("只看送料無料"); has_review_only=st.checkbox("只看有レビュー"); image_only=st.checkbox("只看有图片",value=True)
        if st.button("开始搜索",type="primary"):
            try:
                data=call_search(settings["proxy_url"],settings["proxy_token"],keyword,genre_id,min_price,max_price,hits,int(page),sort,postage_only,has_review_only,image_only)
                df=convert_items_to_df(normalize_items(data),keyword=keyword); st.session_state["search_df"]=df; st.session_state["last_keyword"]=keyword; save_search_snapshots(df,keyword); st.success(f"搜索完成：当前显示 {len(df)} 件")
            except Exception as e: st.error(str(e))
    df=st.session_state.get("search_df",pd.DataFrame())
    if df is None or df.empty: st.info("输入关键词后点击开始搜索。"); return
    st.dataframe(df[["选品分","排名位置","商品名","价格","レビュー数","レビュー评分","店铺名","genreId","itemCode","商品URL"]],use_container_width=True,hide_index=True)
    st.download_button("下载 Excel",data=make_excel_download(df),file_name=f"rakuten_search_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def page_ranking(settings):
    st.title("🏆 楽天総合ランキング抓取")
    st.caption("使用官方 Rakuten Ichiba Item Ranking API，不爬网页。新版 Ranking API 最多可返回 1000 位。")
    with st.container(border=True):
        c1,c2,c3,c4=st.columns(4)
        with c1:
            period_label=st.selectbox("ランキング类型",["リアルタイム","デイリー"]); period="realtime" if period_label=="リアルタイム" else "daily"
        with c2: genre_id=st.text_input("genreId，総合填 0",value="0")
        with c3:
            sex_label=st.selectbox("性别过滤，仅総合有效",["全部","男性","女性"])
            sex = 1 if sex_label=="女性" else 0
        with c4:
            age=st.selectbox("年龄过滤，仅総合有效",[0,10,20,30,40,50],format_func=lambda x:"全部" if x==0 else f"{x}代")
        st.info("注意：genreId 与性别/年龄不能同时指定。genreId 不为 0 时，程序会自动忽略性别/年龄。")
        fetch_btn=st.button("抓取ランキング",type="primary")
    if fetch_btn:
        try:
            data=call_ranking(settings["proxy_url"],settings["proxy_token"],period,genre_id,sex,int(age)); df=convert_ranking_to_df(normalize_items(data)); save_ranking_snapshots(df,period,genre_id,sex,int(age)); st.session_state["ranking_df"]=df; st.success(f"抓取完成：{period}，{len(df)} 件")
        except Exception as e: st.error(str(e))
    df=st.session_state.get("ranking_df",pd.DataFrame())
    if df is not None and not df.empty:
        c1,c2,c3,c4=st.columns(4); c1.metric("商品数",len(df)); c2.metric("平均价格",f"{int(df['价格'].mean()):,} 円"); c3.metric("平均レビュー",f"{df['レビュー数'].mean():.0f}"); c4.metric("平均评分",f"{df['レビュー评分'].mean():.2f}")
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button("下载ランキング Excel",data=make_excel_download(df),file_name=f"rakuten_ranking_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.subheader("ランキング热词"); st.dataframe(top_keywords_from_df(df,"商品名").head(50),use_container_width=True,hide_index=True)
    st.subheader("历史ランキング快照"); hist=load_ranking_snapshots(); st.dataframe(hist.head(300),use_container_width=True,hide_index=True) if not hist.empty else st.info("暂无ランキング历史。")

def page_favorites():
    st.title("⭐ 收藏商品 / 竞品库"); fav_df=load_favorites()
    if fav_df.empty: st.info("还没有收藏商品。"); return
    st.dataframe(fav_df,use_container_width=True,hide_index=True)
    with st.expander("删除收藏"):
        fav_id=st.number_input("收藏ID",min_value=0,value=0,step=1)
        if st.button("删除") and fav_id>0: delete_favorite(int(fav_id)); st.success("已删除"); st.rerun()

def page_own_products(settings):
    st.title("📦 自有商品"); st.caption("支持手动添加，也支持楽天商品URL / itemCode 导入。")
    with st.container(border=True):
        st.subheader("从楽天商品URL导入"); import_value=st.text_input("楽天商品URL / itemCode",placeholder="https://item.rakuten.co.jp/shopcode/itemid/ 或 shopcode:itemid")
        if import_value: st.caption(f"解析到 itemCode：{extract_item_code_from_rakuten_url(import_value) or '未解析到'}")
        if st.button("读取楽天商品",type="primary"):
            item_code=extract_item_code_from_rakuten_url(import_value)
            if not item_code: st.error("没有解析到 itemCode。")
            else:
                try:
                    data=call_item_lookup(settings["proxy_url"],settings["proxy_token"],item_code); items=normalize_items(data)
                    if not items: raise Exception("API没有返回商品数据")
                    st.session_state["own_imported_product"]=rakuten_item_to_own_product(items[0]); st.success("已读取楽天商品。")
                except Exception as e:
                    shop_code,item_id=("","")
                    if ":" in item_code: shop_code,item_id=item_code.split(":",1)
                    st.session_state["own_imported_product"]={"product_name":item_id,"selling_price":0,"cost_price":0,"core_selling_points":"","material":"","target_user":"","size_color":"","keywords":item_id.replace("-"," "),"image_url":"","product_url":import_value.strip(),"note":f"楽天URL手动导入：{now_str()}\nAPI未能直接读取此商品。\n错误信息：{str(e)}\n\nshopCode: {shop_code}\nitemId: {item_id}\nitemCode: {item_code}\n"}; st.warning("API未读取到该商品，已进入手动导入模式。")
    imported=st.session_state.get("own_imported_product",{}) or {}
    with st.container(border=True):
        st.subheader("添加/更新自有商品"); edit_id=st.number_input("更新已有商品ID，不更新填 0",min_value=0,value=0,step=1)
        c1,c2,c3=st.columns([2,1,1])
        with c1: product_name=st.text_input("商品名",value=imported.get("product_name",""))
        with c2: selling_price=st.number_input("销售价 円",min_value=0,value=safe_int(imported.get("selling_price",0)),step=100)
        with c3: cost_price=st.number_input("成本价 円",min_value=0,value=safe_int(imported.get("cost_price",0)),step=100)
        core_selling_points=st.text_area("核心卖点",value=imported.get("core_selling_points",""),height=90)
        c4,c5=st.columns(2)
        with c4:
            material=st.text_input("材质/面料",value=imported.get("material","")); target_user=st.text_input("目标人群",value=imported.get("target_user",""))
        with c5:
            size_color=st.text_input("尺码/颜色",value=imported.get("size_color","")); keywords=st.text_input("已有关键词",value=imported.get("keywords",""))
        image_url=st.text_input("图片URL",value=imported.get("image_url","")); product_url=st.text_input("商品URL",value=imported.get("product_url","")); note=st.text_area("备注",value=imported.get("note",""),height=120)
        if st.button("保存自有商品",type="primary"):
            save_own_product({"product_name":product_name,"selling_price":selling_price,"cost_price":cost_price,"core_selling_points":core_selling_points,"material":material,"target_user":target_user,"size_color":size_color,"keywords":keywords,"image_url":image_url,"product_url":product_url,"note":note},product_id=int(edit_id) if edit_id else None); st.session_state["own_imported_product"]={}; st.success("已保存"); st.rerun()
    st.subheader("自有商品列表"); st.dataframe(load_own_products(),use_container_width=True,hide_index=True)

def page_trends():
    st.title("📈 趋势追踪"); df=load_ranking_snapshots()
    if df.empty: st.info("暂无ランキング趋势数据。请先抓取ランキング。"); return
    st.dataframe(df.head(500),use_container_width=True,hide_index=True)
    st.subheader("ランキング热词"); temp=df.rename(columns={"item_name":"商品名"}); st.dataframe(top_keywords_from_df(temp,"商品名").head(100),use_container_width=True,hide_index=True)

def page_compare():
    st.title("⚖️ 对比分析"); fav_df=load_favorites(); own_df=load_own_products()
    if fav_df.empty or own_df.empty: st.info("请先添加自有商品并收藏竞品。"); return
    st.write("自有商品"); st.dataframe(own_df,use_container_width=True,hide_index=True); st.write("收藏竞品"); st.dataframe(fav_df,use_container_width=True,hide_index=True)

def main():
    init_db(); settings=sidebar_settings()
    page=st.sidebar.radio("功能菜单",["商品搜索","総合ランキング","收藏商品","自有商品","对比分析","趋势追踪"])
    if page=="商品搜索": page_search(settings)
    elif page=="総合ランキング": page_ranking(settings)
    elif page=="收藏商品": page_favorites()
    elif page=="自有商品": page_own_products(settings)
    elif page=="对比分析": page_compare()
    elif page=="趋势追踪": page_trends()

if __name__=="__main__": main()
