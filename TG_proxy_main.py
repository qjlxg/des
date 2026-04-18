# coding=utf-8
import requests
import random
import string
import os
import concurrent.futures
import base64
import urllib3
import time
import json

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CACHE_FILE = 'sub_cache.json'

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_cache(cache_data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, indent=4, ensure_ascii=False)

def bytes_to_gb(size_bytes):
    if size_bytes is None: return 0
    return round(float(size_bytes) / (1024**3), 2)

def get_sub_status(sub_url):
    """检测订阅状态及流量，模拟真实客户端绕过 403"""
    try:
        headers = {
            'User-Agent': 'ClashforWindows/0.19.23 (Windows NT 10.0; Win64; x64)',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        res = requests.get(sub_url, headers=headers, timeout=15, verify=False)
        
        if res.status_code != 200:
            return f"HTTP {res.status_code}"
        
        # 检查 Base64 内容
        try:
            raw_content = res.text.strip()
            content = base64.b64decode(raw_content).decode('utf-8', errors='ignore')
            if not any(p in content for p in ['vmess://', 'ssr://', 'ss://', 'trojan://', 'vless://']):
                return "内容为空(未激活套餐)"
        except:
            return "非标准订阅格式"

        # 提取流量
        info = res.headers.get('Subscription-Userinfo', '')
        status = {"total": 0, "unused": 0, "expire": "永不过期", "raw_expire": 0}
        if info:
            parts = dict(item.split('=') for item in info.split('; ') if '=' in item)
            u = int(parts.get('u', 0))
            d = int(parts.get('d', 0))
            total = int(parts.get('total', 0))
            unused = total - u - d
            status["total"] = bytes_to_gb(total)
            status["unused"] = bytes_to_gb(unused)
            raw_exp = int(parts.get('expire', 0))
            if raw_exp > 0:
                status["raw_expire"] = raw_exp
                status["expire"] = time.strftime('%Y-%m-%d', time.localtime(raw_exp))
        return status
    except Exception as e:
        return "连接超时"

def process_airport(current_url, cache):
    # 1. 缓存逻辑
    if current_url in cache:
        c = cache[current_url]
        if (c['raw_expire'] == 0 or time.time() < c['raw_expire']) and c['unused'] > 0.1:
            print(f"复用缓存: {current_url} | 剩余:{c['unused']}GB")
            return current_url, c

    # 2. 注册逻辑
    email = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10)) + '@gmail.com'
    password = 'autosub_v2b'
    header = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        'Referer': current_url + '/'
    }
    
    try:
        # 注册请求
        reg_res = requests.post(
            f"{current_url}/api/v1/passport/auth/register",
            data={'email': email, 'password': password, 'invite_code': '', 'email_code': ''},
            headers=header, timeout=12, verify=False
        )
        
        res_json = reg_res.json()
        if "data" in res_json and "token" in res_json["data"]:
            token = res_json["data"]["token"]
            auth_data = res_json["data"].get("auth_data", "")
            sub_url = f'{current_url}/api/v1/client/subscribe?token={token}'
            
            # 3. 通用 0 元下单激活逻辑
            if auth_data:
                auth_header = {
                    'Authorization': ''.join(auth_data) if isinstance(auth_data, list) else auth_data,
                    'User-Agent': header['User-Agent'],
                    'Content-Type': 'application/x-www-form-urlencoded'
                }
                # 尝试常见的试用 Plan ID (1, 2)
                for pid in ['1', '2']:
                    order_res = requests.post(f'{current_url}/api/v1/user/order/save', 
                                            headers=auth_header, data={'period':'onetime_price','plan_id': pid}, 
                                            timeout=5, verify=False)
                    if order_res.status_code == 200:
                        trade_no = order_res.json().get("data")
                        requests.post(f'{current_url}/api/v1/user/order/checkout', 
                                    data={'trade_no': trade_no}, headers=auth_header, timeout=5, verify=False)
            
            # 4. 最终状态检测
            status = get_sub_status(sub_url)
            if isinstance(status, dict):
                info = {"sub_url": sub_url, **status}
                print(f"成功: {current_url} | 剩余:{status['unused']}GB")
                return current_url, info
            else:
                print(f"失效: {current_url} ({status})")
    except:
        pass
    return current_url, None

if __name__ == '__main__':
    # 读取 trial.cfg
    if not os.path.exists('trial.cfg'):
        print("未找到 trial.cfg"); exit()
    
    with open('trial.cfg', 'r', encoding='utf-8') as f:
        home_urls = []
        for l in f:
            l = l.strip()
            # 兼容带有 [source] 标签的行
            if 'http' in l:
                clean_url = l.split(' ')[-1] if ' ' in l else l
                home_urls.append(clean_url.rstrip('/'))

    cache = load_cache()
    new_cache = {}
    valid_subs = []

    print(f"========== 开始处理，共 {len(home_urls)} 个机场 ==========")
    
    # 限制并发为 5，防止被防火墙拉黑
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_airport, url, cache): url for url in home_urls}
        for future in concurrent.futures.as_completed(futures):
            url, info = future.result()
            if info:
                new_cache[url] = info
                valid_subs.append(f"{info['sub_url']}    # 剩余:{info['unused']}GB 过期:{info['expire']}")

    save_cache(new_cache)
    with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(valid_subs))
    
    print(f"========== 任务结束：筛选出 {len(valid_subs)} 条有效订阅 ==========")
