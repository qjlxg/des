# coding=utf-8
import requests
import random
import string
import os

# 修改点：完全从 trial.cfg 加载，增加调试日志
def load_home_urls():
    cfg_path = 'trial.cfg'
    urls = []
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 过滤空行、注释行以及不含 http 的非法行
                    if line and not line.startswith('[') and 'http' in line:
                        urls.append(line)
            if urls:
                print(f"成功加载配置：共找到 {len(urls)} 个网址")
            else:
                print(f"警告：{cfg_path} 存在但未发现有效网址（需包含 http）")
        except Exception as e:
            print(f"读取 {cfg_path} 发生错误: {e}")
    else:
        print(f"错误：未找到配置文件 {cfg_path}，请检查文件是否已上传至仓库根目录")
    
    return tuple(urls)

home_urls = load_home_urls()
try_sub = []

def get_sub_url():
    V2B_REG_REL_URL = '/api/v1/passport/auth/register'
    
    for current_url in home_urls:
        print(f"正在尝试注册机场: {current_url}") # 增加每一步的日志，防止“没动静”
        
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
        target_special = ['xn--4gqu8thxjfje.com', 'seeworld.pro', 'jwckk.top', 'vvtestatiantian.top']
        if any(x in current_url for x in target_special):
            try:
                fan_res = requests.post(f'{current_url}/api/v1/passport/auth/register', data=form_data, headers=header, timeout=15)
                auth_data = fan_res.json()["data"]["auth_data"]
                
                fan_header = {
                    'Origin': current_url,
                    'Authorization': ''.join(auth_data),
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': header['User-Agent'],
                    'Referer': current_url,
                }
                # 尝试白嫖计划
                fan_res_n = requests.post(f'{current_url}/api/v1/user/order/save', headers=fan_header, data={'period': 'onetime_price', 'plan_id': '1'}, timeout=15)
                requests.post(f'{current_url}/api/v1/user/order/checkout', data={'trade_no': fan_res_n.json()["data"]}, headers=fan_header, timeout=15)
                
                subscription_url = f'{current_url}/api/v1/client/subscribe?token={fan_res.json()["data"]["token"]}'
                try_sub.append(subscription_url)
                print(f"成功获取(特殊模式): {subscription_url}")
            except Exception as e:
                print(f"机场 {current_url} 特殊注册失败: {e}")
        else:
            try:
                response = requests.post(current_url + V2B_REG_REL_URL, data=form_data, headers=header, timeout=15)
                res_json = response.json()
                if "data" in res_json and "token" in res_json["data"]:
                    subscription_url = f'{current_url}/api/v1/client/subscribe?token={res_json["data"]["token"]}'
                    try_sub.append(subscription_url)
                    print(f"成功获取: {subscription_url}")
                else:
                    print(f"机场 {current_url} 返回异常: {res_json.get('message', '未知错误')}")
            except Exception as e:
                print(f"机场 {current_url} 常规注册失败: {e}")

def save_result():
    if not try_sub:
        print("========== 结果：未获取到任何有效订阅 ==========")
        return
    with open("trial_subscriptions.txt", "w", encoding="utf-8") as f:
        for url in try_sub:
            f.write(url + "\n")
    print(f"========== 任务完成：已保存 {len(try_sub)} 条订阅 ==========")

if __name__ == '__main__':
    if not home_urls:
        print("错误：网址列表为空，请检查 trial.cfg 内容是否正确。")
    else:
        print(f"========== 开始执行，目标机场总数: {len(home_urls)} ==========")
        get_sub_url()
        save_result()
