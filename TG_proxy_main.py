# coding=utf-8
import requests
import random
import string
import os
import concurrent.futures
import base64
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 配置加载 ---
def load_home_urls():
    cfg_path = 'trial.cfg'
    urls = []
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('[') and 'http' in line:
                        urls.append(line)
        except Exception as e:
            print(f"读取配置错误: {e}")
    return tuple(urls)

# --- 实用工具：字节转GB ---
def bytes_to_gb(size_bytes):
    if size_bytes is None: return "未知"
    gb = float(size_bytes) / (1024**3)
    return f"{gb:.2f}GB"

# --- 订阅有效性检测 ---
def check_subscription(sub_url):
    try:
        # 增加请求头模拟 Clash 客户端，否则有些机场不返回流量信息
        headers = {'User-Agent': 'ClashforWindows/0.19.23'}
        res = requests.get(sub_url, headers=headers, timeout=15, verify=False)
        
        if res.status_code != 200:
            return None
        
        # 1. 提取流量信息 (从 Header 提取)
        info = res.headers.get('Subscription-Userinfo', '')
        usage_info = "无流量信息"
        if info:
            parts = dict(item.split('=') for item in info.split('; ') if '=' in item)
            total = int(parts.get('total', 0))
            unused = total - int(parts.get('u', 0)) - int(parts.get('d', 0))
            expire = parts.get('expire', '永不过期')
            if expire != '永不过期':
                expire = time.strftime('%Y-%m-%d', time.localtime(int(expire)))
            usage_info = f"剩余:{bytes_to_gb(unused)}/总:{bytes_to_gb(total)} 过期:{expire}"

        # 2. 检查是否有节点内容 (解密 Base64)
        try:
            content = base64.b64decode(res.text).decode('utf-8', errors='ignore')
            # 简单判断是否包含常见协议头
            if any(proto in content for proto in ['vmess://', 'ssr://', 'ss://', 'trojan://', 'vless://']):
                return usage_info
            else:
                return "订阅为空(无节点)"
        except:
            return "无法解析内容"
            
    except Exception:
        return None

# --- 主注册逻辑 ---
def process_register(current_url):
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    header = {
        'Referer': current_url,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    }
    email = ''.join(random.choice(string.ascii_letters+string.digits) for _ in range(12))+'@gmail.com'
    form_data = {'email': email, 'password': 'autosub_v2b', 'invite_code': '', 'email_code': ''}

    try:
        # 注册
        response = requests.post(current_url + V2B_REG_REL_URL, data=form_data, headers=header, timeout=10, verify=False)
        res_json = response.json()
        
        if "data" in res_json and "token" in res_json["data"]:
            token = res_json["data"]["token"]
            sub_url = f'{current_url}/api/v1/client/subscribe?token={token}'
            
            # 针对特定机场的白嫖下单逻辑 (Plan ID 1)
            target_special = ['xn--4gqu8thxjfje.com', 'seeworld.pro', 'jwckk.top', 'vvtestatiantian.top']
            if any(x in current_url for x in target_special):
                auth_data = res_json["data"].get("auth_data", "")
                fan_header = {'Authorization': ''.join(auth_data), 'User-Agent': header['User-Agent']}
                fan_res_n = requests.post(f'{current_url}/api/v1/user/order/save', headers=fan_header, data={'period': 'onetime_price', 'plan_id': '1'}, timeout=10, verify=False)
                if fan_res_n.status_code == 200:
                    requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no': fan_res_n.json().get("data")}, headers=fan_header, timeout=10, verify=False)
            
            # --- 新增检测环节 ---
            status = check_subscription(sub_url)
            if status and "订阅为空" not in status:
                print(f"有效: {current_url} | {status}")
                return f"{sub_url}    # {status}"
            else:
                print(f"无效: {current_url} ({status if status else '检测失败'})")
        else:
            print(f"失败: {current_url} -> {res_json.get('message', '注册失败')}")
    except:
        pass
    return None

if __name__ == '__main__':
    home_urls = load_home_urls()
    if not home_urls:
        print("未找到网址。")
    else:
        print(f"========== 开始并发检测，总数: {len(home_urls)} ==========")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_register, home_urls))
        
        try_sub = [r for r in results if r]
        
        if try_sub:
            with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
                for item in try_sub:
                    f.write(item + "\n")
            print(f"========== 任务完成：筛选出 {len(try_sub)} 条有效订阅 ==========")
        else:
            print("========== 结果：未发现任何带流量的有效订阅 ==========")
