import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from random import choice
from threading import RLock, Thread
from time import sleep, time
from urllib.parse import (parse_qsl, unquote_plus, urlencode, urljoin,
                          urlsplit, urlunsplit)

import json5
import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3 import Retry
from urllib3.util import parse_url

# 禁用 SSL 安全警告输出
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from utils import (cached, get, keep, parallel_map, rand_id, str2size,
                   str2timestamp)

REDIRECT_TO_GET = 1
REDIRECT_ORIGIN = 2
REDIRECT_PATH_QUERY = 4

# 预检路径配置
PROBE_REG_PATHS = [
    "api/v1/passport/auth/register", 
    "api/v1/guest/passport/auth/register",
    "api/v1/client/register",
    "auth/register",
    "api/v1/passport/auth/subscribe",
    "api/v1/passport/auth/v2boardRegister",
    "register",
    "user/register" 
]
PROBE_CONFIG_PATHS = ["api/v1/guest/comm/config", "api/v1/passport/comm/config"]

re_scheme = re.compile(r'^(?:([a-z]*):)?[\\/]*', re.I)

re_checked_in = re.compile(r'(?:已经?|重复)签到')
re_var_sub_token = re.compile(r'var sub_token = "(.+?)"')
re_email_code = re.compile(r'(?:码|碼|証|code).*?(?<![\da-z])([\da-z]{6})(?![\da-z])', re.I | re.S)

re_snapmail_domains = re.compile(r'emailDomainList.*?(\[.*?\])')
re_mailcx_js_path = re.compile(r'/_next/static/chunks/\d+-[\da-f]{16}.js')
re_mailcx_domains = re.compile(r'mailHosts:(\[.*?\])')
re_option_domain = re.compile(r'<option[^>]+value="@?((?:(?:[\da-z]+-)*[\da-z]+\.)+[a-z]+)"', re.I)

re_sspanel_invitation_num = re.compile(r'剩\D*(\d+)')
re_sspanel_initial_money = re.compile(r'得\s*(\d+(?:\.\d+)?)\s*元')
re_sspanel_sub_url = re.compile(r'https?:')
re_sspanel_expire = re.compile(r'等\D*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
re_sspanel_traffic_today = re.compile(r'日已用\D*?([-+]?\d+(?:\.\d+)?[BKMGTPE]?)', re.I)
re_sspanel_traffic_past = re.compile(r'去已用\D*?([-+]?\d+(?:\.\d+)?[BKMGTPE]?)', re.I)
re_sspanel_traffic_remain = re.compile(r'剩.流量\D*?([-+]?\d+(?:\.\d+)?[BKMGTPE]?)', re.I)
re_sspanel_balance = re.compile(r'(?:余|¥)\D*(\d+(?:\.\d+)?)')
re_sspanel_tab_shop_id = re.compile(r'tab-shop-(\d+)')
re_sspanel_plan_num = re.compile(r'plan_\d+')
re_sspanel_plan_id = re.compile(r'buy\D+(\d+)')
re_sspanel_price = re.compile(r'\d+(?:\.\d+)?')
re_sspanel_traffic = re.compile(r'\d+(?:\.\d+)?\s*[BKMGTPE]', re.I)
re_sspanel_duration = re.compile(r'(\d+)\s*(天|month)')

# 新增：用于保存订阅链接的文件锁
_SAVE_LOCK = RLock()

def save_subscription(sub_url: str, sub_info: dict):
    """
    保存有流量且未过期的订阅链接到 subscription.txt
    """
    if not sub_url or not sub_info:
        return
    try:
        total = sub_info.get('total', 0)
        used = (sub_info.get('upload') or 0) + (sub_info.get('download') or 0)
        if total <= used:
            return

        expire = sub_info.get('expire')
        if expire:
            ts_expire = expire if isinstance(expire, (int, float)) else str2timestamp(str(expire))
            if ts_expire and ts_expire < time():
                return

        with _SAVE_LOCK:
            with open('subscription.txt', 'a', encoding='utf-8') as f:
                for url in str(sub_url).split('|'):
                    if url.strip():
                        f.write(f"{url.strip()}\n")
    except:
        pass


def bs(text):
    return BeautifulSoup(text, 'html.parser')


class Response:
    def __init__(self, r: requests.Response):
        self.__content = r.content
        self.__headers = r.headers
        self.__status_code = r.status_code
        self.__reason = r.reason
        self.__url = r.url

    @property
    def content(self):
        return self.__content

    @property
    def headers(self):
        return self.__headers

    @property
    def status_code(self):
        return self.__status_code

    @property
    def ok(self):
        return 200 <= self.__status_code < 300

    @property
    def is_redirect(self):
        return 300 <= self.__status_code < 400

    @property
    def reason(self):
        return self.__reason

    @property
    def url(self):
        return self.__url

    @property
    @cached
    def text(self):
        return self.__content.decode(errors='ignore')

    @cached
    def json(self):
        try:
            return json.loads(self.text)
        except:
            # 修复解析失败导致的 NoneType 报错
            return {}

    @cached
    def bs(self):
        return bs(self.text)

    @cached
    def __str__(self):
        return f'{self.__status_code} {self.__reason} {repr(self.text[:100])}'


class Session(requests.Session):
    def __init__(self, base=None, user_agent=None, max_redirects=5, allow_redirects=7):
        super().__init__()
        # 恢复高性能连接池配置
        adapter = HTTPAdapter(
            pool_connections=100, 
            pool_maxsize=200, 
            max_retries=Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        )
        self.mount('https://', adapter)
        self.mount('http://', adapter)
        self.verify = False 
        self.max_redirects = max_redirects
        self.allow_redirects = allow_redirects
        self.headers['User-Agent'] = user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
        self.set_base(base)

    def set_base(self, base):
        if base:
            self.__base = re_scheme.sub(lambda m: f"{m[1] or 'https'}://", base.split('#', 1)[0])
        else:
            self.__base = None

    def set_origin(self, origin):
        if self.__base:
            if origin:
                base_split = urlsplit(self.__base)
                origin_split = urlsplit(re_scheme.sub(lambda m: f"{m[1] or base_split[0]}://", origin))
                self.__base = urlunsplit(origin_split[:2] + base_split[2:])
            else:
                self.__base = None
        else:
            self.set_base(origin)

    set_host = set_origin

    @property
    def base(self):
        return self.__base

    @property
    def host(self):
        return self.__base and urlsplit(self.__base).netloc

    @property
    def origin(self):
        if self.__base:
            return '://'.join(urlsplit(self.__base)[:2])
        else:
            return None

    def close(self):
        super().close()

    def reset(self):
        self.cookies.clear()
        self.headers.pop('authorization', None)
        self.headers.pop('token', None)

    def head(self, url='', **kwargs) -> Response:
        return self.request('HEAD', url, **kwargs)

    def get(self, url='', **kwargs) -> Response:
        return self.request('GET', url, **kwargs)

    def post(self, url='', data=None, **kwargs) -> Response:
        return self.request('POST', url, data, **kwargs)

    def put(self, url='', data=None, **kwargs) -> Response:
        return self.request('PUT', url, data, **kwargs)

    def request(self, method: str, url: str = '', data=None, timeout=15, allow_redirects=None, **kwargs):
        method = method.upper()
        url = urljoin(self.__base, url.split('#', 1)[0])
        kwargs.update(data=data, timeout=timeout, allow_redirects=False)
        if allow_redirects is None:
            allow_redirects = self.allow_redirects
        
        try:
            res = super().request(method, url, **kwargs)
        except Exception:
            raise 

        if allow_redirects and 300 <= res.status_code < 400:
            no = ~allow_redirects
            url = res.url
            kwargs.pop('params', None)
            i = 0
            while True:
                if 300 <= res.status_code < 400:
                    i += 1
                    if i > self.max_redirects:
                        break
                    new_url = urljoin(url, res.headers.get('Location', ''))
                    if url == new_url:
                        if no & REDIRECT_TO_GET:
                            break
                        method = 'GET'
                        for k in ('data', 'files', 'json'):
                            kwargs.pop(k, None)
                    else:
                        if no & REDIRECT_ORIGIN and no & REDIRECT_PATH_QUERY:
                            break
                        old, new = map(parse_url, (url, new_url))
                        if (no & REDIRECT_ORIGIN and old[:4] != new[:4]) or (no & REDIRECT_PATH_QUERY and old.request_uri != new.request_uri):
                            break
                        url = new_url
                elif res.status_code == 405 and method == 'POST':
                    if not (allow_redirects & REDIRECT_TO_GET):
                        break
                    method = 'GET'
                    for k in ('data', 'files', 'json'):
                        kwargs.pop(k, None)
                else:
                    break
                res = super().request(method, url, **kwargs)
        return Response(res)


class _ROSession(Session):
    def __init__(self, base=None, user_agent=None, allow_redirects=REDIRECT_ORIGIN):
        super().__init__(base, user_agent, allow_redirects=allow_redirects)
        self.__times = 0
        self.__redirect_origin = False

    @property
    def redirect_origin(self):
        return self.__redirect_origin

    def request(self, method, url='', *args, **kwargs):
        r = super().request(method, url, *args, **kwargs)
        if self.__times < 2:
            url_full = urljoin(self.base, url)
            if parse_url(r.url)[:4] != parse_url(url_full)[:4]:
                self.set_origin(r.url)
                self.__redirect_origin = True
            self.__times += 1
        return r


class V2BoardSession(_ROSession):
    def __set_auth(self, email: str, reg_info: dict):
        self.login_info = reg_info['data']
        self.email = email
        if 'v2board_session' not in self.cookies:
            self.headers['authorization'] = self.login_info.get('auth_data', '')

    def reset(self):
        super().reset()
        if hasattr(self, 'login_info'):
            del self.login_info
        if hasattr(self, 'email'):
            del self.email

    @staticmethod
    def raise_for_fail(res):
        if not res or 'data' not in res:
            raise Exception(res)

    def register(self, email: str, password=None, email_code=None, invite_code=None) -> str | None:
        self.reset()
        res = self.post('api/v1/passport/auth/register', {
            'email': email,
            'password': password or email.split('@')[0],
            'email_code': email_code or '',
            'invite_code': invite_code or '',
        }).json()
        if res and 'data' in res:
            self.__set_auth(email, res)
            return None
        if res and 'message' in res:
            return res['message']
        raise Exception(res)

    def login(self, email: str = None, password=None):
        if hasattr(self, 'login_info') and (not email or email == getattr(self, 'email', None)):
            return
        self.reset()
        res = self.post('api/v1/passport/auth/login', {
            'email': email,
            'password': password or email.split('@')[0]
        }).json()
        self.raise_for_fail(res)
        self.__set_auth(email, res)

    def send_email_code(self, email):
        res = self.post('api/v1/passport/comm/sendEmailVerify', {
            'email': email
        }, timeout=60).json()
        self.raise_for_fail(res)

    def buy(self, data=None):
        if not data:
            data = self.get_plan()
            if not data:
                return None
            data = urlencode(data)
        res = self.post(
            'api/v1/user/order/save',
            data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()
        self.raise_for_fail(res)
        res = self.post('api/v1/user/order/checkout', {
            'trade_no': res['data']
        }).json()
        self.raise_for_fail(res)
        return data

    def get_sub_url(self, **params) -> str:
        res = self.get('api/v1/user/getSubscribe').json()
        self.raise_for_fail(res)
        self.sub_url = res['data']['subscribe_url']
        return self.sub_url

    def get_sub_info(self):
        res = self.get('api/v1/user/getSubscribe').json()
        self.raise_for_fail(res)
        d = res['data']
        return {
            'upload': d.get('u', 0),
            'download': d.get('d', 0),
            'total': d.get('transfer_enable', 0),
            'expire': d.get('expired_at', 0)
        }

    def get_plan(self, min_price=0, max_price=0):
        r = self.get('api/v1/user/plan/fetch').json()
        self.raise_for_fail(r)
        min_price *= 100
        max_price *= 100
        plan = None
        _max = (0, 0, 0)
        for p in r['data']:
            if (ik := next(((i, k) for i, k in enumerate((
                'onetime_price',
                'three_year_price',
                'two_year_price',
                'year_price',
                'half_year_price',
                'quarter_price',
                'month_price',
            )) if (price := p.get(k)) is not None and min_price <= price <= max_price), None)):
                i, period = ik
                v = p[period], p['transfer_enable'], -i
                if v > _max:
                    _max = v
                    plan = {
                        'period': period,
                        'plan_id': p['id'],
                    }
        return plan


class SSPanelSession(_ROSession):
    def __init__(self, host=None, user_agent=None, auth_path=None):
        super().__init__(host, user_agent)
        self.auth_path = auth_path or 'auth'

    def reset(self):
        super().reset()
        if hasattr(self, 'email'):
            del self.email

    @staticmethod
    def raise_for_fail(res):
        if not res or not res.get('ret'):
            raise Exception(res)

    def register(self, email: str, password=None, email_code=None, invite_code=None, name_eq_email=None, reg_fmt=None, im_type=False, aff=None) -> str | None:
        self.reset()
        email_code_k, invite_code_k = ('email_code', 'invite_code') if reg_fmt == 'B' else ('emailcode', 'code')
        password = password or email.split('@')[0]
        res = self.post(f'{self.auth_path}/register', {
            'name': email if name_eq_email == 'T' else password,
            'email': email,
            'passwd': password,
            'repasswd': password,
            email_code_k: email_code or '',
            invite_code_k: invite_code or '',
            **({'imtype': 1, 'wechat': password} if im_type else {}),
            **({'aff': aff} if aff is not None else {}),
        }).json()
        if res and res.get('ret'):
            self.email = email
            return None
        if res and 'msg' in res:
            return res['msg']
        raise Exception(res)

    def login(self, email: str = None, password=None):
        if not email:
            email = self.email
        if 'email' in self.cookies and email == unquote_plus(self.cookies['email']):
            return
        self.reset()
        res = self.post(f'{self.auth_path}/login', {
            'email': email,
            'passwd': password or email.split('@')[0]
        }).json()
        self.raise_for_fail(res)
        self.email = email

    def send_email_code(self, email):
        res = self.post(f'{self.auth_path}/send', {
            'email': email
        }, timeout=60).json()
        self.raise_for_fail(res)

    def buy(self, data=None):
        if not data:
            data = self.get_plan(max_price=self.get_balance())
            if not data:
                return None
            data = urlencode(data)
        res = self.post(
            'user/buy',
            data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        ).json()
        self.raise_for_fail(res)
        return data

    def checkin(self):
        res = self.post('user/checkin').json()
        if not res or (not res.get('ret') and ('msg' not in res or not re_checked_in.search(res['msg']))):
            raise Exception(res)

    def get_sub_url(self, **params) -> str:
        r = self.get('user')
        tag = r.bs().find(attrs={'data-clipboard-text': re_sspanel_sub_url})
        if tag:
            sub_url = tag['data-clipboard-text']
            for k, v in parse_qsl(urlsplit(sub_url).query):
                if k == 'url':
                    sub_url = v
                    break
            params = keep(params, 'sub', 'clash', 'mu')
            if not params:
                params['sub'] = '3'
            sub_url_prefix = f"{sub_url.split('?')[0]}?"
            sub_url = '|'.join(f'{sub_url_prefix}{k}={v}' for k, vs in params.items() for v in vs.split())
        else:
            m = re_var_sub_token.search(r.text)
            if not m:
                raise Exception('未找到订阅链接')
            sub_url = m[1]
        return sub_url

    def get_sub_info(self):
        text = self.get('user').bs().text
        if not (
            (m_today := re_sspanel_traffic_today.search(text))
            and (m_past := re_sspanel_traffic_past.search(text))
            and (m_remain := re_sspanel_traffic_remain.search(text))
        ):
            return None
        m_expire = re_sspanel_expire.search(text)
        used = str2size(m_today[1]) + str2size(m_past[1])
        return {
            'upload': 0,
            'download': used,
            'total': used + str2size(m_remain[1]),
            'expire': str2timestamp(m_expire[1]) if m_expire else ''
        }

    def get_invite_info(self) -> tuple[str, int, float]:
        r = self.get('user/invite')
        if not r.ok:
            r = self.get('user/setting/invite')
        tag = r.bs().find(attrs={'data-clipboard-text': True})
        if not tag:
            raise Exception('未找到邀请码')
        invite_code = tag['data-clipboard-text']
        for k, v in parse_qsl(urlsplit(invite_code).query):
            if k == 'code':
                invite_code = v
                break
        t = r.bs().text
        m_in = re_sspanel_invitation_num.search(t)
        m_im = re_sspanel_initial_money.search(t)
        return invite_code, int(m_in[1]) if m_in else -1, float(m_im[1]) if m_im else 0

    def get_plan(self, min_price=0, max_price=0):
        doc = self.get('user/shop').bs()
        plan = None
        _max = (0, 0, 0)

        def up(id, price, traffic, duration):
            nonlocal plan, _max
            if min_price <= price <= max_price:
                v = price, traffic, duration
                if v > _max:
                    _max = v
                    plan = {'shop': id}

        if (tags := doc.find_all(id=re_sspanel_tab_shop_id)):
            for tag in tags:
                first = tag.find()
                if not first:
                    continue
                id = int(re_sspanel_tab_shop_id.fullmatch(tag['id'])[1])
                price = float(get(re_sspanel_price.search(first.text), 0, default=0))
                traffic = str2size(get(re_sspanel_traffic.search(tag.text), 0, default='1T'))
                duration = int(get(re_sspanel_duration.search(tag.text), 1, default=999))
                up(id, price, traffic, duration)
        elif (tags := doc.find_all(class_='pricing')):
            num_infos = []
            for tag in tags:
                m_price = re_sspanel_price.search(tag.find(class_='pricing-price').find().text)
                price = float(get(m_price, 0, default=0))
                if not (min_price <= price <= max_price):
                    continue
                traffic = str2size(get(re_sspanel_traffic.search(
                    tag.find(class_='pricing-padding').text), 0, default='1T'))
                cta = tag.find(class_='pricing-cta')
                onclick = cta.get('onclick') or cta.find()['onclick']
                m_num = re_sspanel_plan_num.search(onclick)
                if m_num:
                    num_infos.append((m_num[0], traffic))
                else:
                    m_id = re_sspanel_plan_id.search(onclick)
                    if not m_id:
                        raise Exception('未找到 plan_num/plan_id')
                    duration = int(get(re_sspanel_duration.search(
                        tag.find(class_='pricing-padding').text), 1, default=999))
                    up(int(m_id[1]), price, traffic, duration)

            def fn(item):
                for id, price, _time in self.get_plan_infos(item[0]):
                    m_duration = re_sspanel_duration.search(_time)
                    if get(m_duration, 2) != 'month':
                        raise Exception(f'未知时间单位: {_time}')
                    yield id, float(price), item[1], int(m_duration[1]) * 30

            for plans in parallel_map(fn, num_infos):
                for args in plans:
                    up(*args)
        elif (tags := doc.find_all(class_='shop-price')):
            for tag in tags:
                id = int(re_sspanel_plan_id.search(tag.find_next_sibling(class_='btn')['onclick'])[1])
                price, traffic, duration = map(float, (tag.text, *tag.find_next_sibling().text.split(' / ')))
                up(id, price, traffic, duration)
        elif (tags := doc.find_all(class_='pricingTable-firstTable_table__pricing')):
            for tag in tags:
                id = int(re_sspanel_plan_id.search(
                    tag.find_next_sibling(class_='pricingTable-firstTable_table__getstart')['onclick']
                )[1])
                price = float(get(re_sspanel_price.search(tag.text), 0, default=0))
                traffic = str2size(get(re_sspanel_traffic.search(tag.find_next_sibling().text), 0, default='1T'))
                duration = int(get(re_sspanel_duration.search(tag.find_next_sibling().text), 1, default=999))
                up(id, price, traffic, duration)
        return plan

    def get_plan_time(self, num):
        r = self.get('user/shop/getplantime', params={'num': num}).json()
        self.raise_for_fail(r)
        return r['plan_time']

    def get_plan_info(self, num, time):
        r = self.get('user/shop/getplaninfo', params={'num': num, 'time': time}).json()
        self.raise_for_fail(r)
        return r['id'], r['price']

    def get_plan_infos(self, num):
        return parallel_map(lambda time: (*self.get_plan_info(num, time), time), self.get_plan_time(num))

    def get_balance(self) -> float:
        m = re_sspanel_balance.search(self.get('user/code').bs().text)
        if m:
            return float(m[1])
        raise Exception('未找到余额')


class HkspeedupSession(_ROSession):
    def reset(self):
        super().reset()
        if hasattr(self, 'email'):
            del self.email

    @staticmethod
    def raise_for_fail(res):
        if not res or res.get('code') != 200:
            raise Exception(res)

    def register(self, email: str, password=None, email_code=None, invite_code=None) -> str | None:
        self.reset()
        password = password or email.split('@')[0]
        res = self.post('user/register', json={
            'email': email,
            'password': password,
            'ensurePassword': password,
            **({'code': email_code} if email_code else {}),
            **({'inviteCode': invite_code} if invite_code else {})
        }).json()
        if res and res.get('code') == 200:
            self.email = email
            return None
        if res and 'message' in res:
            return res['message']
        raise Exception(res)

    def login(self, email: str = None, password=None):
        if not email:
            email = self.email
        if 'token' in self.headers and email == self.email:
            return
        self.reset()
        res = self.post('user/login', json={
            'email': email,
            'password': password or email.split('@')[0]
        }).json()
        self.raise_for_fail(res)
        self.headers['token'] = res['data']['token']
        self.email = email

    def send_email_code(self, email):
        res = self.post('user/sendAuthCode', json={
            'email': email
        }, timeout=60).json()
        self.raise_for_fail(res)

    def get_sub_url(self, **params) -> str:
        res = self.get('user/info').json()
        self.raise_for_fail(res)
        self.sub_url = f"{self.base}/subscribe/{res['data']['subscribePassword']}"
        return self.sub_url


PanelSession = V2BoardSession | SSPanelSession | HkspeedupSession

panel_class_map = {
    'v2board': V2BoardSession,
    'sspanel': SSPanelSession,
    'hkspeedup': HkspeedupSession,
}


def guess_panel(host):
    info = {}
    session = _ROSession(host)
    try:
        # 恢复高性能预检逻辑：优先探测路径
        has_feature = False
        for path in PROBE_REG_PATHS + PROBE_CONFIG_PATHS:
            try:
                r_probe = session.head(path, timeout=3)
                if r_probe.status_code != 404:
                    has_feature = True
                    break
            except: continue
        
        if not has_feature:
            # 兼容 SSPanel 默认路径
            try:
                r_auth = session.get('auth/login', timeout=5)
                if r_auth.ok:
                    info['type'] = 'sspanel'
                    info['name'] = r_auth.bs().title.text.split(' — ')[-1] if r_auth.bs().title else "SSPanel"
            except: pass
            return info 

        # 详细识别
        try:
            r = session.get('api/v1/guest/comm/config', timeout=5)
            if r.status_code == 403:
                r = session.head(timeout=3)
                if r.ok and session.redirect_origin:
                    r = session.get('api/v1/guest/comm/config', timeout=5)
            
            if r.ok:
                try:
                    rj = r.json()
                    if rj:
                        info['type'] = 'v2board'
                        _r = session.get(timeout=5)
                        if _r.ok and _r.bs().title:
                            info['name'] = _r.bs().title.text
                        email_whitelist = get(rj, 'data', 'email_whitelist_suffix')
                        if email_whitelist:
                            info['email_domain'] = email_whitelist[0]
                except: pass
        except: pass
        
        if 'type' not in info:
            try:
                r = session.get('auth/login', timeout=5)
                if r.ok:
                    info['type'] = 'sspanel'
                    info['name'] = r.bs().title.text.split(' — ')[-1] if r.bs().title else "SSPanel"
                elif 300 <= r.status_code < 400:
                    r = session.head('user/login', timeout=5)
                    if r.ok:
                        info['type'] = 'sspanel'
                        info['auth_path'] = 'user'
            except: pass
        
        if 'api_host' not in info and session.redirect_origin:
            info['api_host'] = session.host
            
    except Exception as e:
        info['error'] = str(e)
    finally:
        session.close() 
    return info


class TempEmailSession(_ROSession):
    def get_domains(self) -> list[str]: ...
    def set_email_address(self, address: str): ...
    def get_messages(self) -> list[str]: ...


class MailGW(TempEmailSession):
    def __init__(self):
        super().__init__('api.mail.gw')

    def get_domains(self) -> list[str]:
        try:
            r = self.get('domains', timeout=10)
            return [item['domain'] for item in r.json().get('hydra:member', [])] if r.status_code == 200 else []
        except: return []

    def set_email_address(self, address: str):
        try:
            account = {'address': address, 'password': address.split('@')[0]}
            self.post('accounts', json=account, timeout=10)
            r = self.post('token', json=account, timeout=10)
            if r.status_code == 200:
                rj = r.json()
                if rj and 'token' in rj:
                    self.headers['Authorization'] = f'Bearer {rj["token"]}'
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.get('messages', timeout=10)
            if r.status_code != 200: return []
            def fn(item):
                try:
                    res = self.get(f'messages/{item["id"]}', timeout=10)
                    return res.json().get('text','') if res.ok else ''
                except: return ''
            return parallel_map(fn, r.json().get('hydra:member', []))
        except: return []


class Snapmail(TempEmailSession):
    def __init__(self):
        super().__init__('snapmail.cc')

    def get_domains(self) -> list[str]:
        try:
            r = self.get('scripts/controllers/addEmailBox.js', timeout=10)
            m = re_snapmail_domains.search(r.text)
            return json5.loads(m[1]) if m else []
        except: return []

    def set_email_address(self, address: str):
        self.address = address

    def get_messages(self) -> list[str]:
        try:
            r = self.get(f'emailList/{self.address}', timeout=10)
            if r.ok and isinstance(r.json(), list):
                return [bs(item['html']).get_text('\n', strip=True) for item in r.json()]
        except: pass
        return []


class MailCX(TempEmailSession):
    def __init__(self):
        super().__init__('api.mail.cx/api/v1/')

    def get_domains(self) -> list[str]:
        try:
            r = self.get('https://mail.cx', timeout=10)
            if not r.ok: return []
            js_paths = [js['src'] for js in r.bs().find_all('script') if js.has_attr('src') and re_mailcx_js_path.fullmatch(js['src'])]
            for path in js_paths:
                try:
                    rj = self.get(urljoin('https://mail.cx', path), timeout=10)
                    if rj.ok:
                        m = re_mailcx_domains.search(rj.text)
                        if m: return json5.loads(m[1])
                except: continue
        except: pass
        return []

    def set_email_address(self, address: str):
        try:
            r = self.post('auth/authorize_token', timeout=10)
            if r.ok:
                self.headers['Authorization'] = f'Bearer {r.json()}'
                self.address = address
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.get(f'mailbox/{self.address}', timeout=10)
            if not r.ok: return []
            def fn(item):
                try:
                    res = self.get(f'mailbox/{self.address}/{item["id"]}', timeout=10)
                    return res.json().get('body', {}).get('text', '') if res.ok else ''
                except: return ''
            return parallel_map(fn, r.json())
        except: return []


class GuerrillaMail(TempEmailSession):
    def __init__(self):
        super().__init__('api.guerrillamail.com/ajax.php')

    def get_domains(self) -> list[str]:
        try:
            r = self.get('https://www.spam4.me', timeout=10)
            return re_option_domain.findall(r.text) if r.ok else []
        except: return []

    def set_email_address(self, address: str):
        try:
            self.get(f'?f=set_email_user&email_user={address.split("@")[0]}', timeout=10)
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.get('?f=get_email_list&offset=0', timeout=10)
            if not (r.ok and r.content and 'list' in r.json()): return []
            def fn(item):
                try:
                    res = self.get(f'?f=fetch_email&email_id={item["mail_id"]}', timeout=10)
                    return bs(res.json()['mail_body']).get_text('\n', strip=True) if res.ok and res.text != 'false' else ''
                except: return ''
            return parallel_map(fn, r.json().get('list',[]))
        except: return []


class Emailnator(TempEmailSession):
    def __init__(self):
        super().__init__('www.emailnator.com/message-list')

    def get_domains(self) -> list[str]:
        return ['smartnator.com', 'femailtor.com', 'psnator.com', 'mydefipet.live', 'tmpnator.live']

    def set_email_address(self, address: str):
        try:
            self.get(timeout=10)
            token = self.cookies.get('XSRF-TOKEN')
            if token:
                self.headers['x-xsrf-token'] = unquote_plus(token)
                self.post(json={'email': address}, timeout=10)
                self.address = address
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.post(json={'email': self.address}, timeout=10)
            if not r.ok: return []
            def fn(item):
                try:
                    res = self.post(json={'email': self.address, 'messageID': item['messageID']}, timeout=10)
                    return res.bs().get_text('\n', strip=True) if res.ok else ''
                except: return ''
            return parallel_map(fn, r.json().get('messageData', [])[1:])
        except: return []


class Moakt(TempEmailSession):
    def __init__(self):
        super().__init__('moakt.com')

    def get_domains(self) -> list[str]:
        try:
            r = self.get(timeout=10)
            return re_option_domain.findall(r.text) if r.ok else []
        except: return []

    def set_email_address(self, address: str):
        try:
            u, d = address.split('@')
            self.post('inbox', {'domain': d, 'username': u}, timeout=10)
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.get('inbox', timeout=10)
            if not r.ok: return []
            def fn(href):
                try:
                    res = self.get(f"{href}/content", timeout=10)
                    return res.bs().get_text('\n', strip=True) if res.ok else ''
                except: return ''
            return parallel_map(fn, [a['href'] for a in r.bs().select('.tm-table td:first-child>a')])
        except: return []


class Rootsh(TempEmailSession):
    def __init__(self):
        super().__init__('rootsh.com')
        self.headers['Accept-Language'] = 'zh-CN,zh;q=0.9'

    def get_domains(self) -> list[str]:
        try:
            r = self.get(timeout=10)
            return [a.text for a in r.bs().select('#domainlist a')] if r.ok else []
        except: return []

    def set_email_address(self, address: str):
        try:
            self.get(timeout=10)
            self.post('applymail', {'mail': address}, timeout=10)
            self.address = address
        except: pass

    def get_messages(self) -> list[str]:
        try:
            r = self.post('getmail', {'mail': self.address}, timeout=10)
            if not (r.ok and 'mail' in r.json()): return []
            prefix = f"win/{self.address.replace('@', '(a)').replace('.', '-_-')}/"
            def fn(item):
                try:
                    res = self.get(prefix + item[4], timeout=10)
                    return res.bs().get_text('\n', strip=True) if res.ok else ''
                except: return ''
            return parallel_map(fn, r.json().get('mail', []))
        except: return []


class Linshiyou(TempEmailSession):
    def __init__(self):
        super().__init__('linshiyou.com')

    def get_domains(self) -> list[str]:
        try:
            r = self.get(timeout=10)
            return re_option_domain.findall(r.text) if r.ok else []
        except: return []

    def set_email_address(self, address: str):
        try:
            self.get('user.php', params={'user': address}, timeout=10)
            self.address = address
        except: pass

    def get_messages(self) -> list[str]:
        try:
            self.get('user.php', params={'user': self.address}, timeout=10)
            r = self.get('mail.php', timeout=10)
            return [tag.get_text('\n', strip=True) for tag in r.bs().find_all(class_='tmail-email-body-content')] if r.ok else []
        except: return []


@cached
def temp_email_domain_to_session_type(domain: str = None) -> dict[str, type[TempEmailSession]] | type[TempEmailSession] | None:
    if domain:
        return temp_email_domain_to_session_type().get(domain)

    session_types = TempEmailSession.__subclasses__()

    def fn(session_type: type[TempEmailSession]):
        try:
            domains = session_type().get_domains()
        except:
            domains = []
        return session_type, domains

    return {d: s for s, ds in parallel_map(fn, session_types) for d in ds}


class TempEmail:
    def __init__(self, banned_domains=None):
        self.__lock = RLock()
        self.__queues: list[tuple[str, Queue, float]] = []
        self.__banned = set(banned_domains or [])
        self.__session = None

    @property
    @cached
    def email(self) -> str:
        id_part = rand_id()
        domain_limit = 31 - len(id_part)
        all_temp_domains = temp_email_domain_to_session_type()
        valid_domains = [d for d in all_temp_domains if len(d) <= domain_limit and d not in self.__banned]
        if not valid_domains:
             raise Exception("没有可用的临时邮箱域名")
        domain = choice(valid_domains)
        address = f'{id_part}@{domain}'
        self.__session = all_temp_domains[domain]()
        self.__session.set_email_address(address)
        return address

    def get_email_code(self, keyword, timeout=60) -> str | None:
        queue = Queue(1)
        with self.__lock:
            self.__queues.append((keyword, queue, time() + timeout))
            if not hasattr(self, f'_{TempEmail.__name__}__th'):
                self.__th = Thread(target=self.__run, daemon=True)
                self.__th.start()
        try:
            return queue.get(timeout=timeout + 5)
        except:
            return None

    def __run(self):
        while True:
            sleep(2) # 恢复 2 秒轮询，提高注册速度
            if not self.__session: break
            
            try:
                # 修复 provider 报错导致的后台线程死掉
                try:
                    messages = self.__session.get_messages()
                except Exception:
                    messages = []
                
                with self.__lock:
                    if not self.__queues:
                        if hasattr(self, f'_{TempEmail.__name__}__th'):
                            del self.__th
                        break
                    new_len = 0
                    for item in self.__queues:
                        keyword, queue, end_time = item
                        found = False
                        for message in messages:
                            if message and (not keyword or keyword in message):
                                m = re_email_code.search(message)
                                if m:
                                    queue.put(m[1])
                                    found = True
                                    break
                        if found: continue
                        if time() > end_time:
                            queue.put(None)
                        else:
                            self.__queues[new_len] = item
                            new_len += 1
                    del self.__queues[new_len:]
                    if not self.__queues:
                        if hasattr(self, f'_{TempEmail.__name__}__th'):
                            del self.__th
                        break
            except Exception:
                # 最后的容错
                sleep(5)
