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

# ========= [保持原封不动的配置] =========
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

# ========= [必要插入：GitHub 环境初始化] =========
def setup_github_env():
    """确保 Xray 二进制文件存在并可执行"""
    xray_bin = "./xray"
    if not os.path.exists(xray_bin):
        print("📥 下载 Linux 版 Xray 内核...")
        url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
        r = requests.get(url, timeout=30)
        with open("xray.zip", "wb") as f:
            f.write(r.content)
        with zipfile.ZipFile("xray.zip", 'r') as zip_ref:
            zip_ref.extract("xray", path=".")
        os.remove("xray.zip")
    
    # 强制赋予执行权限（关键！）
    os.chmod(xray_bin, 0o755)
    
    # 验证 Xray 是否可用
    try:
        result = subprocess.run([xray_bin, "version"], capture_output=True, text=True)
        print(f"✅ Xray 内核就绪: {result.stdout.splitlines()[0]}")
    except Exception as e:
        print(f"❌ Xray 内核无法运行: {e}")
        sys.exit(1)

# ========= [这里完整保留你原始脚本的 XrayTester 类] =========
# 注意：我在这里微调了路径，确保它调用的是当前目录的 ./xray
class XrayTester:
    def __init__(self, input_file, output_file, max_workers=30):
        self.input_file = input_file
        self.output_file = output_file
        self.max_workers = max_workers
        self.xray_path = "./xray" # 确保路径正确
        self.results = []
        self.lock = threading.Lock()

    # 此处请粘贴你原脚本中 test_config 的全部代码逻辑
    def test_config(self, config_line):
        if not config_line.strip(): return
        # ... 原样保留你的测试逻辑 ...
        # 提示：确保在 subprocess.Popen 中使用的是 self.xray_path
        pass

    def run(self):
        if not os.path.exists(self.input_file):
            print(f"⚠️ 找不到待测文件: {self.input_file}")
            return
        
        with open(self.input_file, 'r', encoding='utf-8') as f:
            configs = [line.strip() for line in f if line.strip()]
        
        print(f"⚙️ 正在测速 {len(configs)} 个节点，并发数: {self.max_workers}")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            executor.map(self.test_config, configs)
            
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.results))
        print(f"🎯 测速完成，可用节点已保存至: {self.output_file}")

# ========= [这里完整保留你原脚本的所有异步函数] =========
# fetch, clean_vless, filter_vless, rename_configs, encode_all_configs, main_cycle 等

# ========= [修改后的单次运行入口] =========
async def run_once():
    setup_github_env()
    start_time = time.time()
    print(f"🕒 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 执行你原始的主逻辑
    # await main_cycle() 
    
    print(f"✅ 整个工作流运行结束，总耗时: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    asyncio.run(run_once())
