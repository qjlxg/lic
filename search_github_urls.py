#搜索 (Async Version for URL Validation - Fully Configured)
import os
import re
import requests
import time
import logging
import logging.handlers
import yaml
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor # 用于限制并发校验线程

# ... (日志和配置加载函数保持不变) ...

# 配置日志系统，支持文件和控制台输出
def setup_logging(config):
    """配置日志系统，支持文件和控制台输出，日志文件自动轮转以避免过大"""
    # 尝试从配置中获取日志级别，如果找不到则默认 INFO
    log_level = getattr(logging, config.get('logging', {}).get('log_level', 'INFO').upper(), logging.INFO)
    log_file = config.get('logging', {}).get('log_file', 'logs/iptv.log')
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(log_level)

    # 移除可能存在的旧 handler，避免重复打印
    logger.handlers = [] 

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=1
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 加载配置文件
def load_config(config_path="config/config.yaml"):
    """加载并解析 YAML 配置文件"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            config = yaml.safe_load(file)
            logging.info("配置文件 config.yaml 加载成功")
            return config
    except FileNotFoundError:
        logging.error(f"错误：未找到配置文件 '{config_path}'")
        exit(1)
    except yaml.YAMLError as e:
        logging.error(f"错误：配置文件 '{config_path}' 格式错误: {e}")
        exit(1)
    except Exception as e:
        logging.error(f"错误：加载配置文件 '{config_path}' 失败: {e}")
        exit(1)

# 读取本地 TXT 文件
def read_txt_to_array_local(file_name):
    """从本地 TXT 文件读取内容到数组"""
    try:
        with open(file_name, 'r', encoding='utf-8') as file:
            lines = [line.strip() for line in file if line.strip()]
        return lines
    except FileNotFoundError:
        logging.warning(f"文件 '{file_name}' 未找到")
        return []
    except Exception as e:
        logging.error(f"读取文件 '{file_name}' 失败: {e}")
        return []

# 写入本地 TXT 文件
def write_array_to_txt_local(file_path, data_array):
    """将数组内容写入本地 TXT 文件"""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(data_array))
        logging.info(f"成功写入 {len(data_array)} 行到 '{file_path}'")
    except Exception as e:
        logging.error(f"写入文件 '{file_path}' 失败: {e}")

# 加载配置和设置日志
CONFIG_PATH = "config/config.yaml"
CONFIG = load_config(CONFIG_PATH)
setup_logging(CONFIG)

# 检查环境变量 GITHUB_TOKEN
GITHUB_TOKEN = os.getenv('BOT')
if not GITHUB_TOKEN:
    logging.error("错误：未设置环境变量 'BOT'")
    exit(1)

# URL 文件路径
URLS_PATH = 'config/urls.txt'

# GitHub API 基础 URL
GITHUB_API_BASE_URL = "https://api.github.com"
SEARCH_CODE_ENDPOINT = "/search/code"

# *** 配置 Requests 会话 (用于同步的 GitHub API 调用) ***
# 使用配置中的重试次数
requests_retry_total = CONFIG['network'].get('requests_retry_total', 3) 

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})
retry_strategy = Retry(
    total=requests_retry_total, # 使用配置值
    backoff_factor=CONFIG['network']['requests_retry_backoff_factor'],
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)

# *** 异步校验函数 ***
async def check_url_validity_async(url, aiohttp_session):
    """
    异步检查 URL 是否有效，使用配置中的超时时间。
    """
    # 使用配置中的 check_timeout
    timeout_seconds = CONFIG['network'].get('check_timeout', 20)
    timeout = ClientTimeout(total=timeout_seconds) 
    
    try:
        # 使用 aiohttp 发起 GET 请求
        async with aiohttp_session.get(url, timeout=timeout) as response:
            if response.status >= 200 and response.status < 400:
                # 确保读取一小部分内容以触发完整的连接和请求流程
                await response.content.read(1) 
                return url
            else:
                return None
    except aiohttp.ClientError:
        return None
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None

# *** URL 预筛选函数 ***
def pre_screen_url(raw_url, existing_urls, newly_discovered_urls, config):
    """
    对 URL 进行初步筛选，检查后缀、排除模式和是否重复。
    返回 True 表示 URL 有效且是新发现的，可以进行异步校验。
    """
    # 1. 核心筛选：只接受以 .m3u8 结尾的 URL
    if not re.search(r'\.m3u8$', raw_url, re.IGNORECASE):
        return False

    # 2. 预筛选：检查是否匹配无效模式 (来自 url_pre_screening)
    invalid_patterns = config.get('url_pre_screening', {}).get('invalid_url_patterns', [])
    for pattern in invalid_patterns:
        try:
            # 使用配置中的正则模式进行匹配
            if re.search(pattern, raw_url, re.IGNORECASE):
                logging.debug(f"URL {raw_url} 匹配无效模式: {pattern}，跳过")
                return False
        except Exception as e:
            logging.error(f"配置中存在无效正则模式 '{pattern}': {e}")
            continue
            
    # 3. 检查是否已存在于现有列表或新发现集合中
    if raw_url in existing_urls or raw_url in newly_discovered_urls:
        return False
        
    return True


async def auto_discover_github_urls_async(urls_file_path_local, github_token):
    """从 GitHub 自动发现新的 IPTV 源 URL，并使用异步方式校验"""
    if not github_token:
        logging.warning("未提供 GitHub token，跳过 URL 自动发现")
        return

    existing_urls = set(read_txt_to_array_local(urls_file_path_local))
    newly_discovered_urls = set() # 存储所有发现的、待校验的 .m3u8 URLs
    
    # 获取备用 URL (同步)
    for backup_url in CONFIG.get('backup_urls', []):
        try:
            response = session.get(backup_url, timeout=10)
            response.raise_for_status()
            existing_urls.update([line.strip() for line in response.text.split('\n') if line.strip()])
            logging.info(f"成功从备用 URL {backup_url} 获取现有 URL")
        except Exception as e:
            logging.warning(f"从备用 URL {backup_url} 获取失败: {e}")

    headers = {
        "Accept": "application/vnd.github.v3.text-match+json",
        "Authorization": f"token {github_token}"
    }

    logging.warning("开始从 GitHub 自动发现新的 IPTV 源 URL")
    
    keywords_list = CONFIG.get('search_keywords', [])
    # 使用配置中的 inter-keyword wait time
    wait_time = CONFIG['github'].get('retry_wait', 48) 

    for i, keyword in enumerate(tqdm(keywords_list, desc="关键词搜索进度")):
        
        if i > 0:
            logging.warning(f"切换到下一个关键词: '{keyword}'，等待 {wait_time} 秒以避免速率限制")
            time.sleep(wait_time)

        page = 1
        while page <= CONFIG['github']['max_search_pages']:
            params = {
                "q": keyword,
                "sort": "indexed",
                "order": "desc",
                "per_page": CONFIG['github']['per_page'],
                "page": page
            }
            try:
                # ... (GitHub API 调用和速率限制处理逻辑保持不变) ...
                response = session.get(
                    f"{GITHUB_API_BASE_URL}{SEARCH_CODE_ENDPOINT}",
                    headers=headers,
                    params=params,
                    timeout=CONFIG['github']['api_timeout']
                )
                response.raise_for_status()
                data = response.json()

                rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
                rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', 0))
                rate_limit_threshold = CONFIG['github'].get('rate_limit_threshold', 3) # 使用配置中的阈值

                if rate_limit_remaining <= rate_limit_threshold: # 使用配置的阈值进行检查
                    wait_seconds = max(0, rate_limit_reset - time.time()) + 5
                    logging.warning(f"GitHub API 速率限制预警，剩余请求: {rate_limit_remaining}，等待 {wait_seconds:.0f} 秒")
                    time.sleep(wait_seconds)
                    continue

                if not data.get('items'):
                    logging.info(f"关键词 '{keyword}' 在第 {page} 页无结果")
                    break

                page_discovered_urls = set()
                for item in data['items']:
                    html_url = item.get('html_url', '')
                    match = re.search(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)', html_url)
                    if not match:
                        continue
                    
                    user, repo, branch, file_path = match.groups()
                    raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{file_path}"
                    
                    # *** 调用预筛选函数，应用 .m3u8 限制和 invalid_url_patterns 规则 ***
                    if pre_screen_url(raw_url, existing_urls, newly_discovered_urls, CONFIG):
                        page_discovered_urls.add(raw_url)

                newly_discovered_urls.update(page_discovered_urls)
                logging.info(f"完成关键词 '{keyword}' 第 {page} 页，发现 {len(page_discovered_urls)} 个新的 .m3u8 URL")
                page += 1

            except requests.exceptions.HTTPError as e:
                # ... (403 错误处理逻辑保持不变) ...
                if e.response.status_code == 403:
                    try:
                        rate_limit_reset = int(e.response.headers.get('X-RateLimit-Reset', 0))
                        wait_seconds = max(0, rate_limit_reset - time.time()) + 5
                        logging.error(f"搜索 GitHub 关键词 '{keyword}' 失败: 403. 等待 {wait_seconds:.0f} 秒后重试。")
                        time.sleep(wait_seconds)
                        continue
                    except (ValueError, TypeError):
                        logging.error(f"搜索 GitHub 关键词 '{keyword}' 失败: 403. 无法获取重置时间，等待 60 秒后重试。")
                        time.sleep(60)
                        continue
                else:
                    logging.error(f"搜索 GitHub 关键词 '{keyword}' 失败: {e}")
                    break
            except requests.exceptions.RequestException as e:
                logging.error(f"搜索 GitHub 关键词 '{keyword}' 失败: {e}")
                break
            except Exception as e:
                logging.error(f"搜索 GitHub 关键词 '{keyword}' 时发生意外错误: {e}")
                break

    # --- 异步校验阶段 ---
    if newly_discovered_urls:
        logging.warning(f"开始对 {len(newly_discovered_urls)} 个新发现的 URL 进行异步有效性校验...")
        
        # *** 使用配置中的并发数限制 ***
        max_workers = CONFIG['network'].get('channel_check_workers', 50)
        
        # 使用 aiohttp 客户端会话
        async with aiohttp.ClientSession(headers={"User-Agent": "Async M3U8 Validator"}) as aiohttp_session:
            # 创建所有校验任务
            tasks = [check_url_validity_async(url, aiohttp_session) for url in newly_discovered_urls]
            
            validated_urls = []
            
            # 使用 asyncio.as_completed 和 Semaphore 来限制并发连接数
            semaphore = asyncio.Semaphore(max_workers)
            
            async def limited_check(url):
                async with semaphore:
                    return await check_url_validity_async(url, aiohttp_session)

            limited_tasks = [limited_check(url) for url in newly_discovered_urls]

            # 使用 tqdm 显示进度
            for future in tqdm(asyncio.as_completed(limited_tasks), total=len(limited_tasks), desc="URL 校验进度"):
                result = await future
                if result:
                    validated_urls.append(result)

        validated_urls_set = set(validated_urls)
        
        logging.warning(f"异步校验完成，{len(validated_urls_set)} 个 URL 验证通过。")
        
        if validated_urls_set:
            updated_urls = sorted(list(existing_urls | validated_urls_set))
            logging.warning(f"总计保存 {len(updated_urls)} 个 URL (新增 {len(validated_urls_set)} 个)")
            write_array_to_txt_local(urls_file_path_local, updated_urls)
        else:
            logging.warning("未发现任何通过校验的新 URL")

    else:
        logging.warning("未发现任何新的 IPTV 源 URL")


if __name__ == "__main__":
    # 使用 asyncio.run 启动主异步函数
    asyncio.run(auto_discover_github_urls_async(URLS_PATH, GITHUB_TOKEN))
