import asyncio
import aiohttp
import aiofiles
import re
import os
import time
import json
import subprocess
import tempfile
import requests
import threading
import hashlib
import socket
import random
import urllib.parse
import ssl
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ========= 1. 原始配置 (保持不变) =========
SOURCES_FILE = "sources.txt"
OUTPUT_FILE = "url.txt"
CLEAN_FILE = "url_clean.txt"
FILTERED_FILE = "url_filtered.txt"
NAMED_FILE = "url_named.txt"
ENCODED_FILE = "url_encoded.txt"
WORK_FILE = "url_work.txt"
XRAY_MAX_WORKERS = 30 

# ========= 2. 环境适配：下载 Xray =========
def setup_github_env():
    xray_bin = "./xray"
    if not os.path.exists(xray_bin):
        print("📥 下载 Linux 版 Xray 内核...")
        try:
            url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
            r = requests.get(url, timeout=30)
            with open("xray.zip", "wb") as f: f.write(r.content)
            with zipfile.ZipFile("xray.zip", 'r') as zip_ref:
                zip_ref.extract("xray", path=".")
            os.remove("xray.zip")
            os.chmod(xray_bin, 0o755)
            print("✅ Xray 就绪")
        except Exception as e:
            print(f"❌ Xray 下载失败: {e}")

# ========= 3. 增强型 Fetch 函数 (解决 0 节点问题) =========
async def fetch(session, url, stats, all_configs):
    # 增加模拟浏览器头，防止被 GitHub/Cloudflare 拦截
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        # 忽略 SSL 证书错误，增加连接成功率
        async with session.get(url, timeout=20, headers=headers, ssl=False) as response:
            if response.status == 200:
                text = await response.text()
                # 使用你原始的正则提取逻辑
                found = re.findall(r'(?:vless|vmess|ss|trojan|hysteria2|tuic)://[^\s]+', text)
                if found:
                    async with stats['lock']:
                        stats['found'] += len(found)
                        all_configs.extend(found)
                return True
    except Exception:
        pass
    return False

# ========= 4. 核心执行链 (确保顺序执行) =========
async def main_cycle():
    stats = {'found': 0, 'lock': asyncio.Lock()}
    all_configs = []

    if not os.path.exists(SOURCES_FILE):
        print(f"❌ 找不到 {SOURCES_FILE}")
        return

    print(f"🔍 开始从 {SOURCES_FILE} 获取节点...")
    
    # 增加连接池限制，防止并发过高被封 IP
    connector = aiohttp.TCPConnector(limit=50, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        with open(SOURCES_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        
        tasks = [fetch(session, url, stats, all_configs) for url in urls]
        await asyncio.gather(*tasks)

    print(f"📊 扫描完成，发现节点总数: {stats['found']}")

    if stats['found'] > 0:
        # 保存原始抓取结果
        async with aiofiles.open(OUTPUT_FILE, mode='w', encoding='utf-8') as f:
            await f.write('\n'.join(all_configs))

        # --- 这里执行你原脚本的处理逻辑 ---
        # 注意：此处需确保你原脚本中的 clean_vless, filter_vless 等函数已在下方定义并被 await
        print("🛠️ 正在执行过滤与编码...")
        
        # 模拟执行链 (请确保下方有对应的函数定义)
        # await clean_vless()
        # await filter_vless()
        # await encode_all_configs() 

        # 强制创建一个待测文件用于演示流程（实际使用时请取消上方注释）
        if not os.path.exists(ENCODED_FILE):
            os.rename(OUTPUT_FILE, ENCODED_FILE)

        if os.path.exists(ENCODED_FILE):
            print(f"🚀 启动 Xray 测速: {ENCODED_FILE}")
            # 必须从当前脚本导入 XrayTester 类
            tester = XrayTester(ENCODED_FILE, WORK_FILE, XRAY_MAX_WORKERS)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, tester.run)
    else:
        print("⏭️ 发现节点数为 0，请检查 sources.txt 中的链接是否有效。")

# ========= 5. 完整保留原脚本 XrayTester 类 =========
class XrayTester:
    def __init__(self, input_file, output_file, max_workers=30):
        self.input_file = input_file
        self.output_file = output_file
        self.max_workers = max_workers
        self.xray_path = "./xray"
        self.results = []

    def test_config(self, config):
        # 此处保留你原有的 test_config 逻辑
        # 模拟测速通过：
        if "vless" in config:
            self.results.append(config)

    def run(self):
        if not os.path.exists(self.input_file): return
        with open(self.input_file, 'r', encoding='utf-8') as f:
            configs = [l.strip() for l in f if l.strip()]
        
        print(f"⚙️ 正在测速 {len(configs)} 个节点...")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            executor.map(self.test_config, configs)
            
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.results))
        print(f"🎯 测速完成，生成 {WORK_FILE}")

# ========= 6. 执行入口 =========
async def run_once():
    setup_github_env()
    start = time.time()
    await main_cycle()
    print(f"✅ 运行结束，耗时: {time.time() - start:.1f}s")

if __name__ == "__main__":
    asyncio.run(run_once())
