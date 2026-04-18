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
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========= 1. 配置与变量 (原封不动) =========
SOURCES_FILE = "sources.txt"
OUTPUT_FILE = "url.txt"
CLEAN_FILE = "url_clean.txt"
FILTERED_FILE = "url_filtered.txt"
NAMED_FILE = "url_named.txt"
ENCODED_FILE = "url_encoded.txt"
WORK_FILE = "url_work.txt"
LOG_FILE = "log.txt"
PROCESSED_FILE = "processed.json"
CACHE_FILE = "cache_results.json"
DEBUG_FILE = "debug_failed.txt"
XRAY_LOG_FILE = "xray_errors.log"

THREADS_DOWNLOAD = 50
XRAY_MAX_WORKERS = 30 

# ========= 2. GitHub 环境适配逻辑 =========
def ensure_xray_binary():
    xray_bin = "./xray"
    if not os.path.exists(xray_bin):
        print("📥 下载 Xray 二进制文件...")
        try:
            url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
            r = requests.get(url, timeout=30)
            with open("xray.zip", "wb") as f:
                f.write(r.content)
            with zipfile.ZipFile("xray.zip", 'r') as zip_ref:
                zip_ref.extract("xray", path=".")
            os.chmod(xray_bin, 0o755)
            os.remove("xray.zip")
            print("✅ Xray 准备就绪")
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            sys.exit(1)

# ========= 3. 完整复用原始脚本所有类和函数 =========

class XrayTester:
    def __init__(self, input_file, output_file, max_workers=30):
        self.input_file = input_file
        self.output_file = output_file
        self.max_workers = max_workers
        self.xray_path = "./xray"
        self.results = []
        self.lock = threading.Lock()

    def test_config(self, config_line):
        if not config_line.strip(): return
        # 这里嵌入你原始的测试逻辑（生成 json, subprocess 调用 xray 等）
        # ... (此处应包含你原脚本中完整的 test_config 逻辑)
        pass

    def run(self):
        if not os.path.exists(self.input_file): return
        with open(self.input_file, 'r') as f:
            configs = f.readlines()
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            executor.map(self.test_config, configs)
        with open(self.output_file, 'w') as f:
            f.write('\n'.join(self.results))

# 原始脚本中的其他函数：fetch, clean_vless, filter_vless, encode_all_configs 等
# 请务必将你原脚本中这些函数的代码完整粘贴在下方
async def fetch(session, url, stats):
    try:
        async with session.get(url, timeout=15) as response:
            if response.status == 200:
                text = await response.text()
                # 原始正则匹配逻辑...
                found = re.findall(r'vless://[^\s]+', text)
                stats['found'] += len(found)
                return found
    except: pass
    return []

async def main_cycle():
    stats = {'found': 0}
    # 1. 加载 sources.txt
    if not os.path.exists(SOURCES_FILE):
        print("❌ sources.txt 不存在")
        return

    async with aiohttp.ClientSession() as session:
        with open(SOURCES_FILE, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        tasks = [fetch(session, url, stats) for url in urls]
        await asyncio.gather(*tasks)

    print(f"📊 扫描完成，发现节点: {stats['found']}")

    if stats['found'] > 0:
        # 依次运行你原始的后续处理函数
        # await clean_vless()
        # await filter_vless()
        # await encode_all_configs()
        
        print("\n=== 启动 Xray 测速 ===")
        tester = XrayTester(ENCODED_FILE, WORK_FILE, XRAY_MAX_WORKERS)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, tester.run)
    else:
        print("⏭️ 无新节点，跳过后续步骤")

# ========= 4. 修改后的单次触发入口 =========
async def run_once():
    ensure_xray_binary()
    start_time = time.time()
    print(f"🕒 任务启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    await main_cycle()
    print(f"✅ 任务执行完毕，总耗时: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    asyncio.run(run_once())
