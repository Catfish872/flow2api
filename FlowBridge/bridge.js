// ==UserScript==
// @name         Flow 2API Bridge (深度拟人防风控版)
// @namespace    http://tampermonkey.net/
// @version      2.0
// @description  引入随机鼠标轨迹、平滑滚动、人类反应延迟等防 reCAPTCHA 风控机制
// @author       Gemini
// @match        https://labs.google/fx/tools/flow/project/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      aisandbox-pa.googleapis.com
// ==/UserScript==

(function() {
    'use strict';

    const PYTHON_SERVER = "http://127.0.0.1:8024";
    let isWorking = false;
    const match = window.location.pathname.match(/\/project\/([^/]+)/);
    const projectId = match ? match[1] : null;

    // --- 随机数生成器 ---
    const randomInt = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    // --- 当前虚拟鼠标坐标 ---
    let mouseX = randomInt(100, 800);
    let mouseY = randomInt(100, 600);

    // --- 拟人化：随机平滑滚动 ---
    function simulateRandomScroll() {
        const scrollAmount = randomInt(-300, 300);
        window.scrollBy({ top: scrollAmount, behavior: 'smooth' });
        // addLog(`[模拟行为] 页面滚动了 ${scrollAmount}px`);
    }

    // --- 拟人化：随机鼠标移动 (分段平滑移动) ---
    async function simulateMouseMove() {
        const targetX = randomInt(50, window.innerWidth - 50);
        const targetY = randomInt(50, window.innerHeight - 50);
        
        // 分 5-15 步移动到目标，模拟人类拖拽鼠标的停顿和加速
        const steps = randomInt(5, 15);
        for (let i = 1; i <= steps; i++) {
            mouseX += (targetX - mouseX) / (steps - i + 1) + randomInt(-5, 5);
            mouseY += (targetY - mouseY) / (steps - i + 1) + randomInt(-5, 5);
            
            // 修复：去掉了引发沙盒冲突的 view: window
            const event = new MouseEvent('mousemove', {
                bubbles: true,
                cancelable: true,
                clientX: mouseX,
                clientY: mouseY
            });
            document.dispatchEvent(event);
            
            // 步间随机微小延迟，模拟先快后慢的移动习惯
            await sleep(randomInt(10, 50)); 
        }
    }

    // --- 拟人化：空闲状态随机行为 ---
    async function performIdleBehaviors() {
        if (isWorking) return;
        const rand = Math.random();
        // 30% 概率滑动鼠标，20% 概率滚动页面，50% 概率什么都不做发呆
        if (rand < 0.3) {
            await simulateMouseMove();
        } else if (rand < 0.5) {
            simulateRandomScroll();
        }
    }

    // --- 创建可复制的 UI 日志面板 ---
    const container = document.createElement('div');
    container.style = "position:fixed;top:10px;right:10px;width:350px;z-index:99999;background:#1e1e1e;padding:10px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.5);border:1px solid #444;opacity:0.9;";

    const title = document.createElement('div');
    title.innerHTML = "<b style='color:#0f0'>Flow Bridge (拟人版)</b>";
    title.style = "color:#ccc;font-size:12px;margin-bottom:5px;font-family:sans-serif;";

    const logArea = document.createElement('textarea');
    logArea.style = "width:100%;height:250px;background:#000;color:#0f0;font-family:monospace;font-size:11px;padding:5px;border:1px solid #333;border-radius:4px;resize:vertical;white-space:pre;overflow-y:scroll;";
    logArea.readOnly = true;

    container.appendChild(title);
    container.appendChild(logArea);
    document.body.appendChild(container);

    function addLog(msg) {
        const time = new Date().toLocaleTimeString();
        const newLog = `[${time}] ${msg}\n`;
        logArea.value += newLog;
        logArea.scrollTop = logArea.scrollHeight; 
        console.log(`[FlowBridge] ${msg}`);
    }

    if (!projectId) {
        addLog("❌ 错误: URL 中未找到 Project ID");
        return;
    }
    addLog(`✅ 已监听项目: ${projectId} (防风控模式已开启)`);

    // --- 核心任务拉取循环 ---
    async function checkTaskLoop() {
        if (!isWorking) {
            try {
                const res = await fetch(`${PYTHON_SERVER}/tm/task?project_id=${projectId}`);
                if (res.ok) {
                    const task = await res.json();
                    if (task && task.task_id) {
                        isWorking = true;
                        addLog(`📥 收到任务: ${task.task_id.substring(0,8)}`);
                        await processTask(task);
                        isWorking = false;
                    }
                }
            } catch (e) { /* 忽略后端关闭的情况 */ }
        }

        // 执行一次随机空闲行为
        await performIdleBehaviors();

        // 随机下一次拉取任务的时间 (1.5秒到4秒之间波动，避免规律性请求)
        const nextCheckDelay = randomInt(1500, 4000);
        setTimeout(checkTaskLoop, nextCheckDelay);
    }

    async function processTask(task) {
        try {
            // 1. 获取 Token 前的拟人延迟
            if (task.action && typeof grecaptcha !== 'undefined') {
                const reactionTime = randomInt(800, 2500); // 模拟人类 0.8 到 2.5 秒的反应时间
                addLog(`⏳ 模拟反应延迟 ${reactionTime}ms...`);
                await simulateMouseMove(); // 假装鼠标正在往生成按钮上移动
                await sleep(reactionTime);

                addLog("🛡️ 正在请求 reCAPTCHA Token...");
                const token = await grecaptcha.enterprise.execute('6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV', {action: task.action});
                
                // 1. 替换外层 Token
                if (task.body?.clientContext?.recaptchaContext) {
                    task.body.clientContext.recaptchaContext.token = token;
                }
                // 2. 修复：遍历替换内层 requests 数组中的 Token (新版 Payload 必须)
                if (task.body?.requests && Array.isArray(task.body.requests)) {
                    task.body.requests.forEach(req => {
                        if (req.clientContext?.recaptchaContext) {
                            req.clientContext.recaptchaContext.token = token;
                        }
                    });
                }
                
                addLog("✅ Token 获取成功");
            }

            // 2. 发送请求给 Google
            addLog("📤 正在发送 API 请求到 Google...");

            if (typeof GM_xmlhttpRequest === 'undefined') {
                addLog("🚨 严重错误: GM_xmlhttpRequest 未定义！");
                return;
            }

            GM_xmlhttpRequest({
                method: task.method,
                url: task.url,
                headers: {
                    "Authorization": task.headers["authorization"] || task.headers["Authorization"],
                    "Content-Type": "application/json"
                },
                data: JSON.stringify(task.body),
                timeout: 60000,
                onload: function(res) {
                    addLog(`✅ Google 响应码: ${res.status}`);
                    let resultData;
                    try { resultData = JSON.parse(res.responseText); } catch(e) { resultData = res.responseText; }

                    // 加入微小的结果回传延迟，让节奏更自然
                    setTimeout(() => {
                        fetch(`${PYTHON_SERVER}/tm/result`, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ task_id: task.task_id, status: res.status, data: resultData })
                        }).then(() => addLog("✨ 结果已成功回传"));
                    }, randomInt(200, 600));
                },
                onerror: function(err) {
                    addLog(`❌ 发送失败: 网络连接异常`);
                    submitError(task.task_id, 500, "GM_Network_Error");
                }
            });

        } catch (e) {
            addLog(`❌ 脚本异常: ${e.message}`);
            submitError(task.task_id, 500, e.toString());
        }
    }

    function submitError(tid, code, msg) {
        fetch(`${PYTHON_SERVER}/tm/result`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ task_id: tid, status: code, data: msg })
        });
    }

    // 随机延迟 1~3 秒后启动，错开多个标签页可能同时初始化的峰值
    setTimeout(checkTaskLoop, randomInt(1000, 3000));
})();