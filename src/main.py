"""FastAPI application initialization"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
import random

from .core.config import config
from .core.database import Database
from .services.flow_client import FlowClient
from .services.proxy_manager import ProxyManager
from .services.token_manager import TokenManager
from .services.load_balancer import LoadBalancer
from .services.concurrency_manager import ConcurrencyManager
from .services.generation_handler import GenerationHandler
from .api import routes, admin
import webbrowser
import sqlite3
import asyncio
from threading import Timer
import os
import subprocess


async def auto_restart_edge_task():
    """定时维护任务：极简模式，到点强制重启，依赖请求重试机制兜底"""
    print("🚀 [系统] 浏览器自动重启维护任务已启动 (极简强制模式)...")

    while True:
        try:
            # 1. 随机等待 50-70 分钟
            wait_seconds = random.randint(3000, 4200)
            print(f"⏱️ [维护] 下次 Edge 强制重启将在 {wait_seconds // 60} 分钟后执行...")
            await asyncio.sleep(wait_seconds)

            # 2. 到点直接强杀（不判断活跃数，直接重启）
            print("🔄 [维护] 维护时间到，正在强制重启 Edge 以维持 Session 活力...")
            if os.name == 'nt':  # Windows 环境
                subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)

            await asyncio.sleep(5)  # 等待进程彻底清理

            # 3. 重新调用页面注入
            auto_open_project_pages()
            print("✅ [维护] Edge 进程已强制刷新并重新注入")

        except asyncio.CancelledError:
            # 正常关闭信号
            print("🛑 [维护] 接收到关闭信号，重启任务安全退出。")
            break
        except Exception as e:
            print(f"❌ [重启维护异常]: {e}")
            await asyncio.sleep(60)  # 报错缓冲
def auto_open_project_pages():
    print("⏳ [诊断] 正在尝试读取数据库...")
    try:
        # 增加 check_same_thread=False 防止多线程冲突
        with sqlite3.connect('data/flow.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_project_id FROM tokens WHERE is_active = 1")
            rows = cursor.fetchall()
            project_ids = [row[0] for row in rows if row[0]]

        print(f"🔎 [诊断] 数据库扫描完成，找到活跃 ID 数量: {len(project_ids)}")

        if not project_ids:
            print("⚠️ [警告] 数据库中没有 status 为 'active' 的项目，请检查 Token 列表！")
            return

        edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

        # 确认文件是否存在
        if not os.path.exists(edge_path):
            print(f"❌ [错误] 找不到 Edge 路径: {edge_path}")
            return

        profile_dir = "Profile 2"  # 请务必确认此名在 edge://version 中完全一致

        for p_id in project_ids:
            url = f"https://labs.google/fx/tools/flow/project/{p_id}"
            # 增加 --no-first-run 减少弹窗干扰
            cmd = f'"{edge_path}" --profile-directory="{profile_dir}" --no-first-run "{url}"'
            print(f"🚀 [执行] 正在启动命令: {cmd}")
            subprocess.Popen(cmd, shell=True)

    except Exception as e:
        print(f"🚨 [崩溃] 自动启动逻辑出错: {str(e)}")


def start_auto_open():
    print("🔔 [系统] 5秒后将自动触发浏览器注入...")
    Timer(5, auto_open_project_pages).start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    restart_task_handle = asyncio.create_task(auto_restart_edge_task())
    start_auto_open()
    # Startup
    print("=" * 60)
    print("Flow2API Starting...")
    print("=" * 60)

    # Get config from setting.toml
    config_dict = config.get_raw_config()

    # Check if database exists (determine if first startup)
    is_first_startup = not db.db_exists()

    # Initialize database tables structure
    await db.init_db()

    # Handle database initialization based on startup type
    if is_first_startup:
        print("🎉 First startup detected. Initializing database and configuration from setting.toml...")
        await db.init_config_from_toml(config_dict, is_first_startup=True)
        print("✓ Database and configuration initialized successfully.")
    else:
        print("🔄 Existing database detected. Checking for missing tables and columns...")
        await db.check_and_migrate_db(config_dict)
        print("✓ Database migration check completed.")

    # Load admin config from database
    admin_config = await db.get_admin_config()
    if admin_config:
        config.set_admin_username_from_db(admin_config.username)
        config.set_admin_password_from_db(admin_config.password)
        config.api_key = admin_config.api_key

    # Load cache configuration from database
    cache_config = await db.get_cache_config()
    config.set_cache_enabled(cache_config.cache_enabled)
    config.set_cache_timeout(cache_config.cache_timeout)
    config.set_cache_base_url(cache_config.cache_base_url or "")

    # Load generation configuration from database
    generation_config = await db.get_generation_config()
    config.set_image_timeout(generation_config.image_timeout)
    config.set_video_timeout(generation_config.video_timeout)

    # Load debug configuration from database
    debug_config = await db.get_debug_config()
    config.set_debug_enabled(debug_config.enabled)

    # Load captcha configuration from database
    captcha_config = await db.get_captcha_config()
    
    config.set_captcha_method(captcha_config.captcha_method)
    config.set_yescaptcha_api_key(captcha_config.yescaptcha_api_key)
    config.set_yescaptcha_base_url(captcha_config.yescaptcha_base_url)
    config.set_capmonster_api_key(captcha_config.capmonster_api_key)
    config.set_capmonster_base_url(captcha_config.capmonster_base_url)
    config.set_ezcaptcha_api_key(captcha_config.ezcaptcha_api_key)
    config.set_ezcaptcha_base_url(captcha_config.ezcaptcha_base_url)
    config.set_capsolver_api_key(captcha_config.capsolver_api_key)
    config.set_capsolver_base_url(captcha_config.capsolver_base_url)

    # Initialize browser captcha service if needed
    browser_service = None
    if captcha_config.captcha_method == "personal":
        from .services.browser_captcha_personal import BrowserCaptchaService
        browser_service = await BrowserCaptchaService.get_instance(db)
        print("✓ Browser captcha service initialized (nodriver mode)")
        
        # 启动常驻模式：从第一个可用token获取project_id
        tokens = await token_manager.get_all_tokens()
        resident_project_id = None
        for t in tokens:
            if t.current_project_id and t.is_active:
                resident_project_id = t.current_project_id
                break
        
        if resident_project_id:
            # 直接启动常驻模式（会自动导航到项目页面，cookie已持久化）
            await browser_service.start_resident_mode(resident_project_id)
            print(f"✓ Browser captcha resident mode started (project: {resident_project_id[:8]}...)")
        else:
            # 没有可用的project_id时，打开登录窗口供用户手动操作
            await browser_service.open_login_window()
            print("⚠ No active token with project_id found, opened login window for manual setup")
    elif captcha_config.captcha_method == "browser":
        from .services.browser_captcha import BrowserCaptchaService
        browser_service = await BrowserCaptchaService.get_instance(db)
        print("✓ Browser captcha service initialized (headless mode)")

    # Initialize concurrency manager
    tokens = await token_manager.get_all_tokens()

    await concurrency_manager.initialize(tokens)

    # Start file cache cleanup task
    await generation_handler.file_cache.start_cleanup_task()

    # Start 429 auto-unban task
    async def auto_unban_task():
        """定时任务：每小时检查并解禁429被禁用的token"""
        while True:
            try:
                await asyncio.sleep(3600)  # 每小时执行一次
                await token_manager.auto_unban_429_tokens()
            except Exception as e:
                print(f"❌ Auto-unban task error: {e}")

    auto_unban_task_handle = asyncio.create_task(auto_unban_task())


    print(f"✓ Database initialized")
    print(f"✓ Total tokens: {len(tokens)}")
    print(f"✓ Cache: {'Enabled' if config.cache_enabled else 'Disabled'} (timeout: {config.cache_timeout}s)")
    print(f"✓ File cache cleanup task started")
    print(f"✓ 429 auto-unban task started (runs every hour)")
    print(f"✓ Server running on http://{config.server_host}:{config.server_port}")
    print("=" * 60)

    yield

    # Shutdown
    print("Flow2API Shutting down...")
    restart_task_handle.cancel()
    try:
        await restart_task_handle
    except asyncio.CancelledError:
        pass
    # Stop file cache cleanup task
    await generation_handler.file_cache.stop_cleanup_task()
    # Stop auto-unban task
    restart_task_handle.cancel()
    auto_unban_task_handle.cancel()
    try:
        await auto_unban_task_handle
    except asyncio.CancelledError:
        pass
    # Close browser if initialized
    if browser_service:
        await browser_service.close()
        print("✓ Browser captcha service closed")
    print("✓ File cache cleanup task stopped")
    print("✓ 429 auto-unban task stopped")


# Initialize components
db = Database()
proxy_manager = ProxyManager(db)
flow_client = FlowClient(proxy_manager, db)
token_manager = TokenManager(db, flow_client)
concurrency_manager = ConcurrencyManager()
load_balancer = LoadBalancer(token_manager, concurrency_manager)
generation_handler = GenerationHandler(
    flow_client,
    token_manager,
    load_balancer,
    db,
    concurrency_manager,
    proxy_manager  # 添加 proxy_manager 参数
)

# Set dependencies
routes.set_generation_handler(generation_handler)
admin.set_dependencies(token_manager, proxy_manager, db)

# Create FastAPI app
app = FastAPI(
    title="Flow2API",
    description="OpenAI-compatible API for Google VideoFX (Veo)",
    version="1.0.0",
    lifespan=lifespan
)

@app.on_event("startup")
async def startup_event():
    # 启动自动打开任务
    start_auto_open()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 允许所有来源，包括 google.com
    allow_credentials=True,
    allow_headers=["*"],
    allow_methods=["*"],
)

# Include routers
app.include_router(routes.router)
app.include_router(admin.router)

# Static files - serve tmp directory for cached files
tmp_dir = Path(__file__).parent.parent / "tmp"
tmp_dir.mkdir(exist_ok=True)
app.mount("/tmp", StaticFiles(directory=str(tmp_dir)), name="tmp")

# HTML routes for frontend
static_path = Path(__file__).parent.parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    """Redirect to login page"""
    login_file = static_path / "login.html"
    if login_file.exists():
        return FileResponse(str(login_file))
    return HTMLResponse(content="<h1>Flow2API</h1><p>Frontend not found</p>", status_code=404)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Login page"""
    login_file = static_path / "login.html"
    if login_file.exists():
        return FileResponse(str(login_file))
    return HTMLResponse(content="<h1>Login Page Not Found</h1>", status_code=404)


@app.get("/manage", response_class=HTMLResponse)
async def manage_page():
    """Management console page"""
    manage_file = static_path / "manage.html"
    if manage_file.exists():
        return FileResponse(str(manage_file))
    return HTMLResponse(content="<h1>Management Page Not Found</h1>", status_code=404)
