# coding=utf-8
import requests
import random
import string
import os
import concurrent.futures # 引入并发库

# 禁用 SSL 安全警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
            if urls:
                print(f"成功加载配置：共找到 {len(urls)} 个网址")
        except Exception as e:
            print(f"读取 {cfg_path} 错误: {e}")
    return tuple(urls)

home_urls = load_home_urls()
try_sub = []

def process_register(current_url):
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    header = {
        'Referer': current_url,
        'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Mobile/15E148 Safari/604.1',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    form_data = {
        'email': ''.join(random.choice(string.ascii_letters+string.digits) for _ in range(12))+'@gmail.com',
        'password': 'autosub_v2b',
        'invite_code': '',
        'email_code': ''
    }

    # 尝试注册逻辑
    try:
        # verify=False 忽略 SSL 证书错误
        response = requests.post(current_url + V2B_REG_REL_URL, data=form_data, headers=header, timeout=10, verify=False)
        res_json = response.json()
        
        if "data" in res_json and "token" in res_json["data"]:
            token = res_json["data"]["token"]
            
            # 针对特定机场的下单逻辑
            target_special = ['xn--4gqu8thxjfje.com', 'seeworld.pro', 'jwckk.top', 'vvtestatiantian.top']
            if any(x in current_url for x in target_special):
                auth_data = res_json["data"].get("auth_data", "")
                fan_header = {
                    'Origin': current_url,
                    'Authorization': ''.join(auth_data),
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': header['User-Agent'],
                    'Referer': current_url,
                }
                fan_res_n = requests.post(f'{current_url}/api/v1/user/order/save', headers=fan_header, data={'period': 'onetime_price', 'plan_id': '1'}, timeout=10, verify=False)
                requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no': fan_res_n.json().get("data")}, headers=fan_header, timeout=10, verify=False)
            
            sub_url = f'{current_url}/api/v1/client/subscribe?token={token}'
            print(f"成功: {sub_url}")
            return sub_url
        else:
            msg = res_json.get('message', '未知错误')
            print(f"失败: {current_url} -> {msg}")
    except Exception as e:
        print(f"错误: {current_url} -> 无法连接或格式错误")
    return None

def save_result():
    if not try_sub:
        print("========== 结果：未获取到任何有效订阅 ==========")
        return
    with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
        for url in try_sub:
            if url: f.write(url + "\n")
    print(f"========== 任务完成：已保存 {len(try_sub)} 条订阅 ==========")

if __name__ == '__main__':
    if not home_urls:
        print("未发现有效网址。")
    else:
        print(f"========== 开始并发执行，总数: {len(home_urls)} ==========")
        # 使用线程池并发执行，max_workers 可根据需要调整
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(process_register, home_urls))
        
        # 过滤掉 None 结果
        try_sub = [r for r in results if r]
        save_result()
