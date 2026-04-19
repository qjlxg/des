# coding=utf-8
import base64
import logging
import os
import random
import re
import string
import time
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import cv2
import ddddocr
import numpy as np
import urllib3
from curl_cffi import requests as crequests

# ====================== 配置 ======================
URLS_FILE = "urls.txt"
CACHE_FILE = "tg.cache"
SUB_FILE = "subscription.txt"
NODES_FILE = "nodes_plain.txt"

MAX_WORKERS = 35
DEFAULT_TIMEOUT = 12
MAIL_WAIT_TIMEOUT = 55
RATE_LIMIT_PER_HOST = 0.45

# 常见邀请码（可自行扩展或留空让脚本尝试）
INVITE_CODES = ["", " ", "1", "666", "888", "999", "free", "trial", "2026"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(CACHE_FILE, mode='a', encoding='utf-8')])
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def preprocess_captcha(img_bytes: bytes) -> bytes:
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None: return img_bytes
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        clean = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        clean = cv2.dilate(clean, kernel, iterations=1)
        _, buf = cv2.imencode('.png', clean, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        return buf.tobytes()
    except:
        return img_bytes


class AirportCommander:
    def __init__(self):
        self.old_cache = self._parse_cache()
        self.ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
        self.limiter = RateLimiter(RATE_LIMIT_PER_HOST)

        self.REG_PATHS = [
            "/api/v1/passport/auth/register", "/api/v1/guest/passport/auth/register",
            "/api/v1/auth/register", "/register", "/api/register"
        ]
        self.SEND_EMAIL_PATHS = ["/api/v1/passport/comm/sendEmailVerify", "/api/v1/guest/passport/comm/sendEmailVerify", "/api/v1/comm/sendEmailVerify"]
        self.CAPTCHA_PATHS = ["/api/v1/passport/comm/captcha", "/api/v1/guest/passport/comm/captcha", "/api/v1/comm/captcha"]

        self.mail_apis = ["mail.tm", "mail.gw", "tempmail.lol"]

    def _parse_cache(self):
        data = {}
        if not os.path.exists(CACHE_FILE): return data
        try:
            content = Path(CACHE_FILE).read_text(encoding='utf-8')
            blocks = re.findall(r'\[(https?://[^\]]+)\]\n(.*?)\n\n', content, re.DOTALL)
            for url, body in blocks:
                lines = [l.strip() for l in body.strip().split('\n') if '  ' in l]
                info = {l.split('  ', 1)[0].strip(): l.split('  ', 1)[1].strip() for l in lines}
                if 'sub_url' in info:
                    data[url.rstrip('/')] = info
        except: pass
        return data

    def _get_session(self, base_url=""):
        session = crequests.Session(impersonate=random.choice(["chrome124", "chrome123"]))
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": f"{base_url.rstrip('/')}/",
            "Origin": base_url.rstrip('/'),
            "X-Requested-With": "XMLHttpRequest",
        })
        session.verify = False
        return session

    def create_temp_mail(self):
        random.shuffle(self.mail_apis)
        for api in self.mail_apis:
            try:
                s = crequests.Session(verify=False)
                dom = s.get(f"https://api.{api}/domains", timeout=DEFAULT_TIMEOUT).json()
                domain = dom['hydra:member'][0]['domain']
                email = f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=12))}@{domain}"
                pw = "Pass" + ''.join(random.choices(string.digits, k=9))
                if s.post(f"https://api.{api}/accounts", json={"address": email, "password": pw}).status_code == 201:
                    tk = s.post(f"https://api.{api}/token", json={"address": email, "password": pw}).json()['token']
                    return email, tk, api
            except: continue
        return None, None, None

    def wait_for_code(self, mail_token, mail_api):
        s = crequests.Session(verify=False)
        s.headers.update({"Authorization": f"Bearer {mail_token}"})
        start = time.time()
        while time.time() - start < MAIL_WAIT_TIMEOUT:
            try:
                msgs = s.get(f"https://api.{mail_api}/messages", timeout=10).json().get('hydra:member', [])
                for m in msgs:
                    if any(k in m.get('subject','').lower() for k in ['code','验证码','verification']):
                        detail = s.get(f"https://api.{mail_api}/messages/{m['id']}", timeout=10).json()
                        txt = detail.get('text') or detail.get('intro') or ''
                        code = re.search(r'(\d{4,8})', txt)
                        if code: return code.group(1)
            except: pass
            time.sleep(random.uniform(2.5, 5))
        return None

    def get_captcha(self, session, base_url):
        for path in self.CAPTCHA_PATHS:
            for _ in range(4):
                try:
                    resp = session.get(f"{base_url}{path}", timeout=DEFAULT_TIMEOUT)
                    if resp.status_code != 200: continue
                    if "image" in resp.headers.get("Content-Type", "").lower():
                        img_data = resp.content
                    else:
                        img_data = base64.b64decode(resp.json().get('data','').split(',')[-1])
                    code = self.ocr.classification(preprocess_captcha(img_data)).strip()
                    if code and len(code) >= 4:
                        return code
                except: 
                    time.sleep(0.7)
        return None

    def try_register(self, session, base_url, email, password):
        for invite in INVITE_CODES:
            for reg_path in self.REG_PATHS:
                payloads = [
                    {"email": email, "password": password, "repassword": password, "invite_code": invite},
                    {"email": email, "password": password, "repassword": password},
                ]
                for payload in payloads:
                    for is_json in [True, False]:
                        try:
                            if is_json:
                                resp = session.post(f"{base_url}{reg_path}", json=payload, timeout=DEFAULT_TIMEOUT)
                            else:
                                resp = session.post(f"{base_url}{reg_path}", data=payload, timeout=DEFAULT_TIMEOUT)

                            data = resp.json()
                            token = data.get("data", {}).get("token") or data.get("token")
                            if token and len(str(token)) > 15:
                                return token, ""

                            msg = str(data.get("message", "")).lower()
                            if "captcha" in msg or "验证码" in msg:
                                cap = self.get_captcha(session, base_url)
                                if cap:
                                    payload["captcha_code"] = cap
                                    continue
                            if any(x in msg for x in ["email", "邮箱", "code", "验证码", "verify"]):
                                return "NEED_EMAIL_VERIFY", msg
                            if any(x in msg for x in ["success", "ok", "注册成功"]):
                                return "PENDING", msg
                        except:
                            continue
        return None, None

    # 以下方法保持不变（auto_buy_free_plan、get_subscribe_url、get_traffic_info、format_log、extract_nodes、f_size、run）
    # ...（由于篇幅，这里省略这些方法，请保留你上一版本中对应的完整实现）

    def process_task(self, url):
        # ...（保持上一版本的 process_task 主体，只替换 try_register 调用部分即可）
        # 在注册循环中调用 self.try_register

        # DNS 检查 + 缓存检查保持不变

        for attempt in range(6):   # 增加到6次尝试
            try:
                time.sleep(random.uniform(2, 5))
                email, mail_token, mail_api = self.create_temp_mail()
                if not email: continue

                password = "Pass" + ''.join(random.choices(string.ascii_letters + string.digits, k=10))
                token, status = self.try_register(session, url, email, password)

                if token == "NEED_EMAIL_VERIFY" and mail_token:
                    cap = self.get_captcha(session, url)
                    for path in self.SEND_EMAIL_PATHS:
                        try:
                            session.post(f"{url}{path}", json={"email": email, "captcha_code": cap or ""}, timeout=DEFAULT_TIMEOUT)
                            break
                        except: continue
                    verify_code = self.wait_for_code(mail_token, mail_api)
                    if verify_code:
                        payload = {"email": email, "password": password, "repassword": password, "email_code": verify_code}
                        resp = session.post(f"{url}{self.REG_PATHS[0]}", json=payload, timeout=DEFAULT_TIMEOUT)
                        token = resp.json().get("data", {}).get("token") or resp.json().get("token")

                if token and len(str(token)) > 20:
                    # 后续购买计划、获取订阅等逻辑保持不变
                    session.headers["Authorization"] = f"Bearer {token}" if not str(token).startswith("Bearer") else token
                    self.auto_buy_free_plan(session, url)
                    sub_url = self.get_subscribe_url(session, url, token.replace("Bearer ", ""))
                    used, total, exp, txt = self.get_traffic_info(sub_url, session)
                    log = self.format_log(url, email, used, total, exp, sub_url)
                    logger.info(f"✅ 注册成功: {url}")
                    return self.extract_nodes(txt), log, sub_url

            except Exception as e:
                logger.debug(f"尝试 {attempt+1} 失败: {e}")
                time.sleep(random.uniform(5, 10))

        return [], f"[{url}]\nstatus  failed\nreason  多次注册失败 (已尝试邀请码+多路径)\ntime  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n", ""

    # run 方法保持不变

if __name__ == '__main__':
    commander = AirportCommander()
    commander.run()