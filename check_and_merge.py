import json
import os
import glob
import requests
import concurrent.futures
from urllib.parse import urlparse

# --- 配置 ---
BOX_DIR = "box"
OUTPUT_FILE = "merged_tvbox_config.json"
TIMEOUT = 10  # URL 检查超时时间（秒）
MAX_WORKERS = 32 # 并行检查的线程数

# 定义需要排除的静态文件后缀（这些文件通常只包含脚本/配置，无法代表VOD服务连通性）
# 明确包含 .js, .json, .jsd 等
EXCLUDED_EXTENSIONS = ('.js', '.json', '.txt', '.xml', '.yml', '.yaml', '.jsd')

# 使用 Session 提高连接效率
SESSION = requests.Session()
# 模拟 TVBox 的 User-Agent
HEADERS = {'User-Agent': 'okhttp/4.1.0'}

def is_valid_url(url: str) -> bool:
    """检查字符串是否是有效的 HTTP/HTTPS URL，并且是否可访问。"""
    if not url or not url.startswith(('http://', 'https://')):
        return False
    
    try:
        if not urlparse(url).netloc:
            return False
    except ValueError:
        return False

    try:
        # 使用 HEAD 请求更快，只获取头部信息
        response = SESSION.head(url, timeout=TIMEOUT, allow_redirects=True, headers=HEADERS)
        return 200 <= response.status_code < 400
    except requests.exceptions.RequestException:
        return False

def check_site(site: dict) -> dict or None:
    """
    检查站点的主要 URL，过滤内部站点和静态文件链接，并测试外部 URL 连通性。
    """
    urls_to_check = []
    
    api = site.get('api', '').strip()
    ext = site.get('ext', '').strip()
    
    # --- 辅助函数：判断是否是有效的外部 API URL ---
    def is_valid_api_url(url: str) -> bool:
        # 1. 必须是 http/https 开头
        if not (url.startswith('http://') or url.startswith('https://')):
            return False

        # 2. **增强过滤**：解析 URL 并获取路径，忽略查询参数和片段，检查文件后缀
        parsed_url = urlparse(url)
        # 获取路径部分并转为小写
        clean_path = parsed_url.path.lower()
        
        # 排除以静态文件后缀结尾的路径
        if clean_path.endswith(EXCLUDED_EXTENSIONS):
            return False
        
        # 3. 排除内部 API 标识（如果 api 字段包含了 URL，通常不会以 csp_ 开头，但以防万一）
        if url.lower().startswith('csp_'):
            return False
        
        return True

    # 1. 识别并收集外部 API URL
    if is_valid_api_url(api):
        urls_to_check.append(api)
    
    # 2. 识别并收集外部 Ext URL
    if is_valid_api_url(ext):
        # 排除带参数的复杂 ext (如 http://...$$$...)
        if '$$$' not in ext and '|' not in ext:
            # 避免重复检查 api 和 ext 相同的情况
            if not urls_to_check or urls_to_check[0] != ext:
                urls_to_check.append(ext)
            
    # 【核心过滤逻辑】：如果找不到任何可测试的外部 API URL，则丢弃
    if not urls_to_check:
        site_name = site.get('name', site.get('key', '未知'))
        # 打印信息辅助调试
        print(f"➖ 站点 '{site_name}' 缺乏可测试的外部 API URL，已移除。") 
        return None 
    
    # 3. 连通性测试：只要有一个 URL 可用就保留站点
    site_name = site.get('name', site.get('key', '未知'))
    for url in urls_to_check:
        if is_valid_url(url):
            return site
            
    # 4. 连通性测试失败，丢弃
    print(f"❌ 站点 '{site_name}' 连通性测试失败，已移除。") 
    return None

# --- 其他辅助函数 (process_file, merge_configs, main) 保持不变 ---
def process_file(file_path: str) -> dict or None:
    """读取并解析单个 JSON 文件。"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip().lstrip('\ufeff')
            return json.loads(content)
    except Exception as e:
        print(f"❌ 解析文件 {os.path.basename(file_path)} 失败: {e}")
        return None

def merge_configs(configs: list[dict]) -> dict:
    """合并配置并并行检查站点 URL，只保留 sites 和 spider。"""
    merged_config = {
        "sites": [],
        "spider": ""
    }
    
    all_sites = []
    
    # 1. 收集所有数据 (只收集 sites 和 spider)
    for config in configs:
        if not config:
            continue
            
        all_sites.extend(config.get("sites", []))
        
        # 更新 spider（只取最后一个有效值）
        if config.get("spider"):
            merged_config["spider"] = config["spider"]

    # 2. 站点去重 (基于 key)
    unique_sites = {}
    for site in all_sites:
        key_name = site.get('key', '').strip()
        if not key_name:
            continue
        
        if key_name not in unique_sites:
             unique_sites[key_name] = site
    
    # 3. 并行 URL 检查
    print(f"--- 开始并行检查 {len(unique_sites)} 个唯一站点 URL ---")
    
    checked_sites = []
    sites_to_check = list(unique_sites.values())
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_site = {executor.submit(check_site, site): site for site in sites_to_check}
        
        for i, future in enumerate(concurrent.futures.as_completed(future_to_site)):
            site = future_to_site[future]
            site_name = site.get('name', site.get('key', '未知'))
            try:
                result = future.result()
                if result:
                    checked_sites.append(result)
                    # 连通性通过的站点，check_site 会打印，这里就不再重复打印成功信息了
                    # print(f"✅ [{i+1}/{len(sites_to_check)}] 站点 '{site_name}' 通过检查。")
            except Exception as exc:
                print(f"⚠️ [{i+1}/{len(sites_to_check)}] 站点 '{site_name}' 发生异常: {exc}")

    print(f"--- 检查完成。保留 {len(checked_sites)} 个有效站点。---")
    merged_config["sites"] = checked_sites
    
    # 4. 移除空的 spider 字段，保持配置精简
    if not merged_config.get("spider"):
        del merged_config["spider"]
        
    return merged_config

def main():
    if not os.path.exists(BOX_DIR):
        print(f"错误：未找到目录 '{BOX_DIR}'。请创建此目录并将 JSON 配置文件放入其中。")
        return

    file_paths = glob.glob(os.path.join(BOX_DIR, "*.json"))
    if not file_paths:
        print(f"在 '{BOX_DIR}' 目录下未找到 JSON 文件。退出。")
        return
        
    print(f"找到 {len(file_paths)} 个 JSON 配置文件进行处理...")

    configs = [process_file(f) for f in file_paths]
    configs = [c for c in configs if c is not None]
    
    if not configs:
        print("未加载到有效的配置。退出。")
        return

    final_config = merge_configs(configs)

    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_config, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 成功生成合并后的配置文件: {OUTPUT_FILE}")
    except Exception as e:
        print(f"写入输出文件时发生错误: {e}")

if __name__ == "__main__":
    main()
