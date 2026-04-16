# coding=utf-8
import json, re, base64, time, random, string, os, socket, threading, datetime, sys
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from random import choice
from threading import RLock, Thread
from time import sleep, time as stime
from urllib.parse import (parse_qsl, unquote_plus, urlencode, urljoin,
                        urlsplit, urlunsplit, quote, parse_qs)

import json5, urllib3, requests
from bs4 import BeautifulSoup

# --- 核心引擎：过墙级伪装 ---
try:
    from curl_cffi import requests as crequests 
except ImportError:
    crequests = requests

# --- 核心识别：OCR 验证码 ---
try:
    import ddddocr
    ocr = ddddocr.DdddOcr(show_ad=False)
    ocr_lock = threading.Lock()
except ImportError:
    ocr = None

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== 配置与参数 ====================
INPUT_FILE = "urls.txt"
CACHE_FILE = "airport_master.cache"
SUB_FILE = "subscribes.txt"
NODES_FILE = "nodes.txt"
MAX_WORKERS = 150
SH_TZ = datetime.timezone(datetime.timedelta(hours=8))

# 增强路径参数
REG_PATHS = [
    "api/v1/passport/auth/register", 
    "api/v1/guest/passport/auth/register",
    "api/v1/client/register",
    "auth/register",
    "api/v1/passport/auth/subscribe",
    "api/v1/passport/auth/v2boardRegister",
    "register"
]
MAIL_PATHS = ["api/v1/passport/comm/sendEmailVerify", "api/v1/guest/passport/comm/sendEmailVerify"]
CAPTCHA_PATHS = ["api/v1/passport/comm/captcha", "api/v1/guest/passport/comm/captcha"]

# ==================== 增强版黑名单系统 ====================
DOMAIN_BLACKLIST = {
    'baidu.com', 'google.com', 'github.com', 'zhihu.com', 'xueqiu.com', 
    'yandex.com', 'yamcode.com', 'wikipedia.org', 'microsoft.com', 
    'apple.com', 'cloudflare.com', 'douban.com', 'weibo.com', 'qq.com',
    'csdn.net', 'juejin.cn', 'v2ex.com', 'bilibili.com', 'youtube.com',
    'twitter.com', 'facebook.com', 'instagram.com', 'telegram.org',
    'speedtest.net', 'fast.com', 'ip138.com', 'ip.skk.moe', 'gitee.com',
    'xueshu', 'research', 'edu', 'gov', 'amazon', 'bing', 'outlook', 'mail'
}

SUFFIX_BLACKLIST = ('.gov', '.edu', '.mil', '.org', '.gov.cn', '.edu.cn')

# 全局锁
io_lock = threading.Lock()

# ==================== 基础工具函数 ====================
def fast_log(msg):
    now = datetime.datetime.now(SH_TZ).strftime('%H:%M:%S')
    print(f"[{now}] {msg}", flush=True)

def format_size(size):
    try:
        s = float(size)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024: return f"{s:.2f}{unit}"
            s /= 1024
        return f"{s:.2f}PB"
    except: return "0B"

def format_time(ts):
    if not ts or ts == 0 or ts == "0": return "永久"
    try:
        ts = float(ts)
        # 兼容毫秒级
        if ts > 2147483647: ts = ts / 1000
        # 修改为精确到秒
        return datetime.datetime.fromtimestamp(ts, SH_TZ).strftime('%Y-%m-%d %H:%M:%S')
    except: return "未知"

def cached(func):
    cache = {}
    def wrapper(*args):
        if args not in cache: cache[args] = func(*args)
        return cache[args]
    return wrapper

# ==================== 响应与 Session 包装 ====================
class Response:
    def __init__(self, r, url=""):
        self.__content = getattr(r, 'content', b'')
        self.__headers = getattr(r, 'headers', {})
        self.__status_code = getattr(r, 'status_code', 500)
        self.__url = getattr(r, 'url', url)

    @property
    def content(self): return self.__content
    @property
    def headers(self): return self.__headers
    @property
    def status_code(self): return self.__status_code
    @property
    def ok(self): return 200 <= self.__status_code < 300
    @property
    def url(self): return self.__url

    @property
    @cached
    def text(self):
        try: return self.__content.decode('utf-8', errors='ignore').replace('\t', '    ')
        except: return ""

    @cached
    def json(self):
        try:
            jt = self.text.strip()
            if not (jt.startswith('{') or jt.startswith('[')): return {}
            return json.loads(jt)
        except: return {}

    @cached
    def bs(self): return BeautifulSoup(self.text, 'html.parser')

class Session:
    def __init__(self, base=None):
        self.session = crequests.Session(impersonate="chrome120", verify=False)
        self.headers = self.session.headers
        self.cookies = self.session.cookies
        self.__base = base.rstrip('/') if base else None

    @property
    def base(self): return self.__base

    def request(self, method, url='', data=None, **kwargs):
        full_url = url if url.startswith('http') else urljoin(self.__base + '/', url.lstrip('/'))
        try:
            r = self.session.request(method, full_url, data=data, timeout=20, **kwargs)
            return Response(r)
        except Exception as e:
            class Fake: pass
            f = Fake(); f.content = f"Error: {type(e).__name__}".encode(); f.status_code = 599; f.headers = {}; f.url = full_url
            return Response(f)

    def get(self, url='', **kwargs): return self.request('GET', url, **kwargs)
    def post(self, url='', data=None, **kwargs): return self.request('POST', url, data, **kwargs)

# ==================== V2BoardSession ====================
class V2BoardSession(Session):
    def login(self, email, password):
        paths = ['api/v1/passport/auth/login', 'api/v1/guest/passport/auth/login']
        for path in paths:
            res_obj = self.post(path, {'email': email, 'password': password})
            res = res_obj.json()
            if res.get('data') and isinstance(res['data'], dict):
                token = res['data'].get('token') or res['data'].get('auth_data')
                if token: 
                    self.headers['authorization'] = token
                    return res_obj.text
        return None

    def register(self, email, password):
        paths = [p for p in REG_PATHS if "api/v1" in p or p == "register"]
        payload = {'email': email, 'password': password, 'repassword': password, 'invite_code': ''}
        last_msg = "Path Not Found"
        for path in paths:
            res_obj = self.post(path, payload)
            if res_obj.status_code == 404: continue
            res = res_obj.json()
            if 'captcha' in str(res.get('message','')).lower() and ocr:
                for cp in CAPTCHA_PATHS:
                    c_res = self.get(cp).json()
                    if c_res.get('data'):
                        try:
                            img = base64.b64decode(c_res['data'].split(',')[-1])
                            with ocr_lock: payload['captcha_code'] = ocr.classification(img)
                            res_obj = self.post(path, payload)
                            res = res_obj.json()
                            break
                        except: pass
            data_content = res.get('data')
            if data_content and isinstance(data_content, dict):
                token = data_content.get('token') or data_content.get('auth_data')
                if token: 
                    self.headers['authorization'] = token
                    return None, res_obj.text
            last_msg = res.get('message') or (data_content if isinstance(data_content, str) else 'Reg Fail')
            if any(x in str(last_msg) for x in ["已经", "存在"]):
                login_raw = self.login(email, password)
                if login_raw: return None, login_raw
                break
        return last_msg, None

    def buy(self):
        try:
            sleep(random.uniform(0.5, 1.0))
            r = self.get('api/v1/user/plan/fetch').json()
            plans = r.get('data', [])
            for p in plans:
                price_keys = [k for k in p.keys() if '_price' in k]
                for k in price_keys:
                    if p.get(k) == 0:
                        period = k.replace('_price', '')
                        order = self.post('api/v1/user/order/save', {'period': period, 'plan_id': p['id']}).json()
                        if order.get('data'):
                            trade_no = order['data']
                            self.post('api/v1/user/order/checkout', {'trade_no': trade_no})
                            self.get(f'api/v1/user/plan/resetByOrder?trade_no={trade_no}')
                            return f"FreePlan({p['id']}_{period})"
        except: pass
        return "NoFreePlan"

    def get_sub_info(self):
        # 优先直接请求 User Info API
        try:
            res = self.get('api/v1/user/info').json()
            if res.get('data'):
                d = res['data']
                total = d.get('transfer_enable', 0)
                used = d.get('u', 0) + d.get('d', 0)
                expire = d.get('expired_at')
                # 只有 total > 0 才视为有效流量信息
                if total > 0:
                    return f"{format_size(used)}/{format_size(total)} ({format_time(expire)})", total
        except: pass
        # 备选 getSubscribe 接口
        try:
            res = self.get('api/v1/user/getSubscribe').json()
            if res.get('data'):
                d = res['data']
                total = d['transfer_enable']
                used = d['u'] + d['d']
                expire = d.get('expired_at')
                return f"{format_size(used)}/{format_size(total)} ({format_time(expire)})", total
        except: pass
        return None, 0

    def check_nodes_and_rates(self):
        try:
            res = self.get('api/v1/user/server/fetch').json()
            nodes = res.get('data', [])
            if not nodes: return 0, False # 空壳
            rates = [float(n.get('rate', 1)) for n in nodes]
            is_high_risk = all(r >= 10 for r in rates) if rates else False
            return len(nodes), is_high_risk
        except: return 0, False

    def get_sub_url(self):
        self.headers.update({'User-Agent': 'ClashMeta/1.18.0', 'Referer': f"{self.base}/"})
        tk = self.headers.get('authorization')
        try:
            res = self.get('api/v1/user/getSubscribe').json()
            if res.get('data') and isinstance(res['data'], dict): 
                s_url = res['data'].get('subscribe_url')
                if s_url: return s_url
        except: pass
        return f"{self.base}/api/v1/client/subscribe?token={tk}" if tk else None

# ==================== SSPanelSession ====================
class SSPanelSession(Session):
    def register(self, email, password):
        for path in ["auth/register", "register"]:
            payload = {'email': email, 'passwd': password, 'repasswd': password, 'agreeterm': 1, 'name': email.split('@')[0], 'code': ''}
            res_obj = self.post(path, payload)
            res = res_obj.json()
            if res.get('ret') or "成功" in str(res.get('msg', '')): return None, res_obj.text
            if "已经" in str(res.get('msg', '')):
                l_res_obj = self.post('auth/login', {'email': email, 'passwd': password})
                if l_res_obj.json().get('ret'): return None, l_res_obj.text
            if res_obj.status_code != 404: break
        return res.get('msg', 'Reg Fail'), None

    def get_sub_info(self):
        try:
            text = self.get('user').text
            def to_bytes(s):
                if not s: return 0
                units = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
                m = re.search(r'([-+]?\d+(?:\.\d+)?)\s*([BKMGT]?)', s, re.I)
                return float(m.group(1)) * units.get(m.group(2).upper(), 1) if m else 0
            m_today = re.search(r'日已用\D*?([-+]?\d+.*?B)', text, re.I)
            m_past = re.search(r'去已用\D*?([-+]?\d+.*?B)', text, re.I)
            m_remain = re.search(r'剩.流量\D*?([-+]?\d+.*?B)', text, re.I)
            m_expire = re.search(r'等\D*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', text)
            used = to_bytes(m_today.group(1) if m_today else "0B") + to_bytes(m_past.group(1) if m_past else "0B")
            remain = to_bytes(m_remain.group(1) if m_remain else "0B")
            total = used + remain
            return f"{format_size(used)}/{format_size(total)} ({m_expire.group(1) if m_expire else '永久'})", total
        except: return None, 0

    def get_sub_url(self):
        self.headers['User-Agent'] = 'Clash.meta'
        r = self.get('user').bs()
        tag = r.find(attrs={'data-clipboard-text': re.compile(r'https?://')})
        return tag['data-clipboard-text'] if tag else None

# ==================== 邮箱与处理逻辑 ====================
class TempEmail:
    def __init__(self): self.addr = ""
    def create(self):
        try:
            r = requests.get("https://www.1secmail.com/api/v1/?action=genEmailAddresses&count=1", timeout=10).json()
            if r: self.addr = r[0]; return self.addr
        except: pass
        self.addr = f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}@gmail.com"
        return self.addr

def check_subscription_robust(url):
    try:
        r = crequests.get(url, headers={'User-Agent': 'Clash.meta'}, timeout=15, verify=False)
        if not r.ok or len(r.text) < 100: return "EmptyContent", 0, False
        info_h = r.headers.get('subscription-userinfo', '')
        if info_h:
            p = {i.split('=')[0].strip(): i.split('=')[1].strip() for i in info_h.split(';') if '=' in i}
            total = int(p.get('total', 0)); used = int(p.get('upload', 0)) + int(p.get('download', 0))
            return f"{format_size(used)}/{format_size(total)} ({format_time(p.get('expire', 0))})", total, True
        return "Active(NoHeader)", 1, True
    except: return "CheckFailed", 0, False

def process_worker(url):
    clean_dom = urlsplit(url).netloc.lower() or url.split('/')[0].lower()
    if clean_dom.endswith(SUFFIX_BLACKLIST) or any(b in clean_dom for b in DOMAIN_BLACKLIST): return None

    base_url = url if url.startswith('http') else 'https://' + url
    test_s = Session(base_url)
    session = None
    try:
        if test_s.get('api/v1/guest/comm/config').ok or "v2board" in test_s.get('env.js').text.lower():
            session = V2BoardSession(test_s.base)
        elif any(x in test_s.get('auth/login').text for x in ["SSPanel", "staff"]):
            session = SSPanelSession(test_s.base)
    except: return None
    if not session: return None

    email = TempEmail().create(); password = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    reg_err, reg_raw = session.register(email, password)
    if reg_err: return None

    buy_status = session.buy() if isinstance(session, V2BoardSession) else "Default"
    
    # 1. 流量校验
    info, total_cap = session.get_sub_info()
    sub_url = session.get_sub_url()
    
    # 2. 节点与倍率校验 (后端 API)
    node_count = 1 # 默认 SSPanel 为
