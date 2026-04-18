# coding=utf-8
import requests
import random
import string
import os

# 试用机场链接 (保持原封不动)
home_urls = (
    'https://xn--30rs3bu7r87f.com',
    'https://seeworld.pro',          # 5T   永久
    'https://fastestcloud.xyz',      # 2G   1天
    'https://www.ckcloud.xyz',       # 1G   1天
)

# 存储成功获取的订阅链接
try_sub = []

def get_sub_url():
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    times = 1
    for current_url in home_urls:
        i = 0
        while i < times:
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
            # 原始脚本中的特殊逻辑判断
            if current_url == 'https://xn--4gqu8thxjfje.com' or current_url == 'https://seeworld.pro'  or current_url == 'https://www.jwckk.top'or current_url == 'https://vvtestatiantian.top':
                try:
                    fan_res = requests.post(
                        f'{current_url}/api/v1/passport/auth/register', data=form_data, headers=header)
                    auth_data = fan_res.json()["data"]["auth_data"]
                    
                    fan_header = {
                        'Origin': current_url,
                        'Authorization': ''.join(auth_data),
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Connection': 'keep-alive',
                        'User-Agent': 'Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Mobile/15E148 Safari/604.1',
                        'Referer': current_url,
                    }
                    fan_data = {
                        'period': 'onetime_price',
                        'plan_id': '1',
                    }
                    fan_res_n = requests.post(
                        f'{current_url}/api/v1/user/order/save', headers=fan_header, data=fan_data)
                    
                    fan_data_n = {
                        'trade_no':fan_res_n.json()["data"],
                    }
                    requests.post(
                        f'{current_url}/api/v1/user/order/checkout', data=fan_data_n, headers=fan_header)
                    
                    subscription_url = f'{current_url}/api/v1/client/subscribe?token={fan_res.json()["data"]["token"]}'
                    try_sub.append(subscription_url)
                    print("add:" + subscription_url)
                except Exception as result:
                    print(f"Error at {current_url}: {result}")
                    break
            else:
                try:
                    response = requests.post(
                        current_url+V2B_REG_REL_URL, data=form_data, headers=header)
                    subscription_url = f'{current_url}/api/v1/client/subscribe?token={response.json()["data"]["token"]}'
                    try_sub.append(subscription_url)
                    print("add:" + subscription_url)
                except Exception as e:
                    print(f"获取订阅失败 {current_url}:", e)
            i += 1

def save_to_single_file():
    if not try_sub:
        print("未获取到任何试用订阅，不生成文件。")
        return
    
    filename = "trial_subscriptions.txt"
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            for url in try_sub:
                f.write(url + '\n')
        print(f"========== 任务结束 ==========")
        print(f"共成功获取 {len(try_sub)} 个试用订阅，已保存至: {filename}")
    except Exception as e:
        print(f"写入文件出错: {e}")

if __name__ == '__main__':
    print("========== 开始获取机场试用订阅 ==========")
    get_sub_url()
    print("========== 开始保存结果 ==========")
    save_to_single_file()
