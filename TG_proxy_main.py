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

# ====================== 配置区 ======================
URLS_FILE = "urls.txt"
CACHE_FILE = "tg.cache"
SUB_FILE = "subscription.txt"
NODES_FILE = "nodes_plain.txt"

MAX_WORKERS = 40                    # GitHub Actions 建议进一步降低
DEFAULT_TIMEOUT = 12
MAIL_WAIT_TIMEOUT = 50
RATE_LIMIT_PER_HOST = 0.5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
]

# ====================== 日志 ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(CACHE_FILE, mode='a', encoding='utf-8')]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def preprocess_captcha(img_bytes: bytes) -> bytes:
    """多级别预处理，提升验证码识别率"""
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return img_bytes

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 多种二值化尝试
        methods = [
            cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2),
            cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        ]
        
        best = img_bytes
        best_score = 0
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        
        for bin_img in methods:
            clean = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, kernel)
            clean = cv2.dilate(clean, kernel, iterations=1)
            _, buf = cv2.imencode('.png', clean, [cv2.IMWRITE_PNG_COMPRESSION, 9])
            processed = buf.tobytes()
            # 简单评分：非零像素比例（更清晰的图通常更好）
            score = np.count_nonzero(clean) / clean.size
            if score > best_score:
                best_score = score
                best = processed
                
        return best
    except Exception:
        return img_bytes


class RateLimiter:
    def __init__(self, rate: float = 0.5):
        self.interval = 1.0 / rate
        self.last = {}

    def wait(self, host: str):
        now = time.time()
        if host in self.last:
            sleep_time = self.interval - (now - self.last[host])
            if sleep_time > 0:
                time.sleep(sleep_time + random.uniform(0.2, 0.8))
        self.last[host] = time.time()


class AirportCommander:
    def __init__(self):
        self.old_cache = self._parse_cache()
        self.ocr = ddddocr.DdddOcr(show_ad=False, beta=True)
        self.limiter = RateLimiter(RATE_LIMIT_PER_HOST)

        self.REG_PATHS = [
            "/api/v1/passport/auth/register",
            "/api/v1/guest/passport/auth/register",
            "/api/v1/auth/register"
        ]
        self.SEND_EMAIL_PATHS = [
            "/api/v1/passport/comm/sendEmailVerify",
            "/api/v1/guest/passport/comm/sendEmailVerify",
            "/api/v1/comm/sendEmailVerify"
        ]
        self.CAPTCHA_PATHS = [
            "/api/v1/passport/comm/captcha",
            "/api/v1/guest/passport/comm/captcha",
            "/api/v1/comm/captcha"
        ]

        self.mail_apis = ["mail.tm", "mail.gw", "tempmail.lol"]

    def _parse_cache(self):
        # ... (保持与上一版本相同，省略以节省篇幅)
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
        except:
            pass
        return data

    def _get_session(self, base_url=""):
        session = crequests.Session(impersonate=random.choice(["chrome124", "chrome123", "edge"]))
        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": f"{base_url.rstrip('/')}/" if base_url else "",
            "Origin": base_url.rstrip('/') if base_url else "",
            "X-Requested-With": "XMLHttpRequest",
        })
        session.verify = False
        return session

    # create_temp_mail、wait_for_code、get_captcha 保持上一版本的高级实现（已包含重试）

    def get_captcha(self, session, base_url):
        for path in self.CAPTCHA_PATHS:
            for attempt in range(4):
                try:
                    self.limiter.wait(base_url.split('//')[1] if '//' in base_url else base_url)
                    resp = session.get(f"{base_url}{path}", timeout=DEFAULT_TIMEOUT)
                    if resp.status_code != 200: continue
                    
                    if "image" in resp.headers.get("Content-Type", "").lower():
                        img_data = resp.content
                    else:
                        try:
                            data = resp.json()
                            b64 = data.get('data', '').split(',')[-1]
                            img_data = base64.b64decode(b64)
                        except:
                            img_data = resp.content

                    processed = preprocess_captcha(img_data)
                    code = self.ocr.classification(processed).strip()
                    if code and len(re.sub(r'[^A-Za-z0-9]', '', code)) >= 4:
                        return code
                    time.sleep(0.6)
                except:
                    time.sleep(0.8)
        return None

    def try_register(self, session, base_url, email, password):
        base_payloads = [
            {"email": email, "password": password, "repassword": password},
            {"email": email, "password": password, "repassword": password, "invite_code": ""},
            {"email": email, "password": password, "repassword": password, "invite_code": " "},
        ]

        for reg_path in self.REG_PATHS:
            for payload in base_payloads:
                for is_json in [True, False]:
                    try:
                        if is_json:
                            resp = session.post(f"{base_url}{reg_path}", json=payload, timeout=DEFAULT_TIMEOUT)
                        else:
                            resp = session.post(f"{base_url}{reg_path}", data=payload, timeout=DEFAULT_TIMEOUT)

                        if resp.status_code not in (200, 201, 400, 422):
                            continue

                        data = resp.json()
                        token = data.get("data", {}).get("token") or data.get("token")
                        if token and len(str(token)) > 15:
                            return token, ""

                        msg = str(data.get("message", "")).lower()
                        if any(x in msg for x in ["success", "注册成功", "ok"]):
                            return "PENDING", msg
                        if "captcha" in msg or "验证码" in msg:
                            cap = self.get_captcha(session, base_url)
                            if cap:
                                payload["captcha_code"] = cap
                                continue
                        if any(x in msg for x in ["email", "邮箱", "code", "验证码", "verify"]):
                            return "NEED_EMAIL_VERIFY", msg
                    except:
                        continue
        return None, None

    # auto_buy_free_plan、get_subscribe_url、get_traffic_info、format_log、extract_nodes 等保持上一版本逻辑（已较完善）

    def process_task(self, url):
        url = url.rstrip('/')
        logger.info(f"开始处理 → {url}")

        # DNS 检查
        try:
            host = re.search(r'https?://([^/:\s]+)', url).group(1)
            socket.gethostbyname(host)
        except Exception as e:
            return [], f"[{url}]\nstatus  failed\nreason  DNS失败: {e}\n\n", ""

        session = self._get_session(url)

        # 缓存优先
        if url in self.old_cache:
            info = self.old_cache[url]
            sub_url = info.get('sub_url')
            if sub_url:
                used, total, exp, txt = self.get_traffic_info(sub_url, session)
                if total > 100 * 1024**3:   # 至少有 100GB 才认为有效
                    nodes = self.extract_nodes(txt)
                    log = self.format_log(url, info.get('email', ''), used, total, exp, sub_url)
                    return nodes, log, sub_url

        # 主注册循环 - 最多5次完整尝试
        for attempt in range(5):
            try:
                time.sleep(random.uniform(1.5, 4.0))   # 随机延迟防检测
                email, mail_token, mail_api = self.create_temp_mail()
                if not email:
                    continue

                password = "Pass" + ''.join(random.choices(string.digits + string.ascii_letters, k=9))
                token, status = self.try_register(session, url, email, password)

                if token == "NEED_EMAIL_VERIFY" and mail_token:
                    # 发送邮箱验证码（多路径尝试）
                    cap = self.get_captcha(session, url)
                    sent = False
                    for path in self.SEND_EMAIL_PATHS:
                        try:
                            r = session.post(f"{url}{path}", json={"email": email, "captcha_code": cap or ""}, timeout=DEFAULT_TIMEOUT)
                            if r.status_code in (200, 201):
                                sent = True
                                break
                        except:
                            continue
                    if sent:
                        verify_code = self.wait_for_code(mail_token, mail_api)
                        if verify_code:
                            payload = {"email": email, "password": password, "repassword": password, "email_code": verify_code}
                            resp = session.post(f"{url}{self.REG_PATHS[0]}", json=payload, timeout=DEFAULT_TIMEOUT)
                            token = resp.json().get("data", {}).get("token") or resp.json().get("token")

                if token and len(str(token)) > 20:
                    if not str(token).startswith("Bearer"):
                        token = f"Bearer {token}"
                    session.headers["Authorization"] = token

                    self.auto_buy_free_plan(session, url)
                    sub_url = self.get_subscribe_url(session, url, token.replace("Bearer ", ""))

                    used, total, exp, txt = self.get_traffic_info(sub_url, session)
                    log = self.format_log(url, email, used, total, exp, sub_url)
                    logger.info(f"✅ 注册成功: {url}")
                    return self.extract_nodes(txt), log, sub_url

            except Exception as e:
                logger.debug(f"第 {attempt+1} 次尝试失败: {str(e)[:100]}")
                time.sleep(random.uniform(4, 9))   # 指数退避

        return [], f"[{url}]\nstatus  failed\nreason  多次注册失败\ntime  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n", ""

    # 其余方法（format_log、extract_nodes、f_size、run）与上一版本一致

    def run(self):
        # ... (保持上一版本的 run 方法)
        if not os.path.exists(URLS_FILE):
            logger.error(f"未找到 {URLS_FILE}")
            return

        with open(URLS_FILE, encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip().startswith('http')]
        urls = list(dict.fromkeys(urls))
        random.shuffle(urls)

        logger.info(f"开始处理 {len(urls)} 个机场 (并发 {MAX_WORKERS})...")

        all_nodes, all_logs, all_subs = [], [], []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            futures = {exe.submit(self.process_task, u): u for u in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    nodes, log, sub_url = future.result()
                    if nodes: all_nodes.extend(nodes)
                    if log: all_logs.append(log)
                    if sub_url: all_subs.append(sub_url)
                    status = "成功" if "buy  pass" in log else "失败"
                    print(f"[{status}] {url}")
                except Exception as e:
                    logger.error(f"处理 {url} 时异常: {e}")

        Path(NODES_FILE).write_text("\n".join(dict.fromkeys(all_nodes)), encoding='utf-8')
        Path(SUB_FILE).write_text("\n".join(dict.fromkeys(all_subs)), encoding='utf-8')
        Path(CACHE_FILE).write_text("".join(all_logs), encoding='utf-8')

        logger.info(f"任务完成！节点: {len(all_nodes)} | 订阅: {len(all_subs)}")


if __name__ == '__main__':
    commander = AirportCommander()
    commander.run()