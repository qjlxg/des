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
    """检测订阅状态及流量"""
    try:
        # 必须模拟真实客户端，否则很多机场直接返回 403
        headers = {
            'User-Agent': 'ClashforWindows/0.19.23',
            'Accept': '*/*'
        }
        res = requests.get(sub_url, headers=headers, timeout=12, verify=False)
        
        if res.status_code != 200:
            return f"HTTP {res.status_code}"
        
        # 尝试解密内容确认是否有节点
        try:
            content = base64.b64decode(res.text).decode('utf-8', errors='ignore')
            if not any(p in content for p in ['vmess://', 'ssr://', 'ss://', 'trojan://', 'vless://']):
                return "内容不含节点"
        except:
            return "非Base64格式"

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
        return f"探测异常: {str(e)[:30]}"

def process_airport(current_url, cache):
    # 1. 缓存校验
    if current_url in cache:
        c = cache[current_url]
        now = time.time()
        if (c['raw_expire'] == 0 or now < c['raw_expire']) and c['unused'] > 0.1:
            # 即使有缓存，也快速复核一下订阅是否依然存活
            check = get_sub_status(c['sub_url'])
            if isinstance(check, dict):
                print(f"复用有效缓存: {current_url} | 剩余:{c['unused']}GB")
                return current_url, c

    # 2. 注册流程
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    # 使用较新的 UA 减少被屏蔽概率
    header = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36',
        'Referer': current_url + '/'
    }
    email = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10)) + '@gmail.com'
    
    try:
        reg_res = requests.post(
            current_url + V2B_REG_REL_URL, 
            data={'email': email, 'password': 'autosub_v2b', 'invite_code': '', 'email_code': ''}, 
            headers=header, timeout=12, verify=False
        )
        
        # 诊断：如果返回 403，说明被 Cloudflare 拦截了
        if reg_res.status_code == 403:
            print(f"屏蔽: {current_url} (Cloudflare 403)")
            return current_url, None

        res_json = reg_res.json()
        if "data" in res_json and "token" in res_json["data"]:
            token = res_json["data"]["token"]
            sub_url = f'{current_url}/api/v1/client/subscribe?token={token}'
            
            # 自动领取计划 (针对特定站)
            # 如果你的 trial.cfg 里有很多这种站，建议把关键词加进去
            target_special = ['seeworld', 'jwckk', 'vvtest', 'huangjuexiao']
            if any(s in current_url for s in target_special):
                auth = res_json["data"].get("auth_data", "")
                f_h = {'Authorization': ''.join(auth), 'User-Agent': header['User-Agent']}
                order = requests.post(f'{current_url}/api/v1/user/order/save', headers=f_h, data={'period':'onetime_price','plan_id':'1'}, timeout=10, verify=False)
                if order.status_code == 200:
                    requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no':order.json().get("data")}, headers=f_h, timeout=10, verify=False)

            # 检测结果
            status = get_sub_status(sub_url)
            if isinstance(status, dict):
                info = {"sub_url": sub_url, **status}
                print(f"新注册成功: {current_url} | 流量:{status['unused']}GB")
                return current_url, info
            else:
                print(f"注册成功但无效: {current_url} ({status})")
        else:
            print(f"拒绝注册: {current_url} -> {res_json.get('message', '未知原因')}")
    except Exception as e:
        print(f"连接失败: {current_url}")
    
    return current_url, None

if __name__ == '__main__':
    cfg_path = 'trial.cfg'
    if not os.path.exists(cfg_path):
        print("未找到 trial.cfg"); exit()
    
    with open(cfg_path, 'r', encoding='utf-8') as f:
        home_urls = [l.strip() for l in f if l.strip() and not l.strip().startswith('[') and 'http' in l]
    
    cache = load_cache()
    new_cache = {}
    valid_subs = []

    print(f"========== 开始处理，目标总数: {len(home_urls)} ==========")
    
    # 调低并发数到 5，可以减少因请求过快被机场防火墙集体拉黑的概率
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
    
    print(f"========== 任务结束：有效订阅 {len(valid_subs)} 条 ==========")
