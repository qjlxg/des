# coding=utf-8
import requests
import random
import string
import os

# 修改：完全从 trial.cfg 中加载机场链接，不使用硬编码备份
def load_home_urls():
    cfg_path = 'trial.cfg'
    urls = []
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过空行和以 [ 开头的说明行 (如 )
                    if line and not line.startswith('['):
                        urls.append(line)
            if urls:
                print(f"成功从 {cfg_path} 加载了 {len(urls)} 个机场地址")
            else:
                print(f"警告：{cfg_path} 中没有找到有效的网址。")
        except Exception as e:
            print(f"读取 {cfg_path} 出错: {e}")
    else:
        print(f"错误：找不到配置文件 {cfg_path}")
    
    return tuple(urls)

home_urls = load_home_urls()
try_sub = []

def get_sub_url():
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    for current_url in home_urls:
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
        
        # 兼容特定的 V2board 逻辑
        if any(x in current_url for x in ['xn--4gqu8thxjfje.com', 'seeworld.pro', 'jwckk.top', 'vvtestatiantian.top']):
            try:
                fan_res = requests.post(f'{current_url}/api/v1/passport/auth/register', data=form_data, headers=header)
                auth_data = fan_res.json()["data"]["auth_data"]
                
                fan_header = {
                    'Origin': current_url,
                    'Authorization': ''.join(auth_data),
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': header['User-Agent'],
                    'Referer': current_url,
                }
                # 尝试白嫖 1 号计划
                fan_res_n = requests.post(f'{current_url}/api/v1/user/order/save', headers=fan_header, data={'period': 'onetime_price', 'plan_id': '1'})
                requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no': fan_res_n.json()["data"]}, headers=fan_header)
                
                subscription_url = f'{current_url}/api/v1/client/subscribe?token={fan_res.json()["data"]["token"]}'
                try_sub.append(subscription_url)
                print(f"成功获取: {subscription_url}")
            except Exception as e:
                print(f"机场 {current_url} 注册/下单失败: {e}")
        else:
            try:
                response = requests.post(current_url + V2B_REG_REL_URL, data=form_data, headers=header)
                subscription_url = f'{current_url}/api/v1/client/subscribe?token={response.json()["data"]["token"]}'
                try_sub.append(subscription_url)
                print(f"成功获取: {subscription_url}")
            except Exception as e:
                print(f"机场 {current_url} 获取失败: {e}")

def save_result():
    if not try_sub:
        print("本次未获取到任何有效订阅链接。")
        return
    with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
        for url in try_sub:
            f.write(url + "\n")
    print(f"任务完成，共保存 {len(try_sub)} 条订阅。")

if __name__ == '__main__':
    if not home_urls:
        print("未获取到机场地址，请检查 trial.cfg 文件。任务终止。")
    else:
        print("========== 开始获取机场试用订阅 ==========")
        get_sub_url()
        save_result()
