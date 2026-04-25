import asyncio
import aiohttp
import base64
import re
import csv
import os
import socket
import json
import time
import ssl
import hashlib
from datetime import datetime
from urllib.parse import urlparse, quote, unquote
import geoip2.database

# --- 基础配置 ---
INPUT_FILE = "sub_links.txt"
OUTPUT_TXT = "sub_parser.txt"
OUTPUT_B64 = "sub_parser_base64.txt"
OUTPUT_CSV = "sub_parser.csv"
OUTPUT_YAML = "sub_parser.yaml"
GEOIP_DB = "GeoLite2-Country.mmdb" 

MAX_CONCURRENT_TASKS = 500 
MAX_RETRIES = 1

# --- 排除过滤名单 ---
BLACKLIST_KEYWORDS = [
    "ly.ba000.cc", "wocao.su7.me", "jiasu01.vip", "louwangzhiyu", "mojie", "lyly.649844.xyz", "multiserver", "shahramv1","xship.top",
    "yywhale", "nxxbbf", "slianvpn", "cloudaddy", "quickbeevpn", "114.34.83.215:7001","sub.shadowproxy66.workers.dev",
    "tianmiao", "cokecloud", "boluoidc", "gpket", "fast8888", "ykxqn"
]

# --- 工具函数 ---
def decode_base64(data):
    if not data: return ""
    try:
        data = data.strip().replace("-", "+").replace("_", "/")
        clean_data = re.sub(r'[^A-Za-z0-9+/=]', '', data)
        missing_padding = len(clean_data) % 4
        if missing_padding: clean_data += '=' * (4 - missing_padding)
        decoded_bytes = base64.b64decode(clean_data)
        try:
            return decoded_bytes.decode('utf-8')
        except UnicodeDecodeError:
            return decoded_bytes.decode('latin-1', errors='ignore')
    except: return ""

def encode_base64(data):
    try: return base64.b64encode(data.encode('utf-8')).decode('utf-8')
    except: return ""

def get_md5_short(text):
    return hashlib.md5(text.encode()).hexdigest()[:4]

def get_geo_info(host, reader):
    if not host or not reader: return "🌐", "未知地区"
    ip = host
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        try: 
            ip = socket.gethostbyname(host)
        except: 
            return "🚩", "解析失败"
    try:
        res = reader.country(ip)
        code = res.country.iso_code
        flag = "".join(chr(ord(c) + 127397) for c in code.upper()) if code else "🌐"
        country_name = res.country.names.get('zh-CN') or res.country.name or "未知国家"
        return flag, country_name
    except:
        return "🌐", "未知地区"

def get_node_details(line, protocol):
    try:
        if protocol == 'vmess':
            v = json.loads(decode_base64(line.split("://")[1]))
            return {"server": v.get('add'), "port": int(v.get('port', 443)), "uuid": v.get('id'), "tls": v.get('tls') == "tls"}
        
        # 方案 B：增强 Trojan/通用正则解析，提取 SNI (peer)
        details = {"server": "", "port": 443, "sni": ""}
        match = re.search(r'@([^:/#?]+):(\d+)', line)
        if match:
            details["server"] = match.group(1)
            details["port"] = int(match.group(2))
        else:
            u = urlparse(line)
            details["server"] = u.hostname or ""
            details["port"] = int(u.port or 443)
        
        # 尝试提取 sni/peer 参数
        sni_match = re.search(r'[?&](?:peer|sni)=([^&#]+)', line)
        if sni_match:
            details["sni"] = sni_match.group(1)
            
        return details
    except: return None

def parse_nodes(content, reader):
    # 自动识别并循环解码 Base64 (处理多重编码或纯 Base64 列表)
    current_content = content.strip()
    if "://" not in current_content[:100]:
        decoded = decode_base64(current_content)
        if any(p + "://" in decoded for p in ['vmess', 'vless', 'trojan', 'ss', 'ssr', 'hysteria']):
            current_content = decoded

    protocols = ['vmess', 'vless', 'trojan', 'anytls', 'hysteria', 'hysteria2', 'hy2', 'tuic', 'ss', 'ssr']
    pattern = r'(?:' + '|'.join(protocols) + r')://[^\s\"\'<>#]+(?:#[^\s\"\'<>]*)?'
    found_links = re.findall(pattern, current_content, re.IGNORECASE)
    
    nodes = []
    for link in found_links:
        if link.lower().startswith(('http://', 'https://')): continue
        protocol = link.split("://")[0].lower()
        try:
            if protocol == 'vmess':
                host = json.loads(decode_base64(link.split("://")[1])).get('add')
            else:
                # 兼容提取 Host
                match = re.search(r'@([^:/#?]+)', link)
                host = match.group(1).split(':')[0] if match else urlparse(link).hostname
            
            if not host: continue
            if any(keyword in host.lower() for keyword in BLACKLIST_KEYWORDS):
                continue

            flag, country = get_geo_info(host, reader)
            nodes.append({"protocol": protocol, "flag": flag, "country": country, "line": link})
        except: continue
    return nodes

async def fetch_with_retry(session, url, reader, semaphore):
    async with semaphore:
        for _ in range(MAX_RETRIES + 1):
            try:
                async with session.get(url, timeout=15, ssl=False) as res:
                    if res.status != 200: continue
                    text = await res.text()
                    nodes = parse_nodes(text, reader)
                    if nodes:
                        print(f"[+] 成功 ({len(nodes)} 节点): {url}")
                        return url, nodes, len(nodes)
            except: pass
        return url, [], 0

async def main():
    if not os.path.exists(GEOIP_DB):
        print(f"缺失 {GEOIP_DB} 库文件"); return

    raw_file_content = ""
    if os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            raw_file_content = f.read()

    # 1. 提取所有订阅 URL
    all_urls = re.findall(r'https?://[^\s<>\"\'\u4e00-\u9fa5]+', raw_file_content)
    unique_urls = list(dict.fromkeys(all_urls))
    unique_urls = [u for u in unique_urls if not any(k in u.lower() for k in BLACKLIST_KEYWORDS)]
    
    raw_node_objs = []
    stats = []

    with geoip2.database.Reader(GEOIP_DB) as reader:
        # 2. 改进：始终尝试解析文件本身的文本内容（方案 C 增强）
        print(f"--- 正在解析本地文件 {INPUT_FILE} 内容 ---")
        local_nodes = parse_nodes(raw_file_content, reader)
        if local_nodes:
            raw_node_objs.extend(local_nodes)
            stats.append(["Local_File", len(local_nodes)])
            print(f"[*] 本地解析成功: 找到 {len(local_nodes)} 个节点")

        # 3. 处理远程订阅
        if unique_urls:
            print(f"--- 正在处理 {len(unique_urls)} 个远程订阅源 ---")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
            connector = aiohttp.TCPConnector(limit=50, ssl=False)
            async with aiohttp.ClientSession(headers={'User-Agent': 'v2rayN/6.23'}, connector=connector) as session:
                tasks = [fetch_with_retry(session, url, reader, semaphore) for url in unique_urls]
                results = await asyncio.gather(*tasks)
                for url, nodes, count in results:
                    raw_node_objs.extend(nodes); stats.append([url, count])

    if not raw_node_objs:
        print("未发现任何节点，请检查 sub_links.txt 内容。"); return

    final_links = []
    yaml_proxies = []
    seen_lines = set()
    
    for obj in raw_node_objs:
        line, protocol, flag, country = obj["line"], obj["protocol"], obj["flag"], obj["country"]
        base_link = line.split('#')[0] if protocol != 'vmess' else line
        if base_link in seen_lines: continue
        seen_lines.add(base_link)

        short_id = get_md5_short(base_link)
        new_name = f"{flag} {country} 打倒美帝国主义及其一切走狗_{short_id}"
        
        try:
            if protocol == 'vmess':
                v_json = json.loads(decode_base64(line.split("://")[1]))
                v_json['ps'] = new_name
                final_links.append(f"vmess://{encode_base64(json.dumps(v_json))}")
            elif protocol == 'ssr':
                ssr_body = decode_base64(line.split("://")[1])
                main_part = ssr_body.split('&remarks=')[0]
                new_rem = encode_base64(new_name).replace('=', '').replace('+', '-').replace('/', '_')
                final_links.append(f"ssr://{encode_base64(main_part + '&remarks=' + new_rem)}")
            else:
                final_links.append(f"{base_link}#{quote(new_name)}")

            d = get_node_details(line, protocol)
            if d:
                p_type = "trojan" if protocol == 'anytls' else protocol
                proxy_item = f"  - {{ name: \"{new_name}\", type: {p_type}, server: {d['server']}, port: {d['port']}"
                if protocol == 'vmess': 
                    proxy_item += f", uuid: {d['uuid']}, cipher: auto, tls: {str(d['tls']).lower()}"
                elif protocol == 'trojan' and d.get('sni'):
                    proxy_item += f", password: {line.split('@')[0].split('://')[1]}, sni: {d['sni']}"
                proxy_item += ", udp: true }"
                yaml_proxies.append(proxy_item)
        except: continue

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f: f.write("\n".join(final_links))
    with open(OUTPUT_B64, "w", encoding="utf-8") as f: f.write(encode_base64("\n".join(final_links)))
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f); writer.writerow(["订阅源/文件", "节点数量"]); writer.writerows(stats)

    yaml_header = f"""# 美帝国主义是纸老虎
# Updated: {now_str}
# Total: {len(final_links)}

port: 7890
mode: Rule
dns:
  enable: true
  nameserver: [119.29.29.29, 223.5.5.5]

proxies:
"""
    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        f.write(yaml_header + "\n".join(yaml_proxies))

    print(f"--- 任务完成！已生成文件，总计节点: {len(final_links)} ---")

if __name__ == "__main__":
    if os.name == 'nt': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
