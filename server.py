# -*- coding: utf-8 -*-
"""
全球金融数据看板 · 本地数据服务
================================================================
本机运行，负责去各大公开数据源抓「真实数据」再喂给前端页面，
规避浏览器跨域限制（新浪 / 中国货币网 / 金投网 / 美国财政部 都不允许网页直连）。

数据源一览
----------------------------------------------------------------
  汇率行情        新浪财经 hq.sinajs.cn  fx_s*  （191 个货币对，覆盖东方财富外汇中心全部品种）
                  历史 K 线 / 分时：vip.stock.finance.sina.com.cn（ForexService）
  人民币汇率指数   中国货币网 chinamoney.com.cn（CFETS / BIS / SDR 三大指数 + 历史曲线）
  国债收益率      新浪财经 hq.sinajs.cn globalbd_*（美国 13 + 中国 9 全期限 + 主要国家 10 年期 35 项）
                  历史 K 线 / 分时：bond.finance.sina.com.cn
  美国国债        U.S. Treasury fiscaldata — Debt to the Penny（逐日全量 8400+ 条，表格 + Excel 下载）
  各国央行利率     金投网 calendar.cngold.org/rate.htm（28 个国家 / 地区）
  世界股市        中国内地 6 + 中国香港 3：新浪财经 hq.sinajs.cn；K 线 / 分时：腾讯 web.ifzq.gtimg.cn
                  亚太 / 欧洲 / 美洲 26 个：金投网 api.jijinhao.com（实时 + 日K / 月K / 分时）
  美股三大指数     金投网 api.jijinhao.com（JO_61864 / JO_38733 / JO_38732）
                  指数期货：新浪 hf_YM / hf_NQ / hf_ES（CME 连续合约）
  虚拟币          实时：新浪财经 w.sinajs.cn；日K / 月K：Gate.io api.gateio.ws（新浪不提供历史 K 线）
  国际期货        新浪财经 hf_*（贵金属 / 能源 / 有色 / 农产品 / 数字货币 18 个品种）
================================================================
"""
import bisect
import csv
import datetime
import io
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import requests
except ImportError:
    sys.exit("缺少 requests 库，请先执行：pip install requests")


# ================================================================ 基础工具
class _Sess(requests.Session):
    """不走系统代理（本机代理会干扰部分国内数据源）"""

    def __init__(self):
        super().__init__()
        self.trust_env = False


SESS = _Sess()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HEAD_FIN = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}
HEAD_FX = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/money/forex/hq/USDCNY.shtml"}
HEAD_BOND = {"User-Agent": UA, "Referer": "https://stock.finance.sina.com.cn/forex/globalbd/us5yt.html"}
HEAD_US = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/stock/usstock/"}
HEAD_FUT = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/futures/quotes/"}
HEAD_CRY = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/blockchain/hq.shtml"}
HEAD_CM = {"User-Agent": UA, "Referer": "https://www.chinamoney.com.cn/chinese/bkrmbidx/"}
HEAD_IDX = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/money/globalindex/"}
HEAD_JJH = {"User-Agent": UA, "Referer": "https://quote.cngold.org/gp/hqgz.html"}
HEAD_CNG = {"User-Agent": UA, "Referer": "http://calendar.cngold.org/rate.htm"}
HEAD_TRS = {"User-Agent": UA}
HEAD_TX = {"User-Agent": UA, "Referer": "https://gu.qq.com/"}
HEAD_GATE = {"User-Agent": UA, "Accept": "application/json"}

# ---------------------------------------------------------------- 缓存
_CACHE = {}
_CLOCK = threading.Lock()


def cached(key, ttl, producer):
    now = time.time()
    with _CLOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = producer()
    with _CLOCK:
        _CACHE[key] = (now, val)
    return val


def peek(key):
    """忽略 TTL 直接取缓存（用于失败时回退上一份可用数据）"""
    with _CLOCK:
        hit = _CACHE.get(key)
    return hit[1] if hit else None


def _http_get(url, headers=None, timeout=18):
    r = SESS.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r


def grab(url, headers, ttl=20, encoding=None):
    """带缓存的取数（缓存键含 ttl，避免不同时效互相覆盖）"""
    def _do():
        r = _http_get(url, headers=headers, timeout=18)
        if encoding:
            return r.content.decode(encoding, "replace")
        return r.text
    return cached(url + "|" + str(ttl), ttl, _do)


def fnum(x, default=None):
    try:
        v = float(str(x).strip())
        if v != v or v in (float("inf"), float("-inf")):   # NaN / Inf
            return default
        return v
    except Exception:
        return default


def clean_json(o):
    """把 NaN / Inf 之类非法值清洗成 null，保证输出合法 JSON"""
    if isinstance(o, float):
        return o if (o == o and abs(o) != float("inf")) else None
    if isinstance(o, dict):
        return {k: clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    return o


# ---------------------------------------------------------------- 新浪 hq 解析
def sina_hq(codes, headers=HEAD_FIN, ttl=15):
    """批量取 hq.sinajs.cn，返回 {code: [字段...]}"""
    out = {}
    codes = [c for c in dict.fromkeys(codes) if c]
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        try:
            txt = grab(url, headers, ttl=ttl, encoding="gbk")
        except Exception:
            continue
        for line in txt.split("\n"):
            m = re.match(r'var hq_str_([\w$.\-]+)="(.*)";', line.strip())
            if not m:
                continue
            out[m.group(1)] = m.group(2).split(",") if m.group(2) else []
    return out


def sina_hq_w(codes, ttl=15):
    """虚拟币专用 w.sinajs.cn"""
    url = "https://w.sinajs.cn/?list=" + ",".join(codes)
    try:
        txt = grab(url, HEAD_CRY, ttl=ttl, encoding="gbk")
    except Exception:
        return {}
    out = {}
    for line in txt.split("\n"):
        m = re.match(r'var hq_str_([\w$.\-]+)="(.*)";', line.strip())
        if m:
            out[m.group(1)] = m.group(2).split(",") if m.group(2) else []
    return out


def jsonp_payload(text):
    """从 jsonp 文本里抠出 JSON 主体"""
    t = text.replace("/*<script>location.href='//sina.com';</script>*/", "").strip()
    i = t.find("=(")
    if i < 0:
        i = t.find("= (")
    if i >= 0:
        t = t[i + 1:].strip()
    if t.endswith(";"):
        t = t[:-1]
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    return t


# ================================================================ 金投网（jijinhao）接口
def jjh_real(codes, ttl=15):
    """金投网实时行情，codes 形如 ['JO_38732', ...]"""
    url = "https://api.jijinhao.com/quoteCenter/realTime.htm?codes=" + ",".join(codes)
    txt = grab(url, HEAD_JJH, ttl=ttl, encoding="utf-8").strip()
    if txt.startswith("var quote_json"):
        txt = txt.split("=", 1)[1].strip().rstrip(";")
    try:
        js = json.loads(txt)
    except Exception:
        return {}
    js.pop("errorCode", None)
    js.pop("flag", None)
    return js


def jjh_kline(code, style, count=800, ttl=600):
    """金投网历史数据。style: 1 = 分时(5分钟)  3 = 日K"""
    url = ("https://api.jijinhao.com/history/quotejs.htm?codes=%s&style=%d"
           "&currentPage=1&pageSize=%d" % (code, style, count))
    txt = grab(url, HEAD_JJH, ttl=ttl, encoding="utf-8").strip()
    if txt.startswith("var quot_str"):
        txt = txt.split("=", 1)[1].strip().rstrip(";")
    try:
        js = json.loads(txt)
    except Exception:
        return []
    if not isinstance(js, list) or not js:
        return []
    return ((js[0] or {}).get("data")) or []


def jjh_candles(code, ttl=600):
    """金投网日K → [{d,o,c,h,l}]（接口为倒序，这里正序返回）"""
    rows = jjh_kline(code, 3, 1200, ttl)
    out = []
    for r in rows:
        q = (r or {}).get("quote") or {}
        d = str(q.get("q59") or "")[:10]
        o, c, h, l = fnum(q.get("q1")), fnum(q.get("q2")), fnum(q.get("q3")), fnum(q.get("q4"))
        if d and None not in (o, c, h, l):
            out.append({"d": d, "o": o, "c": c, "h": h, "l": l})
    out.reverse()
    return out


def jjh_min(code, ttl=120):
    """金投网分时（仅保留最后一个交易日）→ [{t,v}]"""
    rows = jjh_kline(code, 1, 400, ttl)
    out = []
    for r in rows:
        q = (r or {}).get("quote") or {}
        ts = str(q.get("q59") or "")
        v = fnum(q.get("q1"))
        if ts and v:
            out.append({"t": ts, "v": v})
    out.reverse()
    return last_day(out)


# ---------------------------------------------------------------- K 线周期工具
def last_day(pts):
    """只保留最后一个交易日，并把时间规整成 HH:MM"""
    if not pts:
        return []
    days = [str(p.get("t") or "")[:10] for p in pts]
    last = max(days)
    out = []
    for p, d in zip(pts, days):
        if d != last:
            continue
        t = str(p.get("t") or "")
        out.append({"t": t[11:16] if len(t) >= 16 else t, "v": p.get("v")})
    return out


def to_month(candles):
    """把日K聚合为月K"""
    buckets = {}
    for r in candles:
        d = str(r.get("d") or "")
        if len(d) < 7:
            continue
        key = d[:7]
        o, c, h, l = r.get("o"), r.get("c"), r.get("h"), r.get("l")
        if None in (o, c, h, l):
            continue
        if key not in buckets:
            buckets[key] = {"d": key + "-01", "o": o, "c": c, "h": h, "l": l}
        else:
            b = buckets[key]
            b["c"] = c
            b["h"] = max(b["h"], h)
            b["l"] = min(b["l"], l)
    return [buckets[k] for k in sorted(buckets)]


def period_wrap(day_fn, min_fn, code, typ):
    """统一包装：month 用日K聚合，day / min 直取"""
    if typ == "month":
        r = day_fn(code)
        if r.get("ok"):
            return {"ok": True, "type": "month", "code": code, "data": to_month(r["data"]), "error": None}
        return {"ok": False, "type": "month", "code": code, "data": [],
                "error": r.get("error") or "月K数据为空"}
    return min_fn(code) if typ == "min" else day_fn(code)


# ================================================================ 配置数据
FX_PAIRS = [
    # code, 显示名, base, quote, 分组（覆盖东方财富外汇中心 forex_all 全部品种）
    # ---- 美元 ----
    ("fx_susdjpy", "美元/日元", "USD", "JPY", "美元"),
    ("fx_susdeur", "美元/欧元", "USD", "EUR", "美元"),
    ("fx_susdgbp", "美元/英镑", "USD", "GBP", "美元"),
    ("fx_susdhkd", "美元/港元", "USD", "HKD", "美元"),
    ("fx_susdchf", "美元/瑞士法郎", "USD", "CHF", "美元"),
    ("fx_susdcad", "美元/加元", "USD", "CAD", "美元"),
    ("fx_susdaud", "美元/澳元", "USD", "AUD", "美元"),
    ("fx_susdnzd", "美元/新西兰元", "USD", "NZD", "美元"),
    ("fx_susdsgd", "美元/新加坡元", "USD", "SGD", "美元"),
    ("fx_susdkrw", "美元/韩元", "USD", "KRW", "美元"),
    ("fx_susdthb", "美元/泰铢", "USD", "THB", "美元"),
    ("fx_susdsar", "美元/沙特里亚尔", "USD", "SAR", "美元"),
    ("fx_susdrub", "美元/俄罗斯卢布", "USD", "RUB", "美元"),
    ("fx_susdinr", "美元/印度卢比", "USD", "INR", "美元"),
    ("fx_susdidr", "美元/印尼盾", "USD", "IDR", "美元"),
    ("fx_susdmxn", "美元/墨西哥比索", "USD", "MXN", "美元"),
    ("fx_susdbrl", "美元/巴西雷亚尔", "USD", "BRL", "美元"),
    ("fx_susdars", "美元/阿根廷比索", "USD", "ARS", "美元"),
    ("fx_susdsek", "美元/瑞典克朗", "USD", "SEK", "美元"),
    ("fx_susdnok", "美元/挪威克朗", "USD", "NOK", "美元"),
    ("fx_susddkk", "美元/丹麦克朗", "USD", "DKK", "美元"),
    ("fx_susdpln", "美元/波兰兹罗提", "USD", "PLN", "美元"),
    ("fx_susdhuf", "美元/匈牙利福林", "USD", "HUF", "美元"),
    ("fx_susdczk", "美元/捷克克朗", "USD", "CZK", "美元"),
    ("fx_susdtry", "美元/土耳其里拉", "USD", "TRY", "美元"),
    ("fx_susdzar", "美元/南非兰特", "USD", "ZAR", "美元"),
    # ---- 欧元 ----
    ("fx_seuraud", "欧元/澳元", "EUR", "AUD", "欧元"),
    ("fx_seurcad", "欧元/加元", "EUR", "CAD", "欧元"),
    ("fx_seurchf", "欧元/瑞士法郎", "EUR", "CHF", "欧元"),
    ("fx_seurczk", "欧元/捷克克朗", "EUR", "CZK", "欧元"),
    ("fx_seurdkk", "欧元/丹麦克朗", "EUR", "DKK", "欧元"),
    ("fx_seurgbp", "欧元/英镑", "EUR", "GBP", "欧元"),
    ("fx_seurhkd", "欧元/港元", "EUR", "HKD", "欧元"),
    ("fx_seurhuf", "欧元/匈牙利福林", "EUR", "HUF", "欧元"),
    ("fx_seurjpy", "欧元/日元", "EUR", "JPY", "欧元"),
    ("fx_seurnok", "欧元/挪威克朗", "EUR", "NOK", "欧元"),
    ("fx_seurnzd", "欧元/新西兰元", "EUR", "NZD", "欧元"),
    ("fx_seurpln", "欧元/波兰兹罗提", "EUR", "PLN", "欧元"),
    ("fx_seursek", "欧元/瑞典克朗", "EUR", "SEK", "欧元"),
    ("fx_seursgd", "欧元/新加坡元", "EUR", "SGD", "欧元"),
    ("fx_seurtry", "欧元/土耳其里拉", "EUR", "TRY", "欧元"),
    ("fx_seurusd", "欧元/美元", "EUR", "USD", "欧元"),
    ("fx_seurzar", "欧元/南非兰特", "EUR", "ZAR", "欧元"),
    # ---- 日元 ----
    ("fx_sjpyaud", "日元/澳元", "JPY", "AUD", "日元"),
    ("fx_sjpycad", "日元/加元", "JPY", "CAD", "日元"),
    ("fx_sjpychf", "日元/瑞士法郎", "JPY", "CHF", "日元"),
    ("fx_sjpyeur", "日元/欧元", "JPY", "EUR", "日元"),
    ("fx_sjpygbp", "日元/英镑", "JPY", "GBP", "日元"),
    ("fx_sjpyhkd", "日元/港元", "JPY", "HKD", "日元"),
    ("fx_sjpynzd", "日元/新西兰元", "JPY", "NZD", "日元"),
    ("fx_sjpysgd", "日元/新加坡元", "JPY", "SGD", "日元"),
    ("fx_sjpytry", "日元/土耳其里拉", "JPY", "TRY", "日元"),
    ("fx_sjpyusd", "日元/美元", "JPY", "USD", "日元"),
    ("fx_sjpyzar", "日元/南非兰特", "JPY", "ZAR", "日元"),
    # ---- 英镑 ----
    ("fx_sgbpaud", "英镑/澳元", "GBP", "AUD", "英镑"),
    ("fx_sgbpcad", "英镑/加元", "GBP", "CAD", "英镑"),
    ("fx_sgbpchf", "英镑/瑞士法郎", "GBP", "CHF", "英镑"),
    ("fx_sgbpeur", "英镑/欧元", "GBP", "EUR", "英镑"),
    ("fx_sgbphkd", "英镑/港元", "GBP", "HKD", "英镑"),
    ("fx_sgbpjpy", "英镑/日元", "GBP", "JPY", "英镑"),
    ("fx_sgbpnzd", "英镑/新西兰元", "GBP", "NZD", "英镑"),
    ("fx_sgbppln", "英镑/波兰兹罗提", "GBP", "PLN", "英镑"),
    ("fx_sgbpsgd", "英镑/新加坡元", "GBP", "SGD", "英镑"),
    ("fx_sgbpusd", "英镑/美元", "GBP", "USD", "英镑"),
    ("fx_sgbpzar", "英镑/南非兰特", "GBP", "ZAR", "英镑"),
    # ---- 澳元 ----
    ("fx_saudcad", "澳元/加元", "AUD", "CAD", "澳元"),
    ("fx_saudchf", "澳元/瑞士法郎", "AUD", "CHF", "澳元"),
    ("fx_saudeur", "澳元/欧元", "AUD", "EUR", "澳元"),
    ("fx_saudgbp", "澳元/英镑", "AUD", "GBP", "澳元"),
    ("fx_saudhkd", "澳元/港元", "AUD", "HKD", "澳元"),
    ("fx_saudjpy", "澳元/日元", "AUD", "JPY", "澳元"),
    ("fx_saudnzd", "澳元/新西兰元", "AUD", "NZD", "澳元"),
    ("fx_saudsgd", "澳元/新加坡元", "AUD", "SGD", "澳元"),
    ("fx_saudusd", "澳元/美元", "AUD", "USD", "澳元"),
    # ---- 加元 ----
    ("fx_scadaud", "加元/澳元", "CAD", "AUD", "加元"),
    ("fx_scadchf", "加元/瑞士法郎", "CAD", "CHF", "加元"),
    ("fx_scadeur", "加元/欧元", "CAD", "EUR", "加元"),
    ("fx_scadgbp", "加元/英镑", "CAD", "GBP", "加元"),
    ("fx_scadhkd", "加元/港元", "CAD", "HKD", "加元"),
    ("fx_scadjpy", "加元/日元", "CAD", "JPY", "加元"),
    ("fx_scadnzd", "加元/新西兰元", "CAD", "NZD", "加元"),
    ("fx_scadsgd", "加元/新加坡元", "CAD", "SGD", "加元"),
    ("fx_scadusd", "加元/美元", "CAD", "USD", "加元"),
    # ---- 瑞士法郎 ----
    ("fx_schfaud", "瑞士法郎/澳元", "CHF", "AUD", "瑞士法郎"),
    ("fx_schfcad", "瑞士法郎/加元", "CHF", "CAD", "瑞士法郎"),
    ("fx_schfeur", "瑞士法郎/欧元", "CHF", "EUR", "瑞士法郎"),
    ("fx_schfgbp", "瑞士法郎/英镑", "CHF", "GBP", "瑞士法郎"),
    ("fx_schfhkd", "瑞士法郎/港元", "CHF", "HKD", "瑞士法郎"),
    ("fx_schfjpy", "瑞士法郎/日元", "CHF", "JPY", "瑞士法郎"),
    ("fx_schfnzd", "瑞士法郎/新西兰元", "CHF", "NZD", "瑞士法郎"),
    ("fx_schfsgd", "瑞士法郎/新加坡元", "CHF", "SGD", "瑞士法郎"),
    ("fx_schfusd", "瑞士法郎/美元", "CHF", "USD", "瑞士法郎"),
    ("fx_schfzar", "瑞士法郎/南非兰特", "CHF", "ZAR", "瑞士法郎"),
    # ---- 新西兰元 ----
    ("fx_snzdaud", "新西兰元/澳元", "NZD", "AUD", "新西兰元"),
    ("fx_snzdcad", "新西兰元/加元", "NZD", "CAD", "新西兰元"),
    ("fx_snzdchf", "新西兰元/瑞士法郎", "NZD", "CHF", "新西兰元"),
    ("fx_snzdeur", "新西兰元/欧元", "NZD", "EUR", "新西兰元"),
    ("fx_snzdgbp", "新西兰元/英镑", "NZD", "GBP", "新西兰元"),
    ("fx_snzdhkd", "新西兰元/港元", "NZD", "HKD", "新西兰元"),
    ("fx_snzdjpy", "新西兰元/日元", "NZD", "JPY", "新西兰元"),
    ("fx_snzdsgd", "新西兰元/新加坡元", "NZD", "SGD", "新西兰元"),
    ("fx_snzdusd", "新西兰元/美元", "NZD", "USD", "新西兰元"),
    # ---- 港元 ----
    ("fx_shkdaud", "港元/澳元", "HKD", "AUD", "港元"),
    ("fx_shkdcad", "港元/加元", "HKD", "CAD", "港元"),
    ("fx_shkdchf", "港元/瑞士法郎", "HKD", "CHF", "港元"),
    ("fx_shkdeur", "港元/欧元", "HKD", "EUR", "港元"),
    ("fx_shkdgbp", "港元/英镑", "HKD", "GBP", "港元"),
    ("fx_shkdjpy", "港元/日元", "HKD", "JPY", "港元"),
    ("fx_shkdnzd", "港元/新西兰元", "HKD", "NZD", "港元"),
    ("fx_shkdsgd", "港元/新加坡元", "HKD", "SGD", "港元"),
    ("fx_shkdusd", "港元/美元", "HKD", "USD", "港元"),
    # ---- 新加坡元 ----
    ("fx_ssgdaud", "新加坡元/澳元", "SGD", "AUD", "新加坡元"),
    ("fx_ssgdcad", "新加坡元/加元", "SGD", "CAD", "新加坡元"),
    ("fx_ssgdchf", "新加坡元/瑞士法郎", "SGD", "CHF", "新加坡元"),
    ("fx_ssgdeur", "新加坡元/欧元", "SGD", "EUR", "新加坡元"),
    ("fx_ssgdgbp", "新加坡元/英镑", "SGD", "GBP", "新加坡元"),
    ("fx_ssgdhkd", "新加坡元/港元", "SGD", "HKD", "新加坡元"),
    ("fx_ssgdjpy", "新加坡元/日元", "SGD", "JPY", "新加坡元"),
    ("fx_ssgdnzd", "新加坡元/新西兰元", "SGD", "NZD", "新加坡元"),
    ("fx_ssgdusd", "新加坡元/美元", "SGD", "USD", "新加坡元"),
    # ---- 其他货币 ----
    ("fx_sczkeur", "捷克克朗/欧元", "CZK", "EUR", "其他货币"),
    ("fx_sczkusd", "捷克克朗/美元", "CZK", "USD", "其他货币"),
    ("fx_sdkkeur", "丹麦克朗/欧元", "DKK", "EUR", "其他货币"),
    ("fx_sdkkusd", "丹麦克朗/美元", "DKK", "USD", "其他货币"),
    ("fx_shufeur", "匈牙利福林/欧元", "HUF", "EUR", "其他货币"),
    ("fx_shufusd", "匈牙利福林/美元", "HUF", "USD", "其他货币"),
    ("fx_sinrusd", "印度卢比/美元", "INR", "USD", "其他货币"),
    ("fx_smxnusd", "墨西哥比索/美元", "MXN", "USD", "其他货币"),
    ("fx_snokeur", "挪威克朗/欧元", "NOK", "EUR", "其他货币"),
    ("fx_snokusd", "挪威克朗/美元", "NOK", "USD", "其他货币"),
    ("fx_splneur", "波兰兹罗提/欧元", "PLN", "EUR", "其他货币"),
    ("fx_splngbp", "波兰兹罗提/英镑", "PLN", "GBP", "其他货币"),
    ("fx_splnusd", "波兰兹罗提/美元", "PLN", "USD", "其他货币"),
    ("fx_ssarusd", "沙特里亚尔/美元", "SAR", "USD", "其他货币"),
    ("fx_ssekeur", "瑞典克朗/欧元", "SEK", "EUR", "其他货币"),
    ("fx_ssekusd", "瑞典克朗/美元", "SEK", "USD", "其他货币"),
    ("fx_sthbusd", "泰铢/美元", "THB", "USD", "其他货币"),
    ("fx_stryeur", "土耳其里拉/欧元", "TRY", "EUR", "其他货币"),
    ("fx_stryjpy", "土耳其里拉/日元", "TRY", "JPY", "其他货币"),
    ("fx_stryusd", "土耳其里拉/美元", "TRY", "USD", "其他货币"),
    ("fx_szarchf", "南非兰特/瑞士法郎", "ZAR", "CHF", "其他货币"),
    ("fx_szareur", "南非兰特/欧元", "ZAR", "EUR", "其他货币"),
    ("fx_szargbp", "南非兰特/英镑", "ZAR", "GBP", "其他货币"),
    ("fx_szarjpy", "南非兰特/日元", "ZAR", "JPY", "其他货币"),
    ("fx_szarusd", "南非兰特/美元", "ZAR", "USD", "其他货币"),
    # ---- 人民币 ----
    ("fx_saudcny", "澳元/人民币", "AUD", "CNY", "人民币"),
    ("fx_scadcny", "加元/人民币", "CAD", "CNY", "人民币"),
    ("fx_schfcny", "瑞士法郎/人民币", "CHF", "CNY", "人民币"),
    ("fx_scnyaed", "人民币/阿联酋迪拉姆", "CNY", "AED", "人民币"),
    ("fx_scnydkk", "人民币/丹麦克朗", "CNY", "DKK", "人民币"),
    ("fx_scnyhuf", "人民币/匈牙利福林", "CNY", "HUF", "人民币"),
    ("fx_scnykrw", "人民币/韩元", "CNY", "KRW", "人民币"),
    ("fx_scnymop", "人民币/澳门元", "CNY", "MOP", "人民币"),
    ("fx_scnymxn", "人民币/墨西哥比索", "CNY", "MXN", "人民币"),
    ("fx_scnymyr", "人民币/马来西亚林吉特", "CNY", "MYR", "人民币"),
    ("fx_scnynok", "人民币/挪威克朗", "CNY", "NOK", "人民币"),
    ("fx_scnypln", "人民币/波兰兹罗提", "CNY", "PLN", "人民币"),
    ("fx_scnyrub", "人民币/俄罗斯卢布", "CNY", "RUB", "人民币"),
    ("fx_scnysar", "人民币/沙特里亚尔", "CNY", "SAR", "人民币"),
    ("fx_scnysek", "人民币/瑞典克朗", "CNY", "SEK", "人民币"),
    ("fx_scnythb", "人民币/泰铢", "CNY", "THB", "人民币"),
    ("fx_scnytry", "人民币/土耳其里拉", "CNY", "TRY", "人民币"),
    ("fx_scnyzar", "人民币/南非兰特", "CNY", "ZAR", "人民币"),
    ("fx_seurcny", "欧元/人民币", "EUR", "CNY", "人民币"),
    ("fx_sgbpcny", "英镑/人民币", "GBP", "CNY", "人民币"),
    ("fx_shkdcny", "港元/人民币", "HKD", "CNY", "人民币"),
    ("fx_sjpycny", "日元/人民币", "JPY", "CNY", "人民币"),
    ("fx_snzdcny", "新西兰元/人民币", "NZD", "CNY", "人民币"),
    ("fx_ssgdcny", "新加坡元/人民币", "SGD", "CNY", "人民币"),
    ("fx_susdcny", "美元/人民币", "USD", "CNY", "人民币"),
    # ---- 离岸人民币 ----
    ("fx_saudcnh", "澳元/离岸人民币", "AUD", "CNH", "离岸人民币"),
    ("fx_scadcnh", "加元/离岸人民币", "CAD", "CNH", "离岸人民币"),
    ("fx_schfcnh", "瑞士法郎/离岸人民币", "CHF", "CNH", "离岸人民币"),
    ("fx_scnhaud", "离岸人民币/澳元", "CNH", "AUD", "离岸人民币"),
    ("fx_scnhcad", "离岸人民币/加元", "CNH", "CAD", "离岸人民币"),
    ("fx_scnhchf", "离岸人民币/瑞士法郎", "CNH", "CHF", "离岸人民币"),
    ("fx_scnheur", "离岸人民币/欧元", "CNH", "EUR", "离岸人民币"),
    ("fx_scnhgbp", "离岸人民币/英镑", "CNH", "GBP", "离岸人民币"),
    ("fx_scnhhkd", "离岸人民币/港元", "CNH", "HKD", "离岸人民币"),
    ("fx_scnhjpy", "离岸人民币/日元", "CNH", "JPY", "离岸人民币"),
    ("fx_scnhnzd", "离岸人民币/新西兰元", "CNH", "NZD", "离岸人民币"),
    ("fx_scnhsgd", "离岸人民币/新加坡元", "CNH", "SGD", "离岸人民币"),
    ("fx_scnhusd", "离岸人民币/美元", "CNH", "USD", "离岸人民币"),
    ("fx_seurcnh", "欧元/离岸人民币", "EUR", "CNH", "离岸人民币"),
    ("fx_sgbpcnh", "英镑/离岸人民币", "GBP", "CNH", "离岸人民币"),
    ("fx_shkdcnh", "港元/离岸人民币", "HKD", "CNH", "离岸人民币"),
    ("fx_sjpycnh", "日元/离岸人民币", "JPY", "CNH", "离岸人民币"),
    ("fx_snzdcnh", "新西兰元/离岸人民币", "NZD", "CNH", "离岸人民币"),
    ("fx_ssgdcnh", "新加坡元/离岸人民币", "SGD", "CNH", "离岸人民币"),
    ("fx_susdcnh", "美元/离岸人民币", "USD", "CNH", "离岸人民币"),
    ("DINIW", "美元指数", "USD", "IDX", "美元"),
]

# 债券：美国 / 中国 全期限 + 其他国家 10 年期
BOND_US = [("us1mt", "1个月"), ("us2mt", "2个月"), ("us3mt", "3个月"), ("us4mt", "4个月"),
           ("us6mt", "6个月"), ("us1yt", "1年"), ("us2yt", "2年"), ("us3yt", "3年"),
           ("us5yt", "5年"), ("us7yt", "7年"), ("us10yt", "10年"), ("us20yt", "20年"),
           ("us30yt", "30年")]
BOND_CN = [("cn1yt", "1年"), ("cn2yt", "2年"), ("cn3yt", "3年"), ("cn5yt", "5年"),
           ("cn7yt", "7年"), ("cn10yt", "10年"), ("cn15yt", "15年"), ("cn20yt", "20年"),
           ("cn30yt", "30年")]
BOND_OTHER = [("jp", "日本"), ("de", "德国"), ("gb", "英国"), ("fr", "法国"), ("it", "意大利"),
              ("es", "西班牙"), ("nl", "荷兰"), ("pt", "葡萄牙"), ("gr", "希腊"), ("ca", "加拿大"),
              ("au", "澳大利亚"), ("nz", "新西兰"), ("ch", "瑞士"), ("se", "瑞典"), ("no", "挪威"),
              ("dk", "丹麦"), ("in", "印度"), ("kr", "韩国"), ("sg", "新加坡"), ("hk", "中国香港"),
              ("tw", "中国台湾"), ("th", "泰国"), ("my", "马来西亚"), ("ph", "菲律宾"),
              ("id", "印度尼西亚"), ("vn", "越南"), ("mx", "墨西哥"), ("br", "巴西"),
              ("tr", "土耳其"), ("za", "南非"), ("ru", "俄罗斯"), ("pl", "波兰"),
              ("il", "以色列"), ("eg", "埃及"), ("co", "哥伦比亚")]

# A 股 / 港股（新浪）
IDX_CN = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sh000300", "沪深300"),
          ("sz399006", "创业板指"), ("sh000688", "科创50"), ("sz399005", "中小100")]
IDX_HK = [("rt_hkHSI", "恒生指数"), ("rt_hkHSCEI", "恒生中国企业指数"),
          ("rt_hkHSCCI", "恒生香港中资企业指数")]

# 全球股指：金投网（有日K/分时）
# (金投网代码, 名称, 国家/地区, 区域分组)
# 注：中国香港（恒生系列）由新浪 IDX_HK 提供并归入「中国」分组，
#     中国台湾加权指数同样归入「中国」分组，此处不再重复收录。
IDX_GL = [
    ("JO_61864", "道琼斯指数", "美国", "美洲"),
    ("JO_38733", "纳斯达克指数", "美国", "美洲"),
    ("JO_38732", "标普500指数", "美国", "美洲"),
    ("JO_52363", "加拿大 S&P/TSX 综合指数", "加拿大", "美洲"),
    ("JO_10349", "墨西哥 BOLSA 指数", "墨西哥", "美洲"),
    ("JO_10194", "巴西 BOVESPA 指数", "巴西", "美洲"),
    ("JO_57199", "日经225指数", "日本", "亚太"),
    ("JO_38742", "韩国 KOSPI 指数", "韩国", "亚太"),
    ("JO_52361", "中国台湾加权指数", "中国台湾", "中国"),
    ("JO_55127", "富时新加坡海峡时报指数", "新加坡", "亚太"),
    ("JO_38745", "印度孟买 SENSEX 30", "印度", "亚太"),
    ("JO_57204", "印尼雅加达综合指数", "印度尼西亚", "亚太"),
    ("JO_38747", "越南指数", "越南", "亚太"),
    ("JO_38750", "巴基斯坦卡拉奇指数", "巴基斯坦", "亚太"),
    ("JO_284178", "英国富时100", "英国", "欧洲"),
    ("JO_284188", "德国 DAX 30", "德国", "欧洲"),
    ("JO_38755", "法国 CAC 40", "法国", "欧洲"),
    ("JO_284184", "欧洲斯托克50", "欧元区", "欧洲"),
    ("JO_55111", "富时意大利 MIB", "意大利", "欧洲"),
    ("JO_38756", "西班牙 IBEX 35", "西班牙", "欧洲"),
    ("JO_38758", "荷兰 AEX 指数", "荷兰", "欧洲"),
    ("JO_284185", "瑞士 SMI 指数", "瑞士", "欧洲"),
    ("JO_10258", "俄罗斯 MICEX 10", "俄罗斯", "欧洲"),
]

# 美股三大指数（金投网，含日K）
US_IDX = [("JO_61864", ".DJI", "道琼斯工业指数"),
          ("JO_38733", ".IXIC", "纳斯达克综合指数"),
          ("JO_38732", ".INX", "标普500指数")]
# 指数期货（新浪外盘）
US_FUT = [("YM", "道指期货（连续）"), ("NQ", "纳指期货（连续）"), ("ES", "标普500期货（连续）")]

CRYPTO = [
    ("btcbtcusd", "BTC", "比特币"), ("btcethusd", "ETH", "以太坊"),
    ("btcbnbusd", "BNB", "币安币"), ("btcsolusd", "SOL", "索拉纳"),
    ("btcxrpusd", "XRP", "瑞波币"), ("btcadausd", "ADA", "艾达币"),
    ("btcavaxusd", "AVAX", "雪崩协议"), ("btctrxusd", "TRX", "波场"),
    ("btcuniusd", "UNI", "Uniswap"), ("btcmaticusd", "POL", "Polygon"),
    ("btcnearusd", "NEAR", "NEAR 协议"), ("btcpepeusd", "PEPE", "佩佩币"),
]
# 新浪代码 -> Gate.io 现货币对（用于历史 K 线，新浪不提供虚拟币 K 线）
GATE_PAIR = {sym: disp for sym, disp, _ in CRYPTO}

FUTURES = [
    ("GC", "纽约黄金", "贵金属", "美元/盎司"),
    ("SI", "纽约白银", "贵金属", "美元/盎司"),
    ("CL", "纽约原油 (WTI)", "能源", "美元/桶"),
    ("OIL", "布伦特原油", "能源", "美元/桶"),
    ("NG", "美国天然气", "能源", "美元/百万英热"),
    ("HO", "取暖油", "能源", "美元/加仑"),
    ("HG", "纽约铜", "有色金属", "美分/磅"),
    ("NID", "伦敦镍", "有色金属", "美元/吨"),
    ("C", "玉米", "农产品", "美分/蒲式耳"),
    ("S", "大豆", "农产品", "美分/蒲式耳"),
    ("W", "小麦", "农产品", "美分/蒲式耳"),
    ("SM", "豆粕", "农产品", "美元/短吨"),
    ("BO", "豆油", "农产品", "美分/磅"),
    ("KC", "咖啡", "农产品", "美分/磅"),
    ("CC", "可可", "农产品", "美元/吨"),
    ("CT", "棉花", "农产品", "美分/磅"),
    ("OJ", "橙汁", "农产品", "美分/磅"),
    ("BTC", "比特币期货", "数字货币", "美元"),
]

RMB_IDX_ORDER = [("cfetsIndexRate", "CFETS 人民币汇率指数", 3),
                 ("bisIndexRate", "BIS 货币篮子人民币汇率指数", 2),
                 ("sdrIndexRate", "SDR 货币篮子人民币汇率指数", 1)]

# 央行利率：主要经济体排前面（金投网数据源）
RATES_ORDER = ["美国", "欧元区", "中国", "日本", "英国", "瑞士", "加拿大", "澳大利亚", "新西兰",
               "韩国", "印度", "中国香港", "中国台湾", "新加坡", "瑞典", "挪威", "巴西",
               "俄罗斯", "墨西哥", "南非", "印尼", "泰国", "马来西亚", "菲律宾", "波兰",
               "捷克", "匈牙利", "智利", "哥伦比亚", "土耳其", "以色列", "沙特阿拉伯", "埃及"]


# ================================================================ 汇率
def fetch_fx():
    codes = [c for c, _, _, _, _ in FX_PAIRS]
    hq = sina_hq(codes, HEAD_FX, ttl=15)
    items = []
    for code, name, base, quote, grp in FX_PAIRS:
        v = hq.get(code)
        if not v or len(v) < 9:
            items.append({"code": code, "name": name, "base": base, "quote": quote,
                          "group": grp, "ok": False})
            continue
        price = fnum(v[8]) or fnum(v[1])
        pct = fnum(v[10], 0.0) if len(v) > 10 else 0.0
        chg = fnum(v[11], 0.0) if len(v) > 11 else 0.0
        prev = fnum(v[3]) if len(v) > 3 else None
        # 新浪部分品种"涨跌额"与"涨跌幅"不自洽，用涨跌幅反推昨收
        if price and pct is not None and abs(pct) > 1e-9:
            pc = price / (1 + pct / 100.0)
            chg = price - pc
            prev = pc
        if not price:
            items.append({"code": code, "name": name, "base": base, "quote": quote,
                          "group": grp, "ok": False})
            continue
        items.append({
            "code": code, "name": name, "base": base, "quote": quote, "group": grp,
            "ok": True, "price": price, "change": chg, "pct": pct,
            "prev": prev, "open": fnum(v[5]), "high": fnum(v[6]), "low": fnum(v[7]),
            "w52h": fnum(v[14]) if len(v) > 14 else None,
            "w52l": fnum(v[15]) if len(v) > 15 else None,
            "time": (v[17] + " " + v[0]) if len(v) > 17 else (v[0] if v else ""),
        })
    ok = [x for x in items if x["ok"]]
    return {"ok": len(ok) > 0, "count": len(ok), "items": items,
            "source": "新浪财经 · 外汇",
            "error": None if ok else "新浪外汇接口无数据返回"}


def fetch_fx_kline_day(code):
    url = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20a=/"
           "NewForexService.getDayKLine?symbol=" + code)
    txt = grab(url, HEAD_FX, ttl=180)
    body = jsonp_payload(txt)
    try:
        raw = json.loads(body) if body.strip().startswith('"') else body
    except Exception:
        raw = body
    if isinstance(raw, str):
        data = []
        for r in [x for x in raw.split("|") if x.strip()]:
            p = r.split(",")
            if len(p) >= 5:
                data.append({"d": p[0], "o": fnum(p[1]), "l": fnum(p[2]),
                             "h": fnum(p[3]), "c": fnum(p[4])})
        data = data[-400:]
        return {"ok": bool(data), "type": "day", "code": code, "data": data,
                "error": None if data else "日K数据为空"}
    return {"ok": False, "type": "day", "code": code, "data": [], "error": "日K解析失败"}


def fetch_fx_kline_min(code):
    url = ("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20a=/"
           "NewForexService.getMinKLine?symbol=" + code + "&scale=5&datalen=400")
    txt = grab(url, HEAD_FX, ttl=60)
    body = jsonp_payload(txt)
    try:
        data = json.loads(body)
    except Exception:
        m = re.search(r"\[.*\]", body, re.S)
        data = json.loads(m.group(0)) if m else []
    out = last_day([{"t": x.get("d"), "v": fnum(x.get("c"))} for x in data if fnum(x.get("c"))])
    return {"ok": bool(out), "type": "min", "code": code, "data": out,
            "error": None if out else "分时数据为空"}


# ================================================================ 人民币汇率指数
def fetch_rmbidx():
    res = {"ok": False, "error": None, "asOf": None, "current": [], "series": {},
           "source": "中国货币网 · 人民币汇率指数"}
    try:
        js = json.loads(grab("https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/fx/rmb-idx.json",
                             HEAD_CM, ttl=1800))
        d = js.get("data", {})
        vo = d.get("vo", {}) or {}
        res["asOf"] = d.get("date")
        for key, label, tid in RMB_IDX_ORDER:
            res["current"].append({"key": key, "label": label, "type": tid,
                                   "value": fnum(vo.get(key)),
                                   "bp": fnum(vo.get(key[:-4] + "Bp"))})
    except Exception as e:
        res["error"] = "中国货币网当前值获取失败：%s" % e

    def _hist(tid):
        url = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-fx/RmbIdxChrt?indexType=%d" % tid
        try:
            js = json.loads(grab(url, HEAD_CM, ttl=1800))
            rec = js.get("records") or []
            return [[r.get("showDateCn"), fnum(r.get("indexRate"))] for r in rec if r.get("indexRate")]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {tid: ex.submit(_hist, tid) for _, _, tid in RMB_IDX_ORDER}
        for key, label, tid in RMB_IDX_ORDER:
            res["series"][label] = futs[tid].result()
    if not res["current"] and not any(res["series"].values()):
        res["error"] = res["error"] or "人民币汇率指数获取失败"
    res["ok"] = bool(res["current"]) or any(res["series"].values())
    return res


# ================================================================ 国债收益率
_BOND_HQ = {}


def _bond_item(code, label, country):
    v = _BOND_HQ.get(code)
    if not v or len(v) < 6:
        return {"code": code, "label": label, "country": country, "ok": False}
    price = fnum(v[3])
    prev = fnum(v[2])
    chg = fnum(v[8]) if len(v) > 8 else (
        price - prev if price is not None and prev is not None else None)
    pct = fnum(v[7]) if len(v) > 7 else None
    if chg is None and price is not None and prev is not None:
        chg = price - prev
    return {"code": code, "label": label, "country": country, "ok": True,
            "yield": price, "prev": prev, "open": fnum(v[1]), "high": fnum(v[4]),
            "low": fnum(v[5]), "change": chg, "pct": pct,
            "bp": (chg * 100) if chg is not None else None,
            "maturity": v[14] if len(v) > 14 else "",
            "coupon": fnum(v[15]) if len(v) > 15 else None,
            "bondprice": fnum(v[17]) if len(v) > 17 else None,
            "date": v[12] if len(v) > 12 else "", "time": v[13] if len(v) > 13 else ""}


def fetch_bonds():
    global _BOND_HQ
    codes = (["globalbd_" + c for c, _ in BOND_US] + ["globalbd_" + c for c, _ in BOND_CN] +
             ["globalbd_%s10yt" % c for c, _ in BOND_OTHER])
    _BOND_HQ = sina_hq(codes, HEAD_BOND, ttl=20)
    us = [_bond_item("globalbd_" + c, l, "美国") for c, l in BOND_US]
    cn = [_bond_item("globalbd_" + c, l, "中国") for c, l in BOND_CN]
    other = [_bond_item("globalbd_%s10yt" % c, "10年期", nm) for c, nm in BOND_OTHER]

    def curve(rows):
        return [[r["label"], r["yield"]] for r in rows if r["ok"] and r["yield"] is not None]

    good = [x for x in us + cn + other if x["ok"]]
    return {"ok": bool(good), "us": us, "cn": cn, "other": other,
            "curve": {"美国": curve(us), "中国": curve(cn)},
            "source": "新浪财经 · 全球国债",
            "error": None if good else "新浪全球国债接口无数据"}


def _bond_sym(code):
    """新浪国债日K/分时接口只认 us5yt 这类裸代码，需去掉前端使用的 globalbd_ 前缀"""
    return str(code or "").strip().lower().replace("globalbd_", "")


def fetch_bond_kline_day(code):
    sym = _bond_sym(code)
    url = "https://bond.finance.sina.com.cn/hq/gb/daily?symbol=" + sym
    js = json.loads(grab(url, HEAD_BOND, ttl=300))
    rows = (js.get("result") or {}).get("data") or []
    data = [{"d": r.get("d"), "o": fnum(r.get("o")), "l": fnum(r.get("l")),
             "h": fnum(r.get("h")), "c": fnum(r.get("c"))} for r in rows]
    return {"ok": bool(data), "type": "day", "code": sym, "data": data,
            "error": None if data else "国债日K为空"}


def fetch_bond_kline_min(code):
    sym = _bond_sym(code)
    url = "https://bond.finance.sina.com.cn/hq/gb/min?symbol=" + sym
    js = json.loads(grab(url, HEAD_BOND, ttl=60))
    rows = (js.get("result") or {}).get("data") or []
    data = []
    last_d = ""
    for r in rows:
        if not isinstance(r, list) or len(r) < 2:
            continue
        if len(r) >= 5 and r[4]:
            last_d = r[4]
        val = fnum(r[3]) if len(r) >= 5 else fnum(r[1])
        if not val:
            val = fnum(r[1])
        if val:
            data.append({"t": (last_d + " " + str(r[0])) if last_d else str(r[0]), "v": val})
    data = last_day(data)
    return {"ok": bool(data), "type": "min", "code": sym, "data": data,
            "error": None if data else "国债分时为空"}


# ================================================================ 美国国债（Debt to the Penny）
def _d2(s):
    """YYYY-MM-DD -> date"""
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except Exception:
        return None


def _attach_cmp(rows):
    """就地附加三类对比：dod 较上一交易日 / mom 较上月同期 / yoy 较上年同期。
    每类给出绝对值、百分比、参照日日期；无参照数据时为 None。"""
    asc = sorted(rows, key=lambda x: x["date"])
    dates = [r["date"] for r in asc]
    idx = {r["date"]: i for i, r in enumerate(asc)}

    def back(i, days):
        """i 往前 days 天，取日期 <= 目标日的最近一条（跨周末自动落到上一交易日）"""
        t = _d2(asc[i]["date"])
        if t is None:
            return None
        target = (t - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        j = bisect.bisect_right(dates, target) - 1
        return j if j >= 0 else None

    for r in asc:
        i = idx.get(r["date"])
        cur = r.get("total")
        if i is None or cur is None:
            for k in ("dod", "mom", "yoy"):
                r[k] = r[k + "P"] = r[k + "Date"] = None
            continue
        for k, j in (("dod", i - 1), ("mom", back(i, 30)), ("yoy", back(i, 365))):
            b = asc[j].get("total") if (j is not None and j >= 0) else None
            if b is not None:
                r[k] = cur - b
                r[k + "P"] = ((cur - b) / b * 100) if b else None
                r[k + "Date"] = asc[j]["date"]
            else:
                r[k] = r[k + "P"] = r[k + "Date"] = None


def debt_rows(ttl=1800):
    """全量逐日数据（倒序，最新在前），每行附带环比 / 月比 / 年比"""
    def _p():
        url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/"
               "debt_to_penny?sort=-record_date&page%5Bsize%5D=10000"
               "&fields=record_date,tot_pub_debt_out_amt,debt_held_public_amt,intragov_hold_amt")
        js = json.loads(grab(url, HEAD_TRS, ttl=ttl))
        out = []
        for r in js.get("data") or []:
            if not r.get("record_date"):
                continue
            out.append({
                "date": r.get("record_date"),
                "total": fnum(r.get("tot_pub_debt_out_amt")),
                "public": fnum(r.get("debt_held_public_amt")),
                "intragov": fnum(r.get("intragov_hold_amt")),
            })
        _attach_cmp(out)
        out.sort(key=lambda x: x["date"] or "", reverse=True)
        return out
    try:
        return cached("debt_all", ttl, _p)
    except Exception:
        return peek("debt_all") or []


def fetch_debt(page=1, size=20):
    rows = debt_rows()
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    page = min(max(1, page), pages)
    latest = rows[0] if rows else None
    # 最近约 10 年的走势数据（正序），供前端图表一次取用
    return {"ok": bool(rows), "total": total, "page": page, "size": size, "pages": pages,
            "rows": rows[(page - 1) * size: page * size],
            "latest": latest,
            "oldest": rows[-1]["date"] if rows else None,
            "series": [{"date": r["date"], "total": r["total"], "public": r["public"],
                        "intragov": r["intragov"]} for r in (rows[:2600])[::-1]],
            "compare": latest and {
                "dod": latest.get("dod"), "dodP": latest.get("dodP"), "dodDate": latest.get("dodDate"),
                "mom": latest.get("mom"), "momP": latest.get("momP"), "momDate": latest.get("momDate"),
                "yoy": latest.get("yoy"), "yoyP": latest.get("yoyP"), "yoyDate": latest.get("yoyDate"),
            },
            "source": "U.S. Treasury · Debt to the Penny",
            "error": None if rows else "美国财政部接口无数据"}


def _debt_line(r):
    """一行导出数据（含对比列）"""
    return [r["date"], r["total"], r["public"], r["intragov"],
            (r["total"] / 1e12) if r["total"] else None,
            r.get("dod"), (round(r["dodP"], 4) if r.get("dodP") is not None else None), r.get("dodDate"),
            r.get("mom"), (round(r["momP"], 4) if r.get("momP") is not None else None), r.get("momDate"),
            r.get("yoy"), (round(r["yoyP"], 4) if r.get("yoyP") is not None else None), r.get("yoyDate")]


def export_debt():
    """生成 Excel（缺 openpyxl 时回退 CSV）"""
    rows = debt_rows()[::-1]          # 导出按时间正序
    header = ["日期", "国债总额（美元）", "公众持有（美元）", "政府内部持有（美元）",
              "国债总额（万亿美元）",
              "较上一交易日（美元）", "较上一交易日（%）", "参照日",
              "较上月同期（美元）", "较上月同期（%）", "参照日",
              "较上年同期（美元）", "较上年同期（%）", "参照日"]
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "美国国债 Debt to the Penny"
        ws.append(header)
        fill = PatternFill("solid", fgColor="1F4E79")
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF", size=11)
            c.fill = fill
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for r in rows:
            ws.append(_debt_line(r))
        money = '#,##0.00'
        for i in range(2, len(rows) + 2):
            for col in (2, 3, 4, 6, 9, 12):
                ws.cell(i, col).number_format = money
            for col in (7, 10, 13):
                ws.cell(i, col).number_format = '0.0000'
            ws.cell(i, 5).number_format = '0.0000'
        for col, w in zip("ABCDEFGHIJKLMN", [13, 22, 22, 24, 19, 21, 18, 13, 21, 18, 13, 21, 18, 13]):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        bio = io.BytesIO()
        wb.save(bio)
        return (bio.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "美国国债数据_DebtToThePenny_%s.xlsx" % time.strftime("%Y%m%d"))
    except ImportError:
        buf = io.StringIO()
        buf.write("\ufeff")
        w = csv.writer(buf)
        w.writerow(header)
        for r in rows:
            line = _debt_line(r)
            w.writerow([("" if v is None else v) for v in line])
        return (buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                "美国国债数据_%s.csv" % time.strftime("%Y%m%d"))


# ================================================================ 各国央行利率（金投网）
def _rate_num(s):
    if s is None:
        return None
    return fnum(str(s).replace("%", "").replace(",", "").strip())


def fetch_rates():
    raw = []
    err = None
    for pg in (1, 2):
        try:
            txt = grab("https://calendar.cngold.org/rate/readmore/%d.html" % pg,
                       HEAD_CNG, ttl=3600, encoding="utf-8").strip()
            js = json.loads(txt)
            if isinstance(js, list):
                raw += js
        except Exception as e:
            err = "%s" % e
    items = []
    seen = set()
    for r in raw:
        country = (r.get("country") or "").strip()
        if not country or country in seen:
            continue
        seen.add(country)
        cur = _rate_num(r.get("currentValue"))
        prev = _rate_num(r.get("previousValue"))
        cp = (r.get("changePoint") or "").strip()
        bp = None
        if cp and cp not in ("持平", "-", "—"):
            bp = fnum(cp.replace("+", "").replace("%", ""))
        ts = fnum(r.get("publishDate"))
        date = time.strftime("%Y-%m-%d", time.localtime(ts / 1000)) if ts else ""
        items.append({"country": country, "name": (r.get("interestRateName") or "").strip(),
                      "cur": cur, "prev": prev, "bp": bp, "bpText": cp or "—",
                      "date": date, "ok": cur is not None})
    rank = {c: i for i, c in enumerate(RATES_ORDER)}
    items.sort(key=lambda x: (rank.get(x["country"], 999), x["country"]))
    good = [x for x in items if x["ok"]]
    return {"ok": bool(good), "items": items, "count": len(good),
            "source": "金投网 · 财经日历 各国央行基准利率",
            "error": None if good else (err or "金投网央行利率接口无数据")}


# ================================================================ 世界股市
def fetch_indices():
    codes = [c for c, _, _, _ in IDX_GL]
    jh = jjh_real(codes, ttl=20)

    def gl_item(code, name, country, region):
        v = jh.get(code) or {}
        price = fnum(v.get("q1"))
        if price is None:
            return {"code": code, "name": name, "country": country, "region": region, "ok": False}
        return {"code": code, "name": name, "country": country, "region": region, "ok": True,
                "price": price, "prev": fnum(v.get("q2")), "open": fnum(v.get("q63")),
                "high": fnum(v.get("q3")), "low": fnum(v.get("q4")),
                "change": fnum(v.get("q70")), "pct": fnum(v.get("q80")),
                "digits": int(fnum(v.get("digits"), 2) or 2),
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime((v.get("time") or 0) / 1000))
                if v.get("time") else "",
                "hasKline": True}

    # 中国内地 / 中国香港（新浪，实时行情更贴合本地口径）
    hq_cn = sina_hq([c for c, _ in IDX_CN] + [c for c, _ in IDX_HK], HEAD_FIN, ttl=20)

    def cn_item(code, name):
        v = hq_cn.get(code)
        if not v or len(v) < 6:
            return {"code": code, "name": name, "country": "中国", "region": "中国", "ok": False,
                    "hasKline": True}
        if code.startswith("rt_hk"):
            price, prev = fnum(v[6]), fnum(v[3])
            chg = fnum(v[7]) if len(v) > 7 else (price - prev if (price is not None and prev) else None)
            pct = fnum(v[8]) if len(v) > 8 else None
            if pct is None and price is not None and prev:
                pct = (price - prev) / prev * 100
            return {"code": code, "name": name, "country": "中国香港", "region": "中国", "ok": price is not None,
                    "price": price, "change": chg, "pct": pct, "high": fnum(v[4]), "low": fnum(v[5]),
                    "prev": prev, "digits": 2, "time": v[17] if len(v) > 17 else "", "hasKline": True}
        price, prev = fnum(v[3]), fnum(v[2])
        return {"code": code, "name": name, "country": "中国", "region": "中国", "ok": price is not None,
                "price": price, "prev": prev,
                "change": (price - prev) if (price is not None and prev) else None,
                "pct": ((price - prev) / prev * 100) if (price is not None and prev) else None,
                "high": fnum(v[4]), "low": fnum(v[5]), "digits": 2,
                "time": (v[30] + " " + v[31]) if len(v) > 31 else "", "hasKline": True}

    cn = [cn_item(c, n) for c, n in IDX_CN]
    hk = [cn_item(c, n) for c, n in IDX_HK]
    gl = [gl_item(c, n, ct, rg) for c, n, ct, rg in IDX_GL]
    good = [x for x in cn + hk + gl if x.get("ok")]
    return {"ok": bool(good), "cn": cn, "hk": hk, "global": gl,
            "source": "金投网 · 全球指数 / 新浪财经",
            "error": None if good else "全球股指接口无数据"}


def fetch_index_kline_day(code):
    if code.startswith("JO_"):
        data = jjh_candles(code)
        return {"ok": bool(data), "type": "day", "code": code, "data": data,
                "error": None if data else "该指数日K数据为空"}
    # A 股 / 港股（腾讯日K）
    sym = code.replace("rt_hk", "hk").replace("sh", "sh").replace("sz", "sz")
    if code.startswith("rt_hk"):
        sym = "hk" + code[5:]
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,800,qfq" % sym)
    js = json.loads(grab(url, HEAD_TX, ttl=300))
    d = ((js.get("data") or {}).get(sym)) or {}
    arr = d.get("qfqday") or d.get("day") or []
    data = [{"d": x[0], "o": fnum(x[1]), "c": fnum(x[2]), "h": fnum(x[3]), "l": fnum(x[4])}
            for x in arr if len(x) >= 5 and fnum(x[2]) is not None]
    return {"ok": bool(data), "type": "day", "code": code, "data": data,
            "error": None if data else "该指数日K数据为空"}


def fetch_index_kline_min(code):
    if code.startswith("JO_"):
        out = jjh_min(code)
        return {"ok": bool(out), "type": "min", "code": code, "data": out,
                "error": None if out else "该指数分时数据为空"}
    if code.startswith("rt_hk"):
        sym = "hk" + code[5:]
    else:
        sym = code
    url = ("https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % sym)
    js = json.loads(grab(url, HEAD_TX, ttl=60))
    d = ((js.get("data") or {}).get(sym)) or {}
    arr = ((d.get("data") or {}).get("data")) or []
    out = []
    for line in arr:
        p = str(line).split(" ")
        if len(p) >= 2 and fnum(p[1]):
            out.append({"t": p[0][:2] + ":" + p[0][2:4], "v": fnum(p[1])})
    return {"ok": bool(out), "type": "min", "code": code, "data": out,
            "error": None if out else "该指数分时数据为空"}


# ================================================================ 美股三大指数 + 指数期货
def fetch_us():
    codes = [c for c, _, _ in US_IDX]
    jh = jjh_real(codes, ttl=20)
    idx = []
    for code, sym, name in US_IDX:
        v = jh.get(code) or {}
        price = fnum(v.get("q1"))
        idx.append({"code": code, "sym": sym, "name": name, "ok": price is not None,
                    "price": price, "prev": fnum(v.get("q2")), "open": fnum(v.get("q63")),
                    "high": fnum(v.get("q3")), "low": fnum(v.get("q4")),
                    "change": fnum(v.get("q70")), "pct": fnum(v.get("q80")),
                    "digits": int(fnum(v.get("digits"), 2) or 2),
                    "time": time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime((v.get("time") or 0) / 1000))
                    if v.get("time") else ""})
    hq = sina_hq(["hf_" + s for s, _ in US_FUT], HEAD_FUT, ttl=15)
    fut = []
    for sym, name in US_FUT:
        v = hq.get("hf_" + sym)
        if not v or len(v) < 9 or not str(v[0]).strip():
            fut.append({"symbol": sym, "name": name, "ok": False})
            continue
        price, prev = fnum(v[0]), fnum(v[7])
        fut.append({"symbol": sym, "name": name, "ok": price is not None,
                    "price": price, "prev": prev, "open": fnum(v[8]),
                    "change": (price - prev) if (price is not None and prev) else None,
                    "pct": ((price - prev) / prev * 100) if (price is not None and prev) else None,
                    "high": fnum(v[4]), "low": fnum(v[5]),
                    "time": (str(v[12]) + " " + str(v[6])) if len(v) > 12 else str(v[6])})
    good = [x for x in idx if x["ok"]]
    return {"ok": bool(good) or any(x["ok"] for x in fut), "idx": idx, "fut": fut,
            "source": "金投网 · 美股指数 / 新浪财经 · CME 指数期货",
            "error": None if good else "美股指数接口无数据"}


# ================================================================ 虚拟币
def fetch_crypto():
    codes = ["btc_" + s for s, _, _ in CRYPTO]
    hq = sina_hq_w(codes, ttl=20)
    items = []
    for sym, disp, name in CRYPTO:
        v = hq.get("btc_" + sym)
        if not v or len(v) < 9:
            items.append({"symbol": sym, "disp": disp, "name": name, "ok": False})
            continue
        price = fnum(v[8])
        prev = fnum(v[5])
        items.append({
            "symbol": sym, "disp": disp, "name": name, "ok": price is not None,
            "price": price, "prev": prev,
            "change": (price - prev) if (price is not None and prev) else None,
            "pct": ((price - prev) / prev * 100) if (price is not None and prev) else None,
            "high": fnum(v[6]), "low": fnum(v[7]),
            "vol": fnum(v[10]), "amount": fnum(v[12]) if len(v) > 12 else None,
            "time": (v[11] + " " + v[0]) if len(v) > 11 else "",
        })
    ok = [x for x in items if x["ok"]]
    return {"ok": bool(ok), "items": items, "source": "新浪财经 · 区块链行情",
            "error": None if ok else "新浪虚拟币接口无数据"}


def _gate_pair(symbol):
    """新浪虚拟币代码 -> Gate.io 现货交易对，如 btcbtcusd -> BTC_USDT"""
    disp = GATE_PAIR.get(symbol)
    if not disp:
        disp = str(symbol or "").upper()
        for w in ("BTCC", "_USDT", "USDT", "USD", "_"):
            disp = disp.replace(w, "")
    return disp + "_USDT"


def _gate_ts(ts):
    """Gate.io 时间戳（UTC 秒）-> 北京时间 struct_time"""
    return time.gmtime(int(ts) + 8 * 3600)


def fetch_crypto_kline_day(symbol):
    """Gate.io 现货日K（新浪虚拟币仅提供实时行情，无历史K线接口）"""
    pair = _gate_pair(symbol)
    url = ("https://api.gateio.ws/api/v4/spot/candlesticks?"
           "currency_pair=" + pair + "&interval=1d&limit=1000")
    js = json.loads(grab(url, HEAD_GATE, ttl=1800))
    if not isinstance(js, list):
        return {"ok": False, "type": "day", "code": symbol, "data": [],
                "error": "虚拟币日K不可用（Gate.io 未返回数据）"}
    data = []
    for r in js:
        # [时间戳, 计价成交量, 收盘, 最高, 最低, 开盘, 基础成交量, 是否收盘]
        if not isinstance(r, list) or len(r) < 6:
            continue
        try:
            ts = int(r[0])
        except Exception:
            continue
        row = {"d": time.strftime("%Y-%m-%d", _gate_ts(ts)),
               "o": fnum(r[5]), "c": fnum(r[2]), "h": fnum(r[3]), "l": fnum(r[4])}
        if row["c"] is not None:
            data.append(row)
    return {"ok": bool(data), "type": "day", "code": symbol, "data": data,
            "error": None if data else "虚拟币日K为空"}


def fetch_crypto_kline_min(symbol):
    """Gate.io 5 分钟 K 线合成的当日分时走势（北京时间 00:00 起）
    新浪虚拟币只有实时行情、不提供分时接口，故走 Gate.io 公开 K 线。"""
    pair = _gate_pair(symbol)
    url = ("https://api.gateio.ws/api/v4/spot/candlesticks?"
           "currency_pair=" + pair + "&interval=5m&limit=288")
    js = json.loads(grab(url, HEAD_GATE, ttl=120))
    if not isinstance(js, list) or not js:
        return {"ok": False, "type": "min", "code": symbol, "data": [],
                "error": "虚拟币分时不可用（Gate.io 未返回数据）"}
    pts = []
    for r in js:
        if not isinstance(r, list) or len(r) < 6:
            continue
        try:
            ts = int(r[0])
        except Exception:
            continue
        pts.append((ts, fnum(r[2])))
    pts.sort(key=lambda x: x[0])
    pts = [p for p in pts if p[1] is not None]
    if not pts:
        return {"ok": False, "type": "min", "code": symbol, "data": [],
                "error": "虚拟币分时为空"}
    last_d = time.strftime("%Y-%m-%d", _gate_ts(pts[-1][0]))
    sel = [p for p in pts if time.strftime("%Y-%m-%d", _gate_ts(p[0])) == last_d]
    if len(sel) < 12:          # 刚过零点，当日点数太少 -> 回退最近 24 小时
        sel = pts
    data = [{"t": time.strftime("%H:%M", _gate_ts(p[0])), "v": p[1]} for p in sel]
    # 昨收：作为分时图的基准线
    ref = None
    try:
        jd = json.loads(grab("https://api.gateio.ws/api/v4/spot/candlesticks?"
                             "currency_pair=" + pair + "&interval=1d&limit=2",
                             HEAD_GATE, ttl=600))
        if isinstance(jd, list) and len(jd) >= 2:
            ref = fnum(jd[-2][2])
    except Exception:
        ref = None
    return {"ok": bool(data), "type": "min", "code": symbol, "data": data, "ref": ref,
            "error": None if data else "虚拟币分时为空格"}

# ================================================================ 国际期货
def fetch_futures():
    codes = ["hf_" + s for s, _, _, _ in FUTURES]
    hq = sina_hq(codes, HEAD_FUT, ttl=15)
    items = []
    for sym, name, grp, unit in FUTURES:
        v = hq.get("hf_" + sym)
        if not v or len(v) < 9 or not str(v[0]).strip():
            items.append({"symbol": sym, "name": name, "group": grp, "unit": unit, "ok": False})
            continue
        price, prev = fnum(v[0]), fnum(v[7])
        items.append({
            "symbol": sym, "name": name, "group": grp, "unit": unit, "ok": price is not None,
            "price": price, "prev": prev, "open": fnum(v[8]),
            "change": (price - prev) if (price is not None and prev) else None,
            "pct": ((price - prev) / prev * 100) if (price is not None and prev) else None,
            "high": fnum(v[4]), "low": fnum(v[5]),
            "time": (str(v[12]) + " " + str(v[6])) if len(v) > 12 else str(v[6]),
        })
    ok = [x for x in items if x["ok"]]
    return {"ok": bool(ok), "items": items, "source": "新浪财经 · 国际期货",
            "error": None if ok else "新浪国际期货接口无数据"}


def fetch_futures_kline_day(symbol):
    url = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20a=/"
           "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=" + symbol)
    txt = grab(url, HEAD_FUT, ttl=300)
    body = jsonp_payload(txt)
    try:
        data = json.loads(body)
    except Exception:
        data = None
    if not isinstance(data, list):
        return {"ok": False, "type": "day", "code": symbol, "data": [], "error": "期货日K不可用"}
    out = [{"d": x.get("date"), "o": fnum(x.get("open")), "h": fnum(x.get("high")),
            "l": fnum(x.get("low")), "c": fnum(x.get("close"))} for x in data][-400:]
    return {"ok": bool(out), "type": "day", "code": symbol, "data": out,
            "error": None if out else "期货日K为空"}


def fetch_futures_kline_min(symbol):
    url = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20a=/"
           "GlobalFuturesService.getGlobalFuturesMinLine?symbol=" + symbol)
    txt = grab(url, HEAD_FUT, ttl=60)
    body = jsonp_payload(txt)
    try:
        js = json.loads(body)
    except Exception:
        js = None
    rows = (js or {}).get("minLine_1d") or []
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 2:
            continue
        v = fnum(r[1])
        if v is None:
            continue
        if len(r) >= 10 and isinstance(r[9], str) and len(r[9]) > 10:
            t = r[9][11:16]
        elif isinstance(r[0], str) and ":" in r[0]:
            t = r[0][:5]
        elif len(r) > 4:
            t = str(r[4])[:5]
        else:
            t = str(r[0])
        out.append({"t": t, "v": v})
    return {"ok": bool(out), "type": "min", "code": symbol, "data": out,
            "error": None if out else "期货分时为空"}


# ================================================================ 统一 K 线入口
KLINE_MAP = {
    "fx": (fetch_fx_kline_day, fetch_fx_kline_min),
    "bond": (fetch_bond_kline_day, fetch_bond_kline_min),
    "index": (fetch_index_kline_day, fetch_index_kline_min),
    "us": (fetch_index_kline_day, fetch_index_kline_min),
    "crypto": (fetch_crypto_kline_day, fetch_crypto_kline_min),
    "futures": (fetch_futures_kline_day, fetch_futures_kline_min),
}


def fetch_kline(kind, code, typ):
    pair = KLINE_MAP.get(kind)
    if not pair or not code:
        return {"ok": False, "error": "不支持的K线类型", "data": []}
    try:
        return period_wrap(pair[0], pair[1], code, typ)
    except Exception as e:
        return {"ok": False, "type": typ, "code": code, "data": [],
                "error": "%s: %s" % (type(e).__name__, e)}


# ================================================================ HTTP 服务
# PyInstaller 打包后资源解压到 sys._MEIPASS；源码运行时用脚本所在目录
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("GFD_PORT", "8770"))

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        ".woff2": "font/woff2", ".map": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "GlobalFinance/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", gz=False, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj):
        raw = json.dumps(clean_json(obj), ensure_ascii=False, default=str).encode("utf-8")
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(raw) > 2048:
            import gzip as _g
            self._send(200, _g.compress(raw), gz=True)
        else:
            self._send(200, raw)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p.startswith("/api/"):
                return self._api(p, q)
            return self._static(p)
        except Exception as e:
            traceback.print_exc()
            return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "data": []})

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/").replace("..", "")
        fp = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(fp):
            return self._send(404, "404 Not Found", "text/plain; charset=utf-8")
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            data = f.read()
        ctype = MIME.get(ext, "application/octet-stream")
        if ext in (".html", ".css", ".js", ".json", ".svg"):
            return self._send(200, data, ctype)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _api(self, p, q):
        def one(k, d=""):
            v = q.get(k)
            return (v[0] if v else d)

        if p == "/api/fx":
            return self._json(fetch_fx())
        if p == "/api/rmbidx":
            return self._json(fetch_rmbidx())
        if p == "/api/bonds":
            return self._json(fetch_bonds())
        if p == "/api/debt":
            return self._json(fetch_debt(int(fnum(one("page"), 1) or 1),
                                         int(fnum(one("size"), 20) or 20)))
        if p == "/api/debt/export":
            body, ctype, fname = export_debt()
            from urllib.parse import quote as _q
            return self._send(200, body, ctype,
                              extra={"Content-Disposition":
                                     "attachment; filename=%s; filename*=UTF-8''%s"
                                     % (fname.encode("ascii", "ignore").decode() or "debt.xlsx",
                                        _q(fname))})
        if p == "/api/rates":
            return self._json(fetch_rates())
        if p == "/api/indices":
            return self._json(fetch_indices())
        if p == "/api/us":
            return self._json(fetch_us())
        if p == "/api/crypto":
            return self._json(fetch_crypto())
        if p == "/api/futures":
            return self._json(fetch_futures())
        if p == "/api/kline":
            return self._json(fetch_kline(one("kind"), one("code"), one("type", "day")))
        if p == "/api/ping":
            return self._json({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        return self._json({"ok": False, "error": "未知接口 " + p})


def main():
    if not os.path.isdir(STATIC_DIR):
        print("[!] 找不到 static 目录：", STATIC_DIR)
        sys.exit(1)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d/" % PORT
    print("=" * 62)
    print("  全球金融数据看板  已启动")
    print("  地址： " + url)
    print("  停止： Ctrl + C")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
