import requests
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置文件和输出文件路径
URLS_FILE = 'config/urls.txt' 
OUTPUT_FILE = 'output/tv_list.m3u'
OUTPUT_TXT_FILE = 'output/tv_list.txt'
MAX_WORKERS = 10 

# --- M3U 文件解析函数 ---
def parse_m3u_content(content):
    """
    解析 M3U 或类似 IPTV 列表内容，提取频道信息。
    此函数现在支持两种常见的格式：M3U 和 频道名称,链接 格式。
    返回: { (频道名, 频道类别): 链接列表 }
    """
    lines = content.splitlines()
    channels = {}
    channel_info = None
    
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            channel_info = None
            
        if line.startswith('#EXTINF:'):
            # 1. M3U 格式的频道信息行
            
            # 提取 group-title
            group_match = re.search(r'group-title="([^"]*)"', line)
            group_title = group_match.group(1).strip() if group_match else "Other"
            
            # 提取频道名称 (逗号后的内容)
            name_match = re.search(r',\s*([^,]+)$', line)
            channel_name = name_match.group(1).strip() if name_match else None
            
            if channel_name:
                channel_info = (channel_name, group_title)
        
        elif line.startswith('http') or line.startswith('rtp'):
            # 2. M3U 格式的链接行
            link = line
            key = channel_info
            
            # 如果是链接行，并且前面已经解析到频道信息，则将其添加到频道列表
            if key and key[0] and key[1]:
                if key not in channels:
                    channels[key] = set()
                channels[key].add(link)
                channel_info = None # M3U格式通常是一行EXTINF接一行链接
            
        elif ',' in line and ('http' in line or 'rtp' in line):
            # 3. TXT 格式 (频道名称,链接) 的兜底解析
            
            # 分割为 频道名称 和 链接
            # 注意：逗号可能出现在频道名称中，但链接中不包含，所以我们从右边开始分割
            parts = line.rsplit(',', 1)
            if len(parts) == 2:
                channel_name = parts[0].strip()
                link = parts[1].strip()
                
                # 针对您提供的台湾源，频道名称有时可能带有 (数字,，例如：公視(１３,
                # 我们尝试清理一下频道名称
                channel_name = re.sub(r'\(.*?,', '', channel_name).strip() 
                
                if channel_name and link:
                    key = (channel_name, "TXT_Import") # 默认类别
                    if key not in channels:
                        channels[key] = set()
                    channels[key].add(link)

    return channels

# --- URL 下载与处理函数 ---
def download_url(url):
    try:
        print(f"Downloading: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status() 
        return parse_m3u_content(response.text)
        
    except requests.exceptions.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return {}
    except Exception as e:
        print(f"An unexpected error occurred while processing {url}: {e}")
        return {}
        

# --- 主执行逻辑 ---
def main():
    
    # 1. 读取 URL 列表
    if not os.path.exists(URLS_FILE):
        print(f"Error: {URLS_FILE} not found. Please create it and add URLs.")
        return

    with open(URLS_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls:
        print("No URLs found in urls.txt. Exiting.")
        return

    # 2. 并行下载和解析所有 URL 
    all_channels = {} 
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(download_url, url): url for url in urls}
        
        for future in as_completed(future_to_url):
            try:
                result = future.result()
                for key, links in result.items():
                    # 核心逻辑: 不进行过滤，全部添加
                    if key not in all_channels:
                        all_channels[key] = set()
                    all_channels[key].update(links) 
            except Exception as e:
                url = future_to_url[future]
                print(f"{url} generated an exception: {e}")

    # 3. 生成最终的 M3U 和 TXT 文件内容
    output_content = ["#EXTM3U"]
    txt_content = [] 
    
    # 按照频道名和类别排序，使结果更稳定
    sorted_keys = sorted(all_channels.keys())
    
    for key in sorted_keys:
        name, group = key 
        links = all_channels[key] 
        
        sorted_links = sorted(list(links))
        first_link = sorted_links[0] if sorted_links else None
        
        if first_link:
            # TXT 格式: 频道名称,链接 (只取第一个可用链接)
            txt_content.append(f"{name},{first_link}") 
        
        # M3U 格式: 包含所有链接作为备选
        for link in sorted_links: 
            extinf_line = f'#EXTINF:-1 tvg-name="{name}" group-title="{group}",{name}'
            output_content.append(extinf_line)
            output_content.append(link)
            
    # 4. 写入输出文件 
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    print(f"Writing {len(all_channels)} unique channels to {OUTPUT_FILE}")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_content) + '\n')
    
    print(f"Writing {len(txt_content)} channels in TXT format to {OUTPUT_TXT_FILE}")
    with open(OUTPUT_TXT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(txt_content) + '\n')
        
    print("Update complete.")

if __name__ == "__main__":
    main()
