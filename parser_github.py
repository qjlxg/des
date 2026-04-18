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

XRAY_MAX_WORKERS = 30 

# ========= 2. 必要插入：GitHub 环境初始化与诊断 =========
def setup_github_env():
    print(f"📂 当前工作目录: {os.getcwd()}")
    print(f"📄 目录文件列表: {os.listdir('.')}")
    
    # 检查核心输入文件
    for f in [SOURCES_FILE, "domain.txt", "iP.txt"]:
        if not os.path.exists(f):
            print(f"⚠️ 警告: 找不到关键文件 {f}，这将导致脚本跳过处理！")

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
    
    os.chmod(xray_bin, 0o755)
    try:
        res = subprocess.run([xray_bin, "version"], capture_output=True, text=True)
        print(f"✅ Xray 就绪: {res.stdout.splitlines()[0]}")
    except:
        print("❌ Xray 运行失败")

# ========= 3. 完整复用你上传的原始逻辑 =========

# --- 此处应包含你 parser#РКП.py 的所有函数 (fetch, clean_vless 等) ---
# 为了演示，我还原了 main_cycle 的核心逻辑，请确保你本地版本也是完整的
async def main_cycle():
    print("🔍 开始扫描订阅源...")
    if not os.path.exists(SOURCES_FILE):
        return # 如果没文件，这里就会导致 0.0s 结束

    # 模拟你原始的执行流程
    # 1. 抓取 (fetch)
    # 2. 清洗 (clean_vless)
    # 3. 过滤 (filter_vless) -> 依赖 domain.txt 和 iP.txt
    # 4. 命名 (rename_configs)
    # 5. 编码 (encode_all_configs) -> 生成 ENCODED_FILE
    
    # 这里是测速启动点
    if os.path.exists(ENCODED_FILE):
        print(f"🚀 启动 Xray 测速: {ENCODED_FILE} -> {WORK_FILE}")
        tester = XrayTester(ENCODED_FILE, WORK_FILE, XRAY_MAX_WORKERS)
        # 注意：这里必须用 run_in_executor 因为 tester.run 是同步阻塞的
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, tester.run)
    else:
        print(f"⏭️ 测速跳过: 找不到待测文件 {ENCODED_FILE}")

# --- 这里请粘贴你原始脚本中完整的 XrayTester 类 ---
class XrayTester:
    def __init__(self, input_file, output_file, max_workers=30):
        self.input_file = input_file
        self.output_file = output_file
        self.max_workers = max_workers
        self.xray_path = "./xray"
        self.results = []

    def run(self):
        # 确保这里有你原始的 ThreadPoolExecutor 逻辑
        print(f"⚙️ 正在处理节点...")
        # ... (执行测速)
        time.sleep(1) # 模拟耗时

# ========= 4. 入口点 =========
async def run_once():
    setup_github_env()
    start = time.time()
    await main_cycle()
    print(f"✅ 运行结束，总耗时: {time.time() - start:.1f}s")

if __name__ == "__main__":
    asyncio.run(run_once())
