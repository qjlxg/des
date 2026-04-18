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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CACHE_FILE = 'sub_cache.json'

# --- 缓存管理 ---
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache_data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, indent=4, ensure_ascii=False)

# --- 工具函数 ---
def bytes_to_gb(size_bytes):
    if size_bytes is None: return 0
    return round(float(size_bytes) / (1024**3), 2)

# --- 订阅状态探测 (包含流量与有效期) ---
def get_sub_status(sub_url):
    try:
        headers = {'User-Agent': 'ClashforWindows/0.19.23'}
        res = requests.get(sub_url, headers=headers, timeout=10, verify=False)
        if res.status_code != 200: return None
        
        # 检查是否包含节点内容
        try:
            content = base64.b64decode(res.text).decode('utf-8', errors='ignore')
            if not any(p in content for p in ['vmess://', 'ssr://', 'ss://', 'trojan://', 'vless://']):
                return None
        except: return None

        # 提取流量信息
        info = res.headers.get('Subscription-Userinfo', '')
        status = {"total": 0, "unused": 0, "expire": "永不过期", "raw_expire": 0}
        if info:
            parts = dict(item.split('=') for item in info.split('; ') if '=' in item)
            total = int(parts.get('total', 0))
            unused = total - int(parts.get('u', 0)) - int(parts.get('d', 0))
            status["total"] = bytes_to_gb(total)
            status["unused"] = bytes_to_gb(unused)
            raw_exp = int(parts.get('expire', 0))
            if raw_exp > 0:
                status["raw_expire"] = raw_exp
                status["expire"] = time.strftime('%Y-%m-%d', time.localtime(raw_exp))
        return status
    except:
        return None

# --- 主处理逻辑 ---
def process_airport(current_url, cache):
    # 1. 检查缓存
    if current_url in cache:
        cached_info = cache[current_url]
        # 检查是否过期或流量耗尽 (预留 0.1GB 缓冲)
        now = time.time()
        is_expired = cached_info['raw_expire'] > 0 and now > cached_info['raw_expire']
        if not is_expired and cached_info['unused'] > 0.1:
            print(f"复用缓存: {current_url} | 剩余:{cached_info['unused']}GB")
            return current_url, cached_info

    # 2. 缓存失效或不存在，执行注册
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    header = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'}
    email = ''.join(random.choice(string.ascii_letters+string.digits) for _ in range(12))+'@gmail.com'
    
    try:
        reg_res = requests.post(current_url + V2B_REG_REL_URL, data={'email':email,'password':'autosub_v2b'}, headers=header, timeout=10, verify=False)
        res_json = reg_res.json()
        if "data" in res_json and "token" in res_json["data"]:
            token = res_json["data"]["token"]
            sub_url = f'{current_url}/api/v1/client/subscribe?token={token}'
            
            # 兼容特定白嫖逻辑
            if any(x in current_url for x in ['seeworld.pro', 'jwckk.top']):
                auth_data = res_json["data"].get("auth_data", "")
                f_h = {'Authorization': ''.join(auth_data), 'User-Agent': header['User-Agent']}
                order = requests.post(f'{current_url}/api/v1/user/order/save', headers=f_h, data={'period':'onetime_price','plan_id':'1'}, timeout=10, verify=False)
                if order.status_code == 200:
                    requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no':order.json().get("data")}, headers=f_h, timeout=10, verify=False)
            
            # 检测新订阅状态
            status = get_sub_status(sub_url)
            if status:
                new_info = {"sub_url": sub_url, **status}
                print(f"新注册成功: {current_url} | 流量:{status['unused']}GB")
                return current_url, new_info
    except:
        pass
    return current_url, None

if __name__ == '__main__':
    # 加载配置与缓存
    cfg_path = 'trial.cfg'
    if not os.path.exists(cfg_path):
        print("未找到 trial.cfg"); exit()
    
    with open(cfg_path, 'r', encoding='utf-8') as f:
        home_urls = [l.strip() for l in f if l.strip() and not l.strip().startswith('[') and 'http' in l]
    
    cache = load_cache()
    new_cache = {}
    valid_subs = []

    print(f"========== 开始处理，目标总数: {len(home_urls)} ==========")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_url = {executor.submit(process_airport, url, cache): url for url in home_urls}
        for future in concurrent.futures.as_completed(future_to_url):
            url, info = future.result()
            if info:
                new_cache[url] = info
                valid_subs.append(f"{info['sub_url']}    # 剩余:{info['unused']}GB 过期:{info['expire']}")

    # 保存新缓存与结果
    save_cache(new_cache)
    with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(valid_subs))
    
    print(f"========== 任务结束：有效订阅 {len(valid_subs)} 条 ==========")
