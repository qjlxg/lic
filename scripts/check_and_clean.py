import requests
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import time

print("--- DEBUG: Script Execution Started ---") # 强制启动日志

# 配置文件和输入/输出文件路径
INPUT_TXT_FILE = 'output/tv_list.txt'
OUTPUT_VALID_TXT_FILE = 'output/final_valid_list.txt'
OUTPUT_VALID_M3U_FILE = 'output/final_valid_list.m3u'

# 核心配置
TIMEOUT = 5             # 每个链接的测试超时时间（秒）
MAX_WORKERS = 20        # 并行测试的线程数
MAX_LINKS_PER_CHANNEL = 50 # 每个频道最多测试多少个链接

# 排除关键字列表 (不区分大小写)
EXCLUDE_KEYWORDS = ['广播', '音乐', '.SPORTS.', '之声', '之音','Radio', '电台']

# ------------------ 辅助函数 ------------------

def is_stream_content(response):
    """
    检查 HTTP 响应头中的 Content-Type，判断是否可能是视频流。
    """
    content_type = response.headers.get('Content-Type', '').lower()
    
    # 常见的流媒体类型
    stream_types = [
        'video/',
        'application/vnd.apple.mpegurl', # m3u8
        'application/x-mpegurl',         # m3u
        'application/octet-stream',      # 可能是ts或其他流
        'application/dash+xml',          # DASH
        'audio/'                         # 广播流
    ]
    
    # 排除常见的非视频类型
    if 'text/html' in content_type or 'text/plain' in content_type or 'image/' in content_type:
        return False
        
    for stype in stream_types:
        if stype in content_type:
            return True
            
    # 如果状态码是 200/302 且没有明确的文本类型，我们倾向于认为是有效的（保守策略）
    return True

def check_link_validity(link_info):
    """
    测试单个链接的有效性，使用 HEAD 请求和超时。
    link_info 格式: (频道名, 链接)
    """
    name, link = link_info
    
    # 某些协议（如 rtp://, p3p://）requests无法直接测试，跳过
    if not link.lower().startswith(('http', 'https')):
        print(f"SKIP (Protocol): {name} - Non-HTTP link: {link}")
        return None
        
    try:
        # 使用 HEAD 请求，只获取头部信息，速度更快
        response = requests.head(
            link, 
            timeout=TIMEOUT, 
            allow_redirects=True, # 允许重定向
            headers={'User-Agent': 'Mozilla/5.0'} # 模拟浏览器
        )
        
        # 检查状态码
        if response.status_code in (200, 301, 302):
            # 检查内容类型
            if is_stream_content(response):
                print(f"SUCCESS (Status {response.status_code}): {name}")
                return link_info # 返回 (频道名, 链接)
            else:
                # 打印内容类型失败的详细信息
                # print(f"FAIL (Content Type {response.headers.get('Content-Type')}): {name}") # 保持原有日志输出
                pass
        else:
            # 打印状态码失败的详细信息
            # print(f"FAIL (Status {response.status_code}): {name}") # 保持原有日志输出
            pass
            
    except requests.exceptions.RequestException as e:
        # 打印请求异常的详细信息 (超时、连接错误等)
        # print(f"FAIL (Error {type(e).__name__}): {name} - Link prefix: {link[:50]}...") # 保持原有日志输出
        pass
    except Exception as e:
        # 打印其他未知错误
        # print(f"FAIL (Unknown Error): {name} - {e}") # 保持原有日志输出
        pass
        
    return None

# ------------------ 主逻辑函数 ------------------

def main():
    if not os.path.exists(INPUT_TXT_FILE):
        print(f"Error: Input file {INPUT_TXT_FILE} not found. Run update_list.py first.")
        return

    # 1. 读取输入文件内容
    raw_channels = []
    unique_links_per_channel = {}
    excluded_channels_count = 0 
    
    # 使用 try/except 捕获文件读取错误
    try:
        with open(INPUT_TXT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.rsplit(',', 1)
                if len(parts) == 2:
                    name = parts[0].strip()
                    link = parts[1].strip()
                    
                    # 关键字排除逻辑
                    is_excluded = False
                    for keyword in EXCLUDE_KEYWORDS:
                        # 使用 name.lower() 确保不区分大小写匹配
                        if keyword.lower() in name.lower():
                            is_excluded = True
                            excluded_channels_count += 1
                            print(f"EXCLUDE (Keyword '{keyword}'): {name}")
                            break
                            
                    if is_excluded:
                        continue # 跳过当前行，不进行测试和输出
                    
                    # 记录每个频道的链接，并限制数量
                    if name not in unique_links_per_channel:
                        unique_links_per_channel[name] = set()
                    
                    if len(unique_links_per_channel[name]) < MAX_LINKS_PER_CHANNEL:
                        unique_links_per_channel[name].add(link)
                        raw_channels.append((name, link))
    except Exception as e:
        print(f"FATAL ERROR: Could not read or process input file {INPUT_TXT_FILE}. Error: {e}")
        return


    if not raw_channels:
        if excluded_channels_count > 0:
            print(f"INFO: All {excluded_channels_count} found channels were excluded by keywords. No links to test. Exiting.")
        else:
            print("INFO: Input file read successfully, but no valid channel lines were found (is the file empty or unreadable?). Exiting.")
        return

    print(f"Loaded {len(raw_channels)} links for testing across {len(unique_links_per_channel)} channels.")
    if excluded_channels_count > 0:
        print(f"Note: {excluded_channels_count} channel links were excluded based on keywords: {', '.join(EXCLUDE_KEYWORDS)}") 
        
    print(f"Starting validity check with {MAX_WORKERS} concurrent workers and {TIMEOUT}s timeout...")
    start_time = time.time()

    # 2. 并行测试所有链接
    valid_links = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 提交所有链接到线程池进行测试
        future_to_link = {executor.submit(check_link_validity, ch_info): ch_info for ch_info in raw_channels}
        
        for future in as_completed(future_to_link):
            result = future.result()
            if result:
                valid_links.append(result)

    end_time = time.time()
    valid_count = len(valid_links)
    print(f"Test finished in {end_time - start_time:.2f} seconds.")
    print(f"Found {valid_count} valid links.")
    
    # 3. 组织最终有效的频道列表 
    final_valid_channels = {} # { 频道名: {链接1, 链接2, ...} }
    
    for name, link in valid_links:
        if name not in final_valid_channels:
            final_valid_channels[name] = set()
        final_valid_channels[name].add(link)
        
    # 4. 写入输出文件 (TXT 格式) - **** 已修改为输出所有有效链接 ****
    os.makedirs(os.path.dirname(OUTPUT_VALID_TXT_FILE), exist_ok=True)
    txt_output_content = []
    
    # 排序以保持输出稳定
    sorted_channel_names = sorted(final_valid_channels.keys())
    
    for name in sorted_channel_names:
        # 获取该频道的所有有效链接，并排序
        links = sorted(list(final_valid_channels[name]))
        
        # 遍历所有有效链接，将它们逐一写入 TXT 文件
        for link in links:
            txt_output_content.append(f"{name},{link}")
        
    print(f"Writing {len(txt_output_content)} total valid links to {OUTPUT_VALID_TXT_FILE}")
    with open(OUTPUT_VALID_TXT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(txt_output_content) + '\n')
        
    # 5. 写入输出文件 (M3U 格式) - 保持不变
    m3u_output_content = ["#EXTM3U"]
    for name in sorted_channel_names:
        links = sorted(list(final_valid_channels[name]))
        
        for link in links:
            # 默认使用 "Valid Channels" 作为 Group Title
            extinf_line = f'#EXTINF:-1 tvg-name="{name}" group-title="Valid Channels",{name}'
            m3u_output_content.append(extinf_line)
            m3u_output_content.append(link)
            
    print(f"Writing M3U file to {OUTPUT_VALID_M3U_FILE}")
    with open(OUTPUT_VALID_M3U_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(m3u_output_content) + '\n')

    print("Channel check and cleanup complete.")


if __name__ == "__main__":
    main()
