# coding=utf-8
import base64
import logging
import os
import random
import re
import string
import time
import socket                      # ← 已添加，修复 DNS 错误
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import cv2
import ddddocr
import numpy as np
import urllib3
from curl_cffi import requests as crequests

# ====================== 配置区 ======================
URLS_FILE = "urls.txt"
CACHE_FILE = "tg.cache"
SUB_FILE = "subscription.txt"
NODES_FILE = "nodes_plain.txt"

MAX_WORKERS = 50                    # GitHub Actions 建议不要超过50-60
DEFAULT_TIMEOUT = 10
MAIL_WAIT_TIMEOUT = 45
RATE_LIMIT_PER_HOST = 0.6           # 降低速率，减少被封

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]

# ====================== 日志配置 ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(CACHE_FILE, mode='a', encoding='utf-8')]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def preprocess_captcha(img_bytes: bytes) -> bytes:
    """增强版图片预处理，提升 ddddocr 识别率"""
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return img_bytes

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 多种预处理尝试，提高成功率
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        cleaned = cv2.dilate(cleaned, kernel, iterations=1)

        _, buffer = cv2.imencode('.png', cleaned, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        return buffer.tobytes()
    except Exception:
        return img_bytes


class RateLimiter:
    def __init__(self, rate: float = 0.6):
        self.interval = 1.0 / rate
        self.last_request = {}

    def wait(self, host: str):
        now = time.time()
        if host in self.last_request:
            elapsed = now - self.last_request[host]
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed + random.uniform(0.1, 0.4))
        self.last_request[host] = time.time()


class AirportCommander:
    def __init__(self):
        self.old_cache = self._parse_cache()
        self.ocr = ddddocr.DdddOcr(show_ad=False, beta=True)   # 使用 beta 模型，通常更准
        self.limiter = RateLimiter(RATE_LIMIT_PER_HOST)

        self.REG_PATHS = ["/api/v1/passport/auth/register", "/api/v1/guest/passport/auth/register"]
        self.SEND_EMAIL_PATHS = ["/api/v1/passport/comm/sendEmailVerify", "/api/v1/guest/passport/comm/sendEmailVerify"]
        self.CAPTCHA_PATHS = ["/api/v1/passport/comm/captcha", "/api/v1/guest/passport/comm/captcha"]

        self.mail_apis = ["mail.tm", "mail.gw", "tempmail.lol"]

    def _parse_cache(self):
        data = {}
        if not os.path.exists(CACHE_FILE):
            return data
        try:
            content = Path(CACHE_FILE).read_text(encoding='utf-8')
            blocks = re.findall(r'\[(https?://[^\]]+)\]\n(.*?)\n\n', content, re.DOTALL)
            for url, body in blocks:
                lines = [line.strip() for line in body.strip().split('\n') if '  ' in line]
                info = {line.split('  ', 1)[0].strip(): line.split('  ', 1)[1].strip() for line in lines if '  ' in line}
                if 'sub_url' in info:
                    data[url.rstrip('/')] = info
        except:
            pass
        return data

    def _get_session(self, base_url=""):
        session = crequests.Session(impersonate=random.choice(["chrome124", "chrome123"]))
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": f"{base_url.rstrip('/')}/" if base_url else None,
            "Origin": base_url.rstrip('/') if base_url else None,
        })
        session.verify = False
        return session

    def create_temp_mail(self):
        random.shuffle(self.mail_apis)
        for api in self.mail_apis:
            try:
                s = crequests.Session(verify=False)
                dom_res = s.get(f"https://api.{api}/domains", timeout=DEFAULT_TIMEOUT)
                domain = dom_res.json()['hydra:member'][0]['domain']
                email = f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=12))}@{domain}"
                pw = "Pass" + ''.join(random.choices(string.digits, k=8))

                if s.post(f"https://api.{api}/accounts", json={"address": email, "password": pw}, timeout=DEFAULT_TIMEOUT).status_code == 201:
                    tk = s.post(f"https://api.{api}/token", json={"address": email, "password": pw}).json()['token']
                    return email, tk, api
            except:
                continue
        return None, None, None

    def wait_for_code(self, mail_token, mail_api):
        s = crequests.Session(verify=False)
        s.headers.update({"Authorization": f"Bearer {mail_token}"})
        start = time.time()
        while time.time() - start < MAIL_WAIT_TIMEOUT:
            try:
                msgs = s.get(f"https://api.{mail_api}/messages", timeout=8).json().get('hydra:member', [])
                for m in msgs:
                    if any(k in m.get('subject', '').lower() for k in ['code', '验证码', 'verification']):
                        detail = s.get(f"https://api.{mail_api}/messages/{m['id']}", timeout=8).json()
                        txt = detail.get('text') or detail.get('intro') or ''
                        code = re.search(r'(\d{4,8})', txt)
                        if code:
                            return code.group(1)
            except:
                pass
            time.sleep(random.uniform(2, 4))
        return None

    def get_captcha(self, session, base_url):
        for path in self.CAPTCHA_PATHS:
            for _ in range(3):   # 验证码重试3次
                try:
                    resp = session.get(f"{base_url}{path}", timeout=DEFAULT_TIMEOUT)
                    if resp.status_code != 200:
                        continue
                    if "image" in resp.headers.get("Content-Type", ""):
                        img_data = resp.content
                    else:
                        data = resp.json()
                        img_data = base64.b64decode(data.get('data', '').split(',')[-1])

                    processed = preprocess_captcha(img_data)
                    code = self.ocr.classification(processed).strip()
                    if code and len(code) >= 4:
                        return code
                    time.sleep(0.5)
                except:
                    continue
        return None

    def try_register(self, session, base_url, email, password):
        payloads = [
            {"email": email, "password": password, "repassword": password},
            {"email": email, "password": password, "repassword": password, "invite_code": ""},
        ]

        for reg_path in self.REG_PATHS:
            for payload in payloads:
                for use_json in [True, False]:
                    try:
                        if use_json:
                            resp = session.post(f"{base_url}{reg_path}", json=payload, timeout=DEFAULT_TIMEOUT)
                        else:
                            resp = session.post(f"{base_url}{reg_path}", data=payload, timeout=DEFAULT_TIMEOUT)

                        if resp.status_code not in (200, 201):
                            continue

                        data = resp.json()
                        token = data.get("data", {}).get("token") or data.get("token")
                        if token:
                            return token, ""

                        msg = str(data.get("message", "")).lower()
                        if "captcha" in msg:
                            captcha = self.get_captcha(session, base_url)
                            if captcha:
                                payload["captcha_code"] = captcha
                                continue
                        if any(k in msg for k in ["email", "邮箱", "code", "验证码"]):
                            return "NEED_EMAIL", msg
                    except:
                        continue
        return None, None

    def auto_buy_free_plan(self, session, base_url):
        for path in ["/api/v1/user/plan/fetch", "/api/v1/guest/plan/fetch"]:
            try:
                res = session.get(f"{base_url}{path}", timeout=DEFAULT_TIMEOUT).json()
                plans = res.get("data", [])
                for p in plans:
                    free_cycles = [k.replace('_price', '') for k, v in p.items() if '_price' in k and str(v) == '0' and k != 'reset_price']
                    if free_cycles and p.get('transfer_enable', 0) > 0:
                        cycle = free_cycles[0]
                        order = session.post(f"{base_url}/api/v1/user/order/save", json={'plan_id': p['id'], 'cycle': cycle})
                        trade_no = order.json().get('data')
                        if trade_no:
                            session.post(f"{base_url}/api/v1/user/order/checkout", json={'trade_no': trade_no, 'method': 1})
                            logger.info(f"成功购买免费计划: {p.get('name')}")
                            return True
            except:
                continue
        return False

    def get_subscribe_url(self, session, base_url, token):
        default_sub = f"{base_url}/api/v1/client/subscribe?token={token}"
        try:
            res = session.get(f"{base_url}/api/v1/user/getSubscribe", timeout=DEFAULT_TIMEOUT)
            data = res.json().get("data")
            if isinstance(data, str) and data.startswith("http"):
                return data
        except:
            pass
        return default_sub

    def get_traffic_info(self, sub_url, session=None):
        try:
            s = session or self._get_session()
            resp = s.get(sub_url, timeout=DEFAULT_TIMEOUT + 5)
            header = resp.headers.get('subscription-userinfo', '')
            u = d = t = e = 0
            if header:
                for item in header.split(';'):
                    if '=' in item:
                        k, v = [x.strip() for x in item.split('=', 1)]
                        if k == 'upload': u = int(v)
                        elif k == 'download': d = int(v)
                        elif k == 'total': t = int(v)
                        elif k == 'expire': e = int(v)
            return u + d, t, e, resp.text
        except:
            return 0, 0, 0, ""

    def f_size(self, size):
        if size <= 0: return "0B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}PB"

    def process_task(self, url):
        url = url.rstrip('/')
        logger.info(f"开始处理: {url}")

        # DNS 检查
        try:
            host = re.search(r'https?://([^/]+)', url).group(1).split(':')[0]
            socket.gethostbyname(host)
        except Exception as e:
            return [], f"[{url}]\nstatus  failed\nreason  DNS失败: {e}\n\n", ""

        session = self._get_session(url)

        # 使用缓存
        if url in self.old_cache:
            info = self.old_cache[url]
            sub_url = info.get('sub_url')
            if sub_url:
                used, total, exp, txt = self.get_traffic_info(sub_url, session)
                if total > 0:
                    nodes = self.extract_nodes(txt)
                    log = self.format_log(url, info.get('email', ''), used, total, exp, sub_url)
                    return nodes, log, sub_url

        # 注册流程（增加重试）
        for attempt in range(4):
            try:
                email, mail_token, mail_api = self.create_temp_mail()
                if not email:
                    time.sleep(random.uniform(2, 5))
                    continue

                password = "Pass" + ''.join(random.choices(string.digits, k=8))
                token, status = self.try_register(session, url, email, password)

                if token == "NEED_EMAIL":
                    # 发送邮箱验证码
                    for send_path in self.SEND_EMAIL_PATHS:
                        try:
                            cap = self.get_captcha(session, url)
                            session.post(f"{url}{send_path}", json={"email": email, "captcha_code": cap or ""}, timeout=DEFAULT_TIMEOUT)
                            break
                        except:
                            continue

                    verify_code = self.wait_for_code(mail_token, mail_api)
                    if verify_code:
                        payload = {"email": email, "password": password, "repassword": password, "email_code": verify_code}
                        resp = session.post(f"{url}{self.REG_PATHS[0]}", json=payload, timeout=DEFAULT_TIMEOUT)
                        token = resp.json().get("data", {}).get("token")

                if token and len(str(token)) > 20:
                    session.headers["Authorization"] = f"Bearer {token}" if not str(token).startswith("Bearer") else token

                    self.auto_buy_free_plan(session, url)
                    sub_url = self.get_subscribe_url(session, url, token)

                    used, total, exp, txt = self.get_traffic_info(sub_url, session)
                    log = self.format_log(url, email, used, total, exp, sub_url)
                    logger.info(f"注册成功: {url}")
                    return self.extract_nodes(txt), log, sub_url

            except Exception as e:
                logger.debug(f"第{attempt+1}次尝试失败: {e}")
                time.sleep(random.uniform(3, 7))

        return [], f"[{url}]\nstatus  failed\nreason  注册失败（已重试）\ntime  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n", ""

    def format_log(self, url, email, used, total, exp, sub_url):
        exp_str = datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M') if exp > 0 else "永久"
        remain = max(0, total - used)
        return (f"[{url}]\nbuy  pass\nemail  {email}\n"
                f"sub_info  {self.f_size(used)}  {self.f_size(total)}  {exp_str}  (剩余 {self.f_size(remain)})\n"
                f"sub_url  {sub_url}\ntime  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\ntype  v2board\n\n")

    def extract_nodes(self, content):
        if not content:
            return []
        pattern = r'(vmess|vless|ss|ssr|trojan|hysteria2?|hy2|tuic|anytls)://[^\s\'"<>]+'
        nodes = re.findall(pattern, content, re.I)
        return list(dict.fromkeys(nodes))

    def run(self):
        if not os.path.exists(URLS_FILE):
            logger.error(f"未找到 {URLS_FILE}")
            return

        with open(URLS_FILE, encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip().startswith('http')]
        urls = list(dict.fromkeys(urls))
        random.shuffle(urls)

        logger.info(f"开始处理 {len(urls)} 个机场...")

        all_nodes = []
        all_logs = []
        all_subs = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            futures = {exe.submit(self.process_task, u): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    nodes, log, sub_url = future.result()
                    if nodes:
                        all_nodes.extend(nodes)
                    if log:
                        all_logs.append(log)
                    if sub_url:
                        all_subs.append(sub_url)
                    status = "成功" if "buy  pass" in log else "失败"
                    print(f"[{status}] {url}")
                except Exception as e:
                    logger.error(f"处理 {url} 异常: {e}")

        Path(NODES_FILE).write_text("\n".join(dict.fromkeys(all_nodes)), encoding='utf-8')
        Path(SUB_FILE).write_text("\n".join(dict.fromkeys(all_subs)), encoding='utf-8')
        Path(CACHE_FILE).write_text("".join(all_logs), encoding='utf-8')

        logger.info(f"任务完成！节点数: {len(all_nodes)} | 订阅数: {len(all_subs)}")


if __name__ == '__main__':
    commander = AirportCommander()
    commander.run()