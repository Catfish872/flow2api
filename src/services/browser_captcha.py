"""
基于 RT 的本地 reCAPTCHA 打码服务 (终极闭环版 - 无 fake_useragent 纯净版)
支持：自动刷新 Session Token、外部触发指纹切换、死磕重试
"""
import os
import sys
import subprocess
# 修复 Windows 上 playwright 的 asyncio 兼容性问题
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

import asyncio
import time
import re
import random
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from urllib.parse import urlparse, unquote
import aiohttp

from ..core.logger import debug_logger


# ==================== Docker 环境检测 ====================
def _is_running_in_docker() -> bool:
    """检测是否在 Docker 容器中运行"""
    # 方法1: 检查 /.dockerenv 文件
    if os.path.exists('/.dockerenv'):
        return True
    # 方法2: 检查 cgroup
    try:
        with open('/proc/1/cgroup', 'r') as f:
            content = f.read()
            if 'docker' in content or 'kubepods' in content or 'containerd' in content:
                return True
    except:
        pass
    # 方法3: 检查环境变量
    if os.environ.get('DOCKER_CONTAINER') or os.environ.get('KUBERNETES_SERVICE_HOST'):
        return True
    return False


IS_DOCKER = _is_running_in_docker()


# ==================== playwright 自动安装 ====================
def _run_pip_install(package: str, use_mirror: bool = False) -> bool:
    """运行 pip install 命令"""
    cmd = [sys.executable, '-m', 'pip', 'install', package]
    if use_mirror:
        cmd.extend(['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'])
    
    try:
        debug_logger.log_info(f"[BrowserCaptcha] 正在安装 {package}...")
        print(f"[BrowserCaptcha] 正在安装 {package}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            debug_logger.log_info(f"[BrowserCaptcha] ✅ {package} 安装成功")
            print(f"[BrowserCaptcha] ✅ {package} 安装成功")
            return True
        else:
            debug_logger.log_warning(f"[BrowserCaptcha] {package} 安装失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        debug_logger.log_warning(f"[BrowserCaptcha] {package} 安装异常: {e}")
        return False


def _run_playwright_install(use_mirror: bool = False) -> bool:
    """安装 playwright chromium 浏览器"""
    cmd = [sys.executable, '-m', 'playwright', 'install', 'chromium']
    env = os.environ.copy()
    
    if use_mirror:
        # 使用国内镜像
        env['PLAYWRIGHT_DOWNLOAD_HOST'] = 'https://npmmirror.com/mirrors/playwright'
    
    try:
        debug_logger.log_info("[BrowserCaptcha] 正在安装 chromium 浏览器...")
        print("[BrowserCaptcha] 正在安装 chromium 浏览器...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        if result.returncode == 0:
            debug_logger.log_info("[BrowserCaptcha] ✅ chromium 浏览器安装成功")
            print("[BrowserCaptcha] ✅ chromium 浏览器安装成功")
            return True
        else:
            debug_logger.log_warning(f"[BrowserCaptcha] chromium 安装失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        debug_logger.log_warning(f"[BrowserCaptcha] chromium 安装异常: {e}")
        return False


def _ensure_playwright_installed() -> bool:
    """确保 playwright 已安装"""
    try:
        import playwright
        debug_logger.log_info("[BrowserCaptcha] playwright 已安装")
        return True
    except ImportError:
        pass
    
    debug_logger.log_info("[BrowserCaptcha] playwright 未安装，开始自动安装...")
    print("[BrowserCaptcha] playwright 未安装，开始自动安装...")
    
    # 先尝试官方源
    if _run_pip_install('playwright', use_mirror=False):
        return True
    
    # 官方源失败，尝试国内镜像
    debug_logger.log_info("[BrowserCaptcha] 官方源安装失败，尝试国内镜像...")
    print("[BrowserCaptcha] 官方源安装失败，尝试国内镜像...")
    if _run_pip_install('playwright', use_mirror=True):
        return True
    
    debug_logger.log_error("[BrowserCaptcha] ❌ playwright 自动安装失败，请手动安装: pip install playwright")
    print("[BrowserCaptcha] ❌ playwright 自动安装失败，请手动安装: pip install playwright")
    return False


def _ensure_browser_installed() -> bool:
    """确保 chromium 浏览器已安装"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # 尝试获取浏览器路径，如果失败说明未安装
            browser_path = p.chromium.executable_path
            if browser_path and os.path.exists(browser_path):
                debug_logger.log_info(f"[BrowserCaptcha] chromium 浏览器已安装: {browser_path}")
                return True
    except Exception as e:
        debug_logger.log_info(f"[BrowserCaptcha] 检测浏览器时出错: {e}")
    
    debug_logger.log_info("[BrowserCaptcha] chromium 浏览器未安装，开始自动安装...")
    print("[BrowserCaptcha] chromium 浏览器未安装，开始自动安装...")
    
    # 先尝试官方源
    if _run_playwright_install(use_mirror=False):
        return True
    
    # 官方源失败，尝试国内镜像
    debug_logger.log_info("[BrowserCaptcha] 官方源安装失败，尝试国内镜像...")
    print("[BrowserCaptcha] 官方源安装失败，尝试国内镜像...")
    if _run_playwright_install(use_mirror=True):
        return True
    
    debug_logger.log_error("[BrowserCaptcha] ❌ chromium 浏览器自动安装失败，请手动安装: python -m playwright install chromium")
    print("[BrowserCaptcha] ❌ chromium 浏览器自动安装失败，请手动安装: python -m playwright install chromium")
    return False


# 尝试导入 playwright
async_playwright = None
Route = None
BrowserContext = None
PLAYWRIGHT_AVAILABLE = False

if IS_DOCKER:
    debug_logger.log_warning("[BrowserCaptcha] 检测到 Docker 环境，有头浏览器打码不可用，请使用第三方打码服务")
    print("[BrowserCaptcha] ⚠️ 检测到 Docker 环境，有头浏览器打码不可用")
    print("[BrowserCaptcha] 请使用第三方打码服务: yescaptcha, capmonster, ezcaptcha, capsolver")
else:
    if _ensure_playwright_installed():
        try:
            from playwright.async_api import async_playwright, Route, BrowserContext
            PLAYWRIGHT_AVAILABLE = True
            # 检查并安装浏览器
            _ensure_browser_installed()
        except ImportError as e:
            debug_logger.log_error(f"[BrowserCaptcha] playwright 导入失败: {e}")
            print(f"[BrowserCaptcha] ❌ playwright 导入失败: {e}")


# 配置
LABS_URL = "https://labs.google/fx/tools/flow"

# ==========================================
# 代理解析工具函数
# ==========================================
def parse_proxy_url(proxy_url: str) -> Optional[Dict[str, str]]:
    """解析代理URL"""
    if not proxy_url: return None
    if not re.match(r'^(http|https|socks5)://', proxy_url): proxy_url = f"http://{proxy_url}"
    match = re.match(r'^(socks5|http|https)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$', proxy_url)
    if match:
        protocol, username, password, host, port = match.groups()
        proxy_config = {'server': f'{protocol}://{host}:{port}'}
        if username and password:
            proxy_config['username'] = username
            proxy_config['password'] = password
        return proxy_config
    return None

def validate_browser_proxy_url(proxy_url: str) -> tuple[bool, str]:
    if not proxy_url: return True, None
    parsed = parse_proxy_url(proxy_url)
    if not parsed: return False, "代理格式错误"
    return True, None

class TokenBrowser:
    """简化版浏览器：每次获取 token 时启动新浏览器，用完即关
    
    每次都是新的随机 UA，避免长时间运行导致的各种问题
    """
    
    # UA 池
    UA_LIST = [
        # Windows Chrome (120-132)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Windows Chrome 完整版本号
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.83 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.139 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.117 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.6668.100 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.138 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.120 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.127 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.141 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        # Windows Edge (120-132)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.6834.83 Safari/537.36 Edg/132.0.2957.115",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.139 Safari/537.36 Edg/131.0.2903.99",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.117 Safari/537.36 Edg/130.0.2849.80",
        # macOS Chrome (120-132)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        # macOS Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        # macOS Edge
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
        # Linux Chrome
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        # Linux Firefox
        "Mozilla/5.0 (X11; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",
        # Windows Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        # macOS Firefox
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
        # Opera
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 OPR/115.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 OPR/114.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 OPR/113.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 OPR/112.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 OPR/115.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
        # Brave
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Brave/131",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Brave/130",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Brave/131",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Brave/131",
        # Vivaldi
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Vivaldi/6.9.3447.54",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Vivaldi/6.8.3381.55",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Vivaldi/6.9.3447.54",
    ]
    
    # 分辨率池
    RESOLUTIONS = [
        (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1536, 864),
        (1600, 900), (1280, 720), (1360, 768), (1920, 1200),
        (1440, 900), (1680, 1050), (1280, 800), (2560, 1600),
        (2880, 1800), (3024, 1890), (3456, 2160),
        (1280, 1024), (1024, 768), (1400, 1050),
        (1920, 1280), (2736, 1824), (2880, 1920), (3000, 2000),
        (2256, 1504), (2496, 1664), (3240, 2160),
        (3200, 1800), (2304, 1440), (1800, 1200),
    ]
    
    def __init__(self, token_id: int, user_data_dir: str, db=None):
        self.token_id = token_id
        self.user_data_dir = user_data_dir
        self.db = db
        self._semaphore = asyncio.Semaphore(1)  # 同时只能有一个任务
        self._solve_count = 0
        self._error_count = 0

    async def _close_roxy_browser(self, profile_id: str):
        """调用 Roxy API 彻底关闭窗口进程"""
        close_url = "http://127.0.0.1:50000/browser/close"
        headers = {
            "token": "c1454d1ae361de8a818a8db8e265d46f",
            "Content-Type": "application/json"
        }
        payload = {"dirId": profile_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(close_url, headers=headers, json=payload, timeout=5) as resp:
                    res = await resp.json()
                    if res.get("code") == 0:
                        print(f"================= 7. Roxy 窗口 {profile_id} 已成功关闭 =================")
                    else:
                        print(f"================= 警告：窗口关闭失败: {res} =================")
        except Exception as e:
            print(f"================= 严重错误：无法连接到 Roxy 关闭接口: {e} =================")

    async def _create_browser(self) -> tuple:
        """接管指定的 RoxyBrowser 环境"""
        print("================= 1. 准备接管 Roxy 流程 =================")
        playwright = await async_playwright().start()

        # 配置信息 (已根据您的要求硬编码)
        roxy_token = "c1454d1ae361de8a818a8db8e265d46f"
        profile_id = "ffec80442cd6e0930f6df16bafc29e74"

        api_url = "http://127.0.0.1:50000/browser/open"
        headers = {"token": roxy_token, "Content-Type": "application/json"}
        payload = {"dirId": profile_id}

        try:
            print(f"================= 2. 发送请求开启窗口: {profile_id} =================")
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, headers=headers, json=payload, timeout=15) as resp:
                    res_json = await resp.json()
                    print(f"================= 3. Roxy 返回: {res_json} =================")

                    if res_json.get("code") != 0:
                        raise Exception(f"Roxy启动拒绝: {res_json.get('msg')}")

                    ws_endpoint = res_json["data"]["ws"]
                    print(f"================= 4. 拿到 WS 地址: {ws_endpoint} =================")

            print("================= 5. Playwright 正在连接 CDP... =================")
            # 这里的 timeout 稍微拉长，防止接管慢
            browser = await playwright.chromium.connect_over_cdp(ws_endpoint, timeout=30000)
            print("================= 6. 接管成功！ =================")

            # 接管已有浏览器时，使用现有的 context
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context()

            return playwright, browser, context

        except Exception as e:
            print(f"\n!!!!!!!!!!!!!!!!!! 致命错误: {e} !!!!!!!!!!!!!!!!!!\n")
            if playwright: await playwright.stop()
            raise
    
    async def _close_browser(self, playwright, browser, context):
        """关闭浏览器实例"""
        try:
            if context:
                await context.close()
        except: pass
        try:
            if browser:
                await browser.close()
        except: pass
        try:
            if playwright:
                await playwright.stop()
        except: pass
    
    async def _execute_captcha(self, context, project_id: str, website_key: str, action: str) -> Optional[str]:
        """在给定 context 中执行打码逻辑"""
        page = None
        try:
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            
            page_url = f"https://labs.google/fx/tools/flow/project/{project_id}"
            
            async def handle_route(route):
                if route.request.url.rstrip('/') == page_url.rstrip('/'):
                    html = f"""<html><head><script src="https://www.google.com/recaptcha/enterprise.js?render={website_key}"></script></head><body></body></html>"""
                    await route.fulfill(status=200, content_type="text/html", body=html)
                elif any(d in route.request.url for d in ["google.com", "gstatic.com", "recaptcha.net"]):
                    await route.continue_()
                else:
                    await route.abort()
            
            await page.route("**/*", handle_route)
            try:
                await page.goto(page_url, wait_until="load", timeout=30000)
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Token-{self.token_id} page.goto 失败: {type(e).__name__}: {str(e)[:200]}")
                return None
            
            try:
                await page.wait_for_function("typeof grecaptcha !== 'undefined'", timeout=15000)
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Token-{self.token_id} grecaptcha 未就绪: {type(e).__name__}: {str(e)[:200]}")
                return None
            
            token = await asyncio.wait_for(
                page.evaluate(f"""
                    (actionName) => {{
                        return new Promise((resolve, reject) => {{
                            const timeout = setTimeout(() => reject(new Error('timeout')), 25000);
                            grecaptcha.enterprise.execute('{website_key}', {{action: actionName}})
                                .then(t => {{ resolve(t); }})
                                .catch(e => {{ reject(e); }});
                        }});
                    }}
                """, action),
                timeout=30
            )
            return token
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)}"
            debug_logger.log_warning(f"[BrowserCaptcha] Token-{self.token_id} 打码失败: {msg[:200]}")
            return None
        finally:
            if page:
                try: await page.close()
                except: pass

    async def get_token(self, project_id: str, website_key: str, action: str = "IMAGE_GENERATION") -> Optional[str]:
        """获取 Token：唤醒 -> 打码 -> 彻底杀掉进程"""
        async with self._semaphore:
            MAX_RETRIES = 2  # 24x7 场景建议减少重试，防止卡死
            roxy_token = "c1454d1ae361de8a818a8db8e265d46f"
            profile_id = "ffec80442cd6e0930f6df16bafc29e74"

            for attempt in range(MAX_RETRIES):
                playwright, browser, context = None, None, None
                try:
                    # 1. 唤醒并接管
                    playwright, browser, context = await self._create_browser()

                    # 2. 执行打码逻辑
                    token = await self._execute_captcha(context, project_id, website_key, action)

                    if token:
                        debug_logger.log_info(f"[BrowserCaptcha] Token 产出成功")
                        return token

                except Exception as e:
                    debug_logger.log_error(f"[BrowserCaptcha] 运行异常: {e}")
                finally:
                    # --- 彻底清理现场 ---
                    try:
                        if browser: await browser.close()
                        if playwright: await playwright.stop()

                        # 调用 Roxy 接口关闭窗口进程 (关键！)
                        close_url = "http://127.0.0.1:50000/browser/close"
                        async with aiohttp.ClientSession() as session:
                            await session.post(close_url,
                                               headers={"token": roxy_token},
                                               json={"dirId": profile_id})
                        print(f"================= 7. 窗口 {profile_id} 已彻底关闭进程 =================")
                    except:
                        pass

                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2)
            return None
    

class BrowserCaptchaService:
    """多浏览器轮询打码服务（单例模式）
    
    支持配置浏览器数量，每个浏览器只开 1 个标签页，请求轮询分配
    """
    
    _instance: Optional['BrowserCaptchaService'] = None
    _lock = asyncio.Lock()
    
    def __init__(self, db=None):
        self.db = db
        self.website_key = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
        self.base_user_data_dir = os.path.join(os.getcwd(), "browser_data_rt")
        self._browsers: Dict[int, TokenBrowser] = {}
        self._browsers_lock = asyncio.Lock()
        
        # 浏览器数量配置
        self._browser_count = 1  # 默认 1 个，会从数据库加载
        self._round_robin_index = 0  # 轮询索引
        
        # 统计指标
        self._stats = {
            "req_total": 0,
            "gen_ok": 0,
            "gen_fail": 0,
            "api_403": 0
        }
        
        # 并发限制将在 _load_browser_count 中根据配置设置
        self._token_semaphore = None
    
    @classmethod
    async def get_instance(cls, db=None) -> 'BrowserCaptchaService':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db)
                    # 从数据库加载 browser_count 配置
                    await cls._instance._load_browser_count()
        return cls._instance
    
    def _check_available(self):
        """检查服务是否可用"""
        if IS_DOCKER:
            raise RuntimeError(
                "有头浏览器打码在 Docker 环境中不可用。"
                "请使用第三方打码服务: yescaptcha, capmonster, ezcaptcha, capsolver"
            )
        if not PLAYWRIGHT_AVAILABLE or async_playwright is None:
            raise RuntimeError(
                "playwright 未安装或不可用。"
                "请手动安装: pip install playwright && python -m playwright install chromium"
            )
    
    async def _load_browser_count(self):
        """从数据库加载浏览器数量配置"""
        if self.db:
            try:
                captcha_config = await self.db.get_captcha_config()
                self._browser_count = max(1, captcha_config.browser_count)
                debug_logger.log_info(f"[BrowserCaptcha] 浏览器数量配置: {self._browser_count}")
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] 加载 browser_count 配置失败: {e}，使用默认值 1")
                self._browser_count = 1
        # 并发限制 = 浏览器数量，不再硬编码限制
        self._token_semaphore = asyncio.Semaphore(self._browser_count)
        debug_logger.log_info(f"[BrowserCaptcha] 并发上限: {self._browser_count}")
    
    async def reload_browser_count(self):
        """重新加载浏览器数量配置（用于配置更新后热重载）"""
        old_count = self._browser_count
        await self._load_browser_count()
        
        # 如果数量减少，移除多余的浏览器实例
        if self._browser_count < old_count:
            async with self._browsers_lock:
                for browser_id in list(self._browsers.keys()):
                    if browser_id >= self._browser_count:
                        self._browsers.pop(browser_id)
                        debug_logger.log_info(f"[BrowserCaptcha] 移除多余浏览器实例 {browser_id}")
    
    def _log_stats(self):
        total = self._stats["req_total"]
        gen_fail = self._stats["gen_fail"]
        api_403 = self._stats["api_403"]
        gen_ok = self._stats["gen_ok"]
        
        valid_success = gen_ok - api_403
        if valid_success < 0: valid_success = 0
        
        rate = (valid_success / total * 100) if total > 0 else 0.0

    
    async def _get_or_create_browser(self, browser_id: int) -> TokenBrowser:
        """获取或创建指定 ID 的浏览器实例"""
        async with self._browsers_lock:
            if browser_id not in self._browsers:
                user_data_dir = os.path.join(self.base_user_data_dir, f"browser_{browser_id}")
                browser = TokenBrowser(browser_id, user_data_dir, db=self.db)
                self._browsers[browser_id] = browser
                debug_logger.log_info(f"[BrowserCaptcha] 创建浏览器实例 {browser_id}")
            return self._browsers[browser_id]
    
    def _get_next_browser_id(self) -> int:
        """轮询获取下一个浏览器 ID"""
        browser_id = self._round_robin_index % self._browser_count
        self._round_robin_index += 1
        return browser_id
    
    async def get_token(self, project_id: str, action: str = "IMAGE_GENERATION", token_id: int = None) -> tuple[Optional[str], int]:
        """获取 reCAPTCHA Token（轮询分配到不同浏览器）
        
        Args:
            project_id: 项目 ID
            action: reCAPTCHA action
            token_id: 忽略，使用轮询分配
        
        Returns:
            (token, browser_id) 元组，调用方失败时用 browser_id 调用 report_error
        """
        # 检查服务是否可用
        self._check_available()
        
        self._stats["req_total"] += 1
        
        # 全局并发限制（如果已配置）
        if self._token_semaphore:
            async with self._token_semaphore:
                # 轮询选择浏览器
                browser_id = self._get_next_browser_id()
                browser = await self._get_or_create_browser(browser_id)
                
                token = await browser.get_token(project_id, self.website_key, action)
            
            if token:
                self._stats["gen_ok"] += 1
            else:
                self._stats["gen_fail"] += 1
                
            self._log_stats()
            return token, browser_id
        
        # 无并发限制时直接执行
        browser_id = self._get_next_browser_id()
        browser = await self._get_or_create_browser(browser_id)
        
        token = await browser.get_token(project_id, self.website_key, action)
        
        if token:
            self._stats["gen_ok"] += 1
        else:
            self._stats["gen_fail"] += 1
            
        self._log_stats()
        return token, browser_id

    async def report_error(self, browser_id: int = None):
        """上层举报：Token 无效（统计用）
        
        Args:
            browser_id: 浏览器 ID（当前架构下每次都是新浏览器，此参数仅用于日志）
        """
        async with self._browsers_lock:
            self._stats["api_403"] += 1
            if browser_id is not None:
                debug_logger.log_info(f"[BrowserCaptcha] 浏览器 {browser_id} 的 token 验证失败")

    async def remove_browser(self, browser_id: int):
        async with self._browsers_lock:
            if browser_id in self._browsers:
                self._browsers.pop(browser_id)

    async def close(self):
        """释放资源并彻底关闭 RoxyBrowser"""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

            # ------ 增加调用 Roxy 的关闭接口 ------
            api_url = "http://127.0.0.1:50000/browser/close"
            headers = {"token": "c1454d1ae361de8a818a8db8e265d46f", "Content-Type": "application/json"}
            payload = {"dirId": "ffec80442cd6e0930f6df16bafc29e74"}  # 需与上面一致

            async with aiohttp.ClientSession() as session:
                await session.post(api_url, headers=headers, json=payload)
            debug_logger.log_info("[BrowserCaptcha] 已通过 API 彻底关闭 RoxyBrowser 进程")
            # -------------------------------------

        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] 关闭浏览器环境失败: {e}")
            
    async def open_login_browser(self): return {"success": False, "error": "Not implemented"}
    async def create_browser_for_token(self, t, s=None): pass
    def get_stats(self): 
        base_stats = {
            "total_solve_count": self._stats["gen_ok"],
            "total_error_count": self._stats["gen_fail"],
            "risk_403_count": self._stats["api_403"],
            "browser_count": len(self._browsers),
            "configured_browser_count": self._browser_count,
            "browsers": []
        }
        return base_stats
