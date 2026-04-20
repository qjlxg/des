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
    "user/register" # 补充常用路径
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
        # 流量校验
        total = sub_info.get('total', 0)
        used = (sub_info.get('upload') or 0) + (sub_info.get('download') or 0)
        if total <= used:
            return

        # 有效期校验
        expire = sub_info.get('expire')
        if expire:
            # 将字符串或数字转换为时间戳
            ts_expire = expire if isinstance(expire, (int, float)) else str2timestamp(str(expire))
            if ts_expire and ts_expire < time():
                return

        # 写入文件
        with _SAVE_LOCK:
            with open('subscription.txt', 'a', encoding='utf-8') as f:
                # 强制转为字符串防止拼接报错，并兼容多地址
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
            return {}

    @cached
    def bs(self):
        return bs(self.text)

    @cached
    def __str__(self):
        # 限制打印长度，防止大规模运行时日志输出导致卡顿
        return f'{self.__status_code} {self.__reason} {repr(self.text[:100])}'


class Session(requests.Session):
    def __init__(self, base=None, user_agent=None, max_redirects=5, allow_redirects=7):
        super().__init__()
        # 优化连接池：针对大规模任务，增加池大小
        adapter = HTTPAdapter(
            pool_connections=200, 
            pool_maxsize=500, 
            max_retries=Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        )
        self.mount('https://', adapter)
        self.mount('http://', adapter)
        self.verify = False # 强制忽略 SSL 证书
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
        # 强制 verify=False 解决证书无效问题
        kwargs.update(data=data, timeout=timeout, allow_redirects=False, verify=False)
        if allow_redirects is None:
            allow_redirects = self.allow_redirects
        
        try:
            res = super().request(method, url, **kwargs)
        except:
            raise 

        if allow_redirects and res.is_redirect:
            no = ~allow_redirects
            url = res.url
            kwargs.pop('params', None)
            i = 0
            while True:
                if res.is_redirect:
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
            url = urljoin(self.base, url)
            if parse_url(r.url)[:4] != parse_url(url)[:4]:
                self.set_origin(r.url)
                self.__redirect_origin = True
            self.__times += 1
        return r


class V2BoardSession(_ROSession):
    def __set_auth(self, email: str, reg_info: dict):
        if not reg_info or 'data' not in reg_info:
            return
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
        if not isinstance(res, dict) or 'data' not in res:
            raise Exception(str(res))

    def register(self, email: str, password=None, email_code=None, invite_code=None) -> str | None:
        self.reset()
        res = self.post('api/v1/passport/auth/register', {
            'email': email,
            'password': password or email.split('@')[0],
            'email_code': email_code or '',
            'invite_code': invite_code or '',
        }).json()
        if isinstance(res, dict) and 'data' in res:
            self.__set_auth(email, res)
            return None
        return res.get('message', str(res)) if isinstance(res, dict) else str(res)

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
            'trade_no': res.get('data')
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
        for p in r.get('data', []):
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
                v = p[period], p.get('transfer_enable', 0), -i
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
            raise Exception(str(res))

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
        if res.get('ret'):
            self.email = email
            return None
        return res.get('msg', str(res))

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
        if not res.get('ret') and ('msg' not in res or not re_checked_in.search(res['msg'])):
            raise Exception(str(res))

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
            raise Exception(str(res))

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
        if res.get('code') == 200:
            self.email = email
            return None
        return res.get('message', str(res))

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
        # 第一步：分析首页内容以识别变体面板（如 Xboard）
        homepage_text = ""
        has_feature = False
        try:
            r_idx = session.get(timeout=5)
            homepage_text = r_idx.text
            # 识别 Xboard 特有的 settings 或 theme
            if 'window.settings' in homepage_text or 'theme/Xboard' in homepage_text:
                has_feature = True
        except: pass

        if not has_feature:
            for path in PROBE_REG_PATHS + PROBE_CONFIG_PATHS:
                try:
                    if session.head(path, timeout=3).status_code != 404:
                        has_feature = True
                        break
                except: continue
        
        if not has_feature:
            return info 

        # 第二步：探测 V2Board / Xboard
        r = session.get('api/v1/guest/comm/config', timeout=5)
        if r.status_code == 403:
            r = session.head(timeout=3)
            if r.ok and session.redirect_origin:
                r = session.get('api/v1/guest/comm/config', timeout=5)
        
        if r.ok:
            try:
                rj = r.json()
                info['type'] = 'v2board'
                # 尝试从 window.settings 提取标题
                m_title = re.search(r"title:\s*['\"](.+?)['\"]", homepage_text)
                if m_title:
                    info['name'] = m_title.group(1)
                else:
                    _r = session.get(timeout=5)
                    if _r.ok and _r.bs().title:
                        info['name'] = _r.bs().title.text
                
                # 处理邮箱白名单逻辑
                email_whitelist = get(rj, 'data', 'email_whitelist_suffix')
                if email_whitelist:
                    info['email_domain'] = email_whitelist[0]
            except: pass
        
        # 探测 SSPanel
        if 'type' not in info:
            r = session.get('auth/login', timeout=5)
            if r.ok:
                info['type'] = 'sspanel'
                info['name'] = r.bs().title.text.split(' — ')[-1] if r.bs().title else "SSPanel"
            elif 300 <= r.status_code < 400:
                r = session.head('user/login', timeout=5)
                if r.ok:
                    info['type'] = 'sspanel'
                    info['auth_path'] = 'user'
        
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
        r = self.get('domains', timeout=10)
        if r.status_code != 200:
            raise Exception(f'获取 {self.host} 邮箱域名失败: {r}')
        return [item['domain'] for item in r.json()['hydra:member']]

    def set_email_address(self, address: str):
        account = {'address': address, 'password': address.split('@')[0]}
        r = self.post('accounts', json=account, timeout=10)
        if r.status_code != 201:
            raise Exception(f'创建 {self.host} 账户失败: {r}')
        r = self.post('token', json=account, timeout=10)
        if r.status_code != 200:
            raise Exception(f'获取 {self.host} token 失败: {r}')
        self.headers['Authorization'] = f'Bearer {r.json()["token"]}'

    def get_messages(self) -> list[str]:
        r = self.get('messages', timeout=10)
        return [
            r.json().get('text','')
            for r in parallel_map(lambda x: self.get(x, timeout=10), (f'messages/{item["id"]}' for item in r.json().get('hydra:member', [])))
            if r.status_code == 200
        ] if r.status_code == 200 else []


class Snapmail(TempEmailSession):
    def __init__(self):
        super().__init__('snapmail.cc')

    def get_domains(self) -> list[str]:
        r = self.get('scripts/controllers/addEmailBox.js', timeout=10)
        if not r.ok:
            raise Exception(f'获取 {self.host} addEmailBox.js 失败: {r}')
        return json5.loads(re_snapmail_domains.search(r.text)[1])

    def set_email_address(self, address: str):
        self.address = address

    def get_messages(self) -> list[str]:
        r = self.get(f'emailList/{self.address}', timeout=10)
        if r.ok and isinstance(r.json(), list):
            return [bs(item['html']).get_text('\n', strip=True) for item in r.json()]
        return []


class MailCX(TempEmailSession):
    def __init__(self):
        super().__init__('api.mail.cx/api/v1/')

    def get_domains(self) -> list[str]:
        r = self.get('https://mail.cx', timeout=10)
        if not r.ok:
            return []
        js_paths = []
        for js in r.bs().find_all('script'):
            if js.has_attr('src') and re_mailcx_js_path.fullmatch(js['src']):
                js_paths.append(js['src'])
        if js_paths:
            with ThreadPoolExecutor(len(js_paths)) as executor:
                futures = {executor.submit(self.get, urljoin('https://mail.cx', js_path), timeout=10): js_path for js_path in js_paths}
                for future in as_completed(futures, timeout=15):
                    try:
                        r = future.result()
                        if r.ok:
                            m = re_mailcx_domains.search(r.text)
                            if m:
                                return json5.loads(m[1])
                    except: pass
        return []

    def set_email_address(self, address: str):
        r = self.post('auth/authorize_token', timeout=10)
        if not r.ok:
            raise Exception(f'获取 {self.host} token 失败: {r}')
        self.headers['Authorization'] = f'Bearer {r.json()}'
        self.address = address

    def get_messages(self) -> list[str]:
        r = self.get(f'mailbox/{self.address}', timeout=10)
        return [
            r.json().get('body', {}).get('text', '')
            for r in parallel_map(lambda x: self.get(x, timeout=10), (f'mailbox/{self.address}/{item["id"]}' for item in r.json()))
            if r.ok
        ] if r.ok else []


class GuerrillaMail(TempEmailSession):
    def __init__(self):
        super().__init__('api.guerrillamail.com/ajax.php')

    def get_domains(self) -> list[str]:
        r = self.get('https://www.spam4.me', timeout=10)
        if not r.ok:
            return []
        return re_option_domain.findall(r.text)

    def set_email_address(self, address: str):
        r = self.get(f'?f=set_email_user&email_user={address.split("@")[0]}', timeout=10)
        if not (r.ok and r.content and r.json().get('email_addr')):
            raise Exception(f'设置 {self.host} 账户失败: {r}')

    def get_messages(self) -> list[str]:
        r = self.get('?f=get_email_list&offset=0', timeout=10)
        return [
            bs(r.json()['mail_body']).get_text('\n', strip=True)
            for r in parallel_map(lambda x: self.get(x, timeout=10), (f'?f=fetch_email&email_id={item["mail_id"]}' for item in r.json().get('list',[])))
            if r.ok and r.content and r.text != 'false'
        ] if r.ok and r.content else []


class Emailnator(TempEmailSession):
    def __init__(self):
        super().__init__('www.emailnator.com/message-list')

    def get_domains(self) -> list[str]:
        return ['smartnator.com', 'femailtor.com', 'psnator.com', 'mydefipet.live', 'tmpnator.live']

    def set_email_address(self, address: str):
        self.get(timeout=10)
        if not (token := self.cookies.get('XSRF-TOKEN')):
            raise Exception(f'获取 {self.host} XSRF-TOKEN 失败')
        self.headers['x-xsrf-token'] = unquote_plus(token)
        r = self.post(json={'email': address}, timeout=10)
        if not r.ok:
            raise Exception(f'设置 {self.host} 账户失败({address}): {r}')
        self.address = address

    def get_messages(self) -> list[str]:
        r = self.post(json={'email': self.address}, timeout=10)
        def fn(item): return self.post(json={'email': self.address, 'messageID': item['messageID']}, timeout=10)
        return [
            r.bs().get_text('\n', strip=True)
            for r in parallel_map(fn, r.json().get('messageData', [])[1:])
            if r.ok
        ] if r.ok else []


class Moakt(TempEmailSession):
    def __init__(self):
        super().__init__('moakt.com')

    def get_domains(self) -> list[str]:
        r = self.get(timeout=10)
        if not r.ok:
            return []
        return re_option_domain.findall(r.text)

    def set_email_address(self, address: str):
        username, domain = address.split('@')
        r = self.post('inbox', {'domain': domain, 'username': username}, timeout=10)
        if 'tm_session' not in self.cookies:
            raise Exception(f'设置 {self.host} 账户失败: {r}')

    def get_messages(self) -> list[str]:
        r = self.get('inbox', timeout=10)
        return [
            r.bs().get_text('\n', strip=True)
            for r in parallel_map(lambda x: self.get(x, timeout=10), (f"{item['href']}/content" for item in r.bs().select('.tm-table td:first-child>a')))
            if r.ok
        ] if r.ok else []


class Rootsh(TempEmailSession):
    def __init__(self):
        super().__init__('rootsh.com')
        self.headers['Accept-Language'] = 'zh-CN,zh;q=0.9'

    def get_domains(self) -> list[str]:
        r = self.get(timeout=10)
        if not r.ok:
            return []
        return [a.text for a in r.bs().select('#domainlist a')]

    def set_email_address(self, address: str):
        if 'mail' not in self.cookies:
            self.get(timeout=10)
        r = self.post('applymail', {'mail': address}, timeout=10)
        if not r.ok or r.json().get('success') != 'true':
            raise Exception(f'设置 {self.host} 账户失败: {r}')
        self.address = address

    def get_messages(self) -> list[str]:
        r = self.post('getmail', {'mail': self.address}, timeout=10)
        prefix = f"win/{self.address.replace('@', '(a)').replace('.', '-_-')}/"
        return [
            r.bs().get_text('\n', strip=True)
            for r in parallel_map(lambda x: self.get(x, timeout=10), (prefix + item[4] for item in r.json().get('mail', [])))
            if r.ok
        ] if r.ok else []


class Linshiyou(TempEmailSession):
    def __init__(self):
        super().__init__('linshiyou.com')

    def get_domains(self) -> list[str]:
        r = self.get(timeout=10)
        if not r.ok:
            return []
        return re_option_domain.findall(r.text)

    def set_email_address(self, address: str):
        r = self.get('user.php', params={'user': address}, timeout=10)
        if not r.ok or r.text != address:
            raise Exception(f'设置 {self.host} 账户失败: {r}')
        self.address = address

    def get_messages(self) -> list[str]:
        self.set_email_address(self.address)
        r = self.get('mail.php', timeout=10)
        if r.ok and r.content:
            return [tag.get_text('\n', strip=True) for tag in r.bs().find_all(class_='tmail-email-body-content')]
        return []


@cached
def temp_email_domain_to_session_type(domain: str = None) -> dict[str, type[TempEmailSession]] | type[TempEmailSession] | None:
    if domain:
        return temp_email_domain_to_session_type().get(domain)

    session_types = TempEmailSession.__subclasses__()

    def fn(session_type: type[TempEmailSession]):
        try:
            domains = session_type().get_domains()
        except Exception:
            domains = []
        return session_type, domains

    return {d: s for s, ds in parallel_map(fn, session_types) for d in ds}


class TempEmail:
    def __init__(self, banned_domains=None, allowed_domains=None):
        self.__lock = RLock()
        self.__queues: list[tuple[str, Queue, float]] = []
        self.__banned = set(banned_domains or [])
        self.__allowed = set(allowed_domains or [])

    @property
    @cached
    def email(self) -> str:
        id = rand_id()
        all_temp_domains = temp_email_domain_to_session_type()
        
        # 优先匹配允许的域名列表
        valid_domains = []
        if self.__allowed:
            valid_domains = [d for d in self.__allowed if d in all_temp_domains]
            
        if not valid_domains:
            domain_len_limit = 31 - len(id)
            valid_domains = [
                d for d in all_temp_domains
                if len(d) <= domain_len_limit and d not in self.__banned
            ]
            
        if not valid_domains:
             raise Exception("没有可用的临时邮箱域名")
        domain = choice(valid_domains)
        address = f'{id}@{domain}'
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
        return queue.get()

    def __run(self):
        while True:
            sleep(3) # 稍微增加间隔，防止挂起
            try:
                messages = self.__session.get_messages()
            except:
                messages = []
            with self.__lock:
                new_len = 0
                for item in self.__queues:
                    keyword, queue, end_time = item
                    found = False
                    for message in messages:
                        if keyword and message and keyword in message:
                            m = re_email_code.search(message)
                            queue.put(m[1] if m else None)
                            found = True
                            break
                    if found:
                        continue
                    if time() > end_time:
                        queue.put(None)
                    else:
                        self.__queues[new_len] = item
                        new_len += 1
                del self.__queues[new_len:]
                if new_len == 0:
                    if hasattr(self, f'_{TempEmail.__name__}__th'):
                        del self.__th
                    break
