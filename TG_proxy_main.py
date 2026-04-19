# coding=utf-8
import base64
import json
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

MAX_WORKERS = 80                    # 根据你的网络情况调整（建议50-100）
DEFAULT_TIMEOUT = 8
MAIL_WAIT_TIMEOUT = 40
RATE_LIMIT_PER_HOST = 0.8           # 每秒最多请求数

# 随机User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]

# ====================== 日志配置 ======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(CACHE_FILE, mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def preprocess_captcha(img_bytes: bytes) -> bytes:
    """图像预处理，提升ddddocr识别率"""
    try:
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return img_bytes

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 自适应二值化 + 轻度去噪
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        
        _, buffer = cv2.imencode('.png', cleaned, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        return buffer.tobytes()
    except Exception:
        return img_bytes


class RateLimiter:
    def __init__(self, rate: float = 1.0):
        self.interval = 1.0 / rate
        self.last_request = {}

    def wait(self, host: str):
        now = time.time()
        if host in self.last_request:
            elapsed = now - self.last_request[host]
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
        self.last_request[host] = time.time()


class AirportCommander:
    def __init__(self):
        self.old_cache = self._parse_cache()
        self.ocr = ddddocr.DdddOcr(show_ad=False, beta=True)  # 开启beta模型，通常更准
        self.limiter = RateLimiter(RATE_LIMIT_PER_HOST)

        # 常用路径（支持v2board / SSRF等常见面板）
        self.REG_PATHS = ["/api/v1/passport/auth/register", "/api/v1/guest/passport/auth/register"]
        self.SEND_EMAIL_PATHS = ["/api/v1/passport/comm/sendEmailVerify", "/api/v1/guest/passport/comm/sendEmailVerify"]
        self.CAPTCHA_PATHS = ["/api/v1/passport/comm/captcha", "/api/v1/guest/passport/comm/captcha"]

        self.mail_apis = ["mail.tm", "mail.gw", "tempmail.lol"]  # 可继续扩展

    def _parse_cache(self) -> dict:
        """解析已有缓存中的成功记录"""
        data = {}
        if not os.path.exists(CACHE_FILE):
            return data

        try:
            content = Path(CACHE_FILE).read_text(encoding='utf-8')
            blocks = re.findall(r'\[(https?://[^\]]+)\]\n(.*?)\n\n', content, re.DOTALL)
            for url, body in blocks:
                lines = [line.strip() for line in body.strip().split('\n') if '  ' in line]
                info = {}
                for line in lines:
                    if '  ' in line:
                        k, v = line.split('  ', 1)
                        info[k.strip()] = v.strip()
                if 'sub_url' in info:
                    data[url.rstrip('/')] = info
        except Exception as e:
            logger.warning(f"解析缓存失败: {e}")
        return data

    def _get_session(self, base_url: str = "") -> crequests.Session:
        session = crequests.Session(impersonate=random.choice(["chrome124", "chrome123", "edge"]))

        session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": f"{base_url.rstrip('/')}/" if base_url else None,
            "Origin": base_url.rstrip('/') if base_url else None,
        })
        session.verify = False
        return session

    def create_temp_mail(self):
        """创建临时邮箱，支持多个后端"""
        random.shuffle(self.mail_apis)
        for api in self.mail_apis:
            try:
                s = crequests.Session(verify=False)
                domain_resp = s.get(f"https://api.{api}/domains", timeout=DEFAULT_TIMEOUT)
                domain = domain_resp.json()['hydra:member'][0]['domain']

                email = f"{''.join(random.choices(string.ascii_lowercase + string.digits, k=11))}@{domain}"
                password = "Pass" + ''.join(random.choices(string.digits, k=7))

                reg_res = s.post(f"https://api.{api}/accounts",
                                 json={"address": email, "password": password},
                                 timeout=DEFAULT_TIMEOUT)

                if reg_res.status_code == 201:
                    token_resp = s.post(f"https://api.{api}/token",
                                        json={"address": email, "password": password})
                    token = token_resp.json().get('token')
                    return email, token, api
            except Exception:
                continue
        return None, None, None

    def wait_for_verification_code(self, mail_token: str, mail_api: str, timeout: int = MAIL_WAIT_TIMEOUT):
        s = crequests.Session(verify=False)
        s.headers.update({"Authorization": f"Bearer {mail_token}"})

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                msgs = s.get(f"https://api.{mail_api}/messages", timeout=6).json().get('hydra:member', [])
                for msg in msgs:
                    subject = msg.get('subject', '').lower()
                    if any(k in subject for k in ['验证码', 'code', 'verification', '验证']):
                        detail = s.get(f"https://api.{mail_api}/messages/{msg['id']}", timeout=6).json()
                        text = detail.get('text') or detail.get('intro') or ''
                        code_match = re.search(r'(\d{4,8})', text)
                        if code_match:
                            return code_match.group(1)
            except:
                pass
            time.sleep(2.5)
        return None

    def get_captcha(self, session: crequests.Session, base_url: str) -> str | None:
        for path in self.CAPTCHA_PATHS:
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
                code = self.ocr.classification(processed)
                if code and len(code) >= 4:
                    return code.strip()
            except:
                continue
        return None

    def try_register(self, session: crequests.Session, base_url: str, email: str, password: str):
        """尝试多种注册方式，提高成功率"""
        payloads = [
            {"email": email, "password": password, "repassword": password},
            {"email": email, "password": password, "repassword": password, "invite_code": ""},
        ]

        for reg_path in self.REG_PATHS:
            for payload in payloads:
                for is_json in [True, False]:
                    try:
                        if is_json:
                            resp = session.post(f"{base_url}{reg_path}", json=payload, timeout=DEFAULT_TIMEOUT)
                        else:
                            resp = session.post(f"{base_url}{reg_path}", data=payload, timeout=DEFAULT_TIMEOUT)

                        if resp.status_code in (200, 201):
                            data = resp.json()
                            token = data.get("data", {}).get("token") or data.get("token")
                            if token:
                                return token, data.get("message", "")

                        # 处理需要验证码或邮箱验证的情况
                        msg = str(data.get("message", "")).lower()
                        if "captcha" in msg:
                            captcha_code = self.get_captcha(session, base_url)
                            if captcha_code:
                                payload["captcha_code"] = captcha_code
                                continue  # 重试本次payload

                        if any(k in msg for k in ["email", "邮箱", "code", "验证码"]):
                            return "NEED_EMAIL_VERIFY", msg

                    except Exception:
                        continue
        return None, None

    def auto_buy_free_plan(self, session: crequests.Session, base_url: str) -> bool:
        """智能购买免费或最低价流量包"""
        for fetch_path in ["/api/v1/user/plan/fetch", "/api/v1/guest/plan/fetch"]:
            try:
                resp = session.get(f"{base_url}{fetch_path}", timeout=DEFAULT_TIMEOUT)
                plans = resp.json().get("data", [])

                # 优先选择免费（price=0）的计划
                free_plans = [p for p in plans if any(
                    str(p.get(k, 1)).strip() == '0' 
                    for k in p.keys() if '_price' in k and k != 'reset_price'
                )]

                if free_plans:
                    # 选流量最多的免费计划
                    best = max(free_plans, key=lambda x: x.get('transfer_enable', 0))
                    plan_id = best['id']
                    cycle = next((k.replace('_price', '') for k, v in best.items() 
                                if '_price' in k and str(v) == '0'), 'month')

                    # 创建订单
                    order_res = session.post(f"{base_url}/api/v1/user/order/save",
                                           json={"plan_id": plan_id, "cycle": cycle})
                    trade_no = order_res.json().get("data")

                    if trade_no:
                        session.post(f"{base_url}/api/v1/user/order/checkout",
                                   json={"trade_no": trade_no, "method": 1})
                        logger.info(f"成功购买免费计划: {best.get('name', 'Unknown')}")
                        return True
            except:
                continue
        return False

    def get_subscribe_url(self, session: crequests.Session, base_url: str, token: str) -> str:
        """优先获取官方订阅链接"""
        default_sub = f"{base_url}/api/v1/client/subscribe?token={token}"
        
        try:
            # 尝试获取用户设置的订阅短链接
            resp = session.get(f"{base_url}/api/v1/user/getSubscribe", timeout=DEFAULT_TIMEOUT)
            data = resp.json().get("data")
            if data and isinstance(data, str) and data.startswith("http"):
                return data
        except:
            pass
        return default_sub

    def get_traffic_info(self, sub_url: str, base_url: str, session=None):
        """获取流量信息"""
        try:
            s = session or self._get_session()
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            resp = s.get(sub_url, headers=headers, timeout=DEFAULT_TIMEOUT + 5)

            header = resp.headers.get('subscription-userinfo', '')
            u = d = t = e = 0

            if header:
                for item in header.split(';'):
                    if '=' in item:
                        k, v = item.split('=', 1)
                        k = k.strip()
                        if k == 'upload': u = int(v)
                        elif k == 'download': d = int(v)
                        elif k == 'total': t = int(v)
                        elif k == 'expire': e = int(v)

            used = u + d
            return used, t, e, resp.text
        except:
            return 0, 0, 0, ""

    def format_success_log(self, url: str, email: str, used: int, total: int, expire: int, sub_url: str):
        exp_str = datetime.fromtimestamp(expire).strftime('%Y-%m-%d %H:%M') if expire > 0 else "永久"
        remain = max(0, total - used)
        return (f"[{url}]\n"
                f"buy  pass\n"
                f"email  {email}\n"
                f"sub_info  {self.f_size(used)}  {self.f_size(total)}  {exp_str}  "
                f"(剩余 {self.f_size(remain)})\n"
                f"sub_url  {sub_url}\n"
                f"time  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"type  v2board\n\n")

    def f_size(self, size: int) -> str:
        if not size:
            return "0B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}PB"

    def process_task(self, url: str):
        url = url.rstrip('/')
        logger.info(f"开始处理: {url}")

        # DNS预检查
        try:
            host = re.search(r'https?://([^/]+)', url).group(1).split(':')[0]
            socket.gethostbyname(host)  # 触发DNS解析
        except Exception as e:
            return [], f"[{url}]\nstatus  failed\nreason  DNS失败: {e}\n\n", ""

        session = self._get_session(url)

        # 优先使用缓存中的有效订阅
        if url in self.old_cache:
            info = self.old_cache[url]
            sub_url = info.get('sub_url')
            if sub_url:
                used, total, exp, sub_txt = self.get_traffic_info(sub_url, url, session)
                if total > 0:
                    nodes = self.extract_nodes(sub_txt)
                    log = self.format_success_log(url, info.get('email', ''), used, total, exp, sub_url)
                    logger.info(f"缓存有效 → {url}")
                    return nodes, log, sub_url

        # ==================== 注册流程 ====================
        email_base = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        password = "Pass" + ''.join(random.choices(string.digits, k=8))

        for _ in range(3):  # 最多重试3次
            try:
                email, mail_token, mail_api = self.create_temp_mail()
                if not email:
                    continue

                token, msg = self.try_register(session, url, email, password)

                if token == "NEED_EMAIL_VERIFY" and mail_token:
                    # 发送邮箱验证码
                    cap = self.get_captcha(session, url)
                    session.post(f"{url}/api/v1/passport/comm/sendEmailVerify",
                                 json={"email": email, "captcha_code": cap or ""})

                    verify_code = self.wait_for_verification_code(mail_token, mail_api)
                    if verify_code:
                        # 重新注册，带上邮箱验证码
                        payload = {"email": email, "password": password, "repassword": password, "email_code": verify_code}
                        reg_resp = session.post(f"{url}{self.REG_PATHS[0]}", json=payload)
                        token = reg_resp.json().get("data", {}).get("token")

                if token and isinstance(token, str) and len(token) > 20:
                    session.headers["Authorization"] = f"Bearer {token}" if not token.startswith("Bearer") else token

                    # 自动购买免费计划
                    self.auto_buy_free_plan(session, url)

                    # 获取订阅链接
                    sub_url = self.get_subscribe_url(session, url, token)

                    used, total, exp, sub_txt = self.get_traffic_info(sub_url, url, session)
                    log = self.format_success_log(url, email, used, total, exp, sub_url)

                    logger.info(f"注册成功: {url}")
                    return self.extract_nodes(sub_txt), log, sub_url

            except Exception as e:
                logger.debug(f"注册尝试失败: {e}")
                time.sleep(random.uniform(1.5, 3.5))

        # 失败日志
        return [], f"[{url}]\nstatus  failed\nreason  注册失败\n time  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n", ""

    def extract_nodes(self, content: str):
        if not content:
            return []
        pattern = r'(vmess|vless|ss|ssr|trojan|hysteria2?|hy2|tuic|anytls)://[^\s\'"<>]+'
        nodes = re.findall(pattern, content, re.I)
        return list(dict.fromkeys(nodes))  # 去重并保持顺序

    def run(self):
        if not os.path.exists(URLS_FILE):
            logger.error(f"未找到 {URLS_FILE}")
            return

        urls = [line.strip() for line in open(URLS_FILE, encoding='utf-8') 
                if line.strip().startswith('http')]
        urls = list(dict.fromkeys(urls))   # 去重
        random.shuffle(urls)

        logger.info(f"开始处理 {len(urls)} 个机场...")

        all_nodes = []
        all_logs = []
        all_subs = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_url = {executor.submit(self.process_task, u): u for u in urls}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
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
                    logger.error(f"处理 {url} 时发生异常: {e}")

        # 保存结果
        Path(NODES_FILE).write_text("\n".join(dict.fromkeys(all_nodes)), encoding='utf-8')
        Path(SUB_FILE).write_text("\n".join(dict.fromkeys(all_subs)), encoding='utf-8')
        
        # 覆盖缓存为本次成功记录
        Path(CACHE_FILE).write_text("".join(all_logs), encoding='utf-8')

        logger.info(f"任务完成！节点数: {len(all_nodes)} | 订阅数: {len(all_subs)}")


if __name__ == '__main__':
    commander = AirportCommander()
    commander.run()