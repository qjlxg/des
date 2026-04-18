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

# ========= 1. 原始配置 (原封不动) =========
SOURCES_FILE = "sources.txt"
OUTPUT_FILE = "url.txt"
CLEAN_FILE = "url_clean.txt"
FILTERED_FILE = "url_filtered.txt"
NAMED_FILE = "url_named.txt"
ENCODED_FILE = "url_encoded.txt"
WORK_FILE = "url_work.txt"
XRAY_MAX_WORKERS = 30 

# ========= 2. 环境初始化 =========
def setup_github_env():
    xray_bin = "./xray"
    if not os.path.exists(xray_bin):
        print("📥 下载 Linux 版 Xray 内核...")
        url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
        r = requests.get(url, timeout=30)
        with open("xray.zip", "wb") as f: f.write(r.content)
        with zipfile.ZipFile("xray.zip", 'r') as zip_ref: zip_ref.extract("xray", path=".")
        os.remove("xray.zip")
    os.chmod(xray_bin, 0o755)

# ========= 3. 核心逻辑 (修复异步衔接) =========

# 这里假定你原始脚本中已经定义了这些函数，如果没有，请确保它们被完整包含
# 重点：必须确保这些函数在 main_cycle 中被 await
async def main_cycle():
    stats = {'found': 0}
    print(f"🔍 检查 {SOURCES_FILE}...")
    
    if not os.path.exists(SOURCES_FILE):
        print("❌ 错误: 找不到 sources.txt")
        return

    # --- 步骤 1: 抓取 ---
    # 确保调用了你原有的 fetch 逻辑并更新了 stats['found']
    # 示例 (请替换为你原有的 fetch 调用):
    # await fetch_all_sources(stats)
    
    print(f"📊 扫描完成，发现节点总数: {stats['found']}")

    if stats['found'] > 0:
        print("🛠️ 开始执行清洗、过滤和编码...")
        # ！！！关键：必须按照顺序 await 每一个步骤 ！！！
        # await clean_vless()
        # await filter_vless()
        # await rename_configs()
        # await encode_all_configs() # 此步骤生成 ENCODED_FILE (url_encoded.txt)

        if os.path.exists(ENCODED_FILE):
            print(f"🚀 启动 Xray 测速: {ENCODED_FILE}")
            # XrayTester 是同步阻塞的，需在线程池运行
            from parser_github import XrayTester # 确保类定义正确
            tester = XrayTester(ENCODED_FILE, WORK_FILE, XRAY_MAX_WORKERS)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, tester.run)
        else:
            print(f"❌ 失败: 编码后的文件 {ENCODED_FILE} 未生成，请检查过滤逻辑")
    else:
        print("⏭️ 发现节点数为 0，任务提前结束")

# ========= 4. 入口函数 =========
async def run_once():
    setup_github_env()
    start_time = time.time()
    # 执行主逻辑
    try:
        # 如果你的原始代码中 class 和 def 都在这个文件里
        # 请确保 main_cycle 能够访问到它们
        await main_cycle() 
    except Exception as e:
        print(f"❌ 运行崩溃: {e}")
    
    print(f"✅ 运行结束，总耗时: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    asyncio.run(run_once())
