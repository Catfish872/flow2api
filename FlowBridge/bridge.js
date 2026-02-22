// @name         Flow 2API Bridge (文本框可复制版)
// @namespace    http://tampermonkey.net/
// @version      1.3
// @description  修复权限问题，并将日志改为可复制的文本框
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

    // --- 创建可复制的 UI 日志面板 ---
    const container = document.createElement('div');
    container.style = "position:fixed;top:10px;right:10px;width:350px;z-index:99999;background:#1e1e1e;padding:10px;border-radius:8px;box-shadow:0 4px 15px rgba(0,0,0,0.5);border:1px solid #444;";

    const title = document.createElement('div');
    title.innerHTML = "<b style='color:#0f0'>Flow Bridge Log</b> (可直接选中下方文字复制)";
    title.style = "color:#ccc;font-size:12px;margin-bottom:5px;font-family:sans-serif;";

    // 使用 textarea 代替普通的 div，确保 100% 可复制
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
        logArea.scrollTop = logArea.scrollHeight; // 自动滚动到底部
        console.log(`[FlowBridge] ${msg}`);
    }

    if (!projectId) {
        addLog("❌ 错误: URL 中未找到 Project ID");
        return;
    }
    addLog(`✅ 已监听项目: ${projectId}`);

    async function checkTask() {
        if (isWorking) return;
        try {
            // 注意：fetch 本地接口不需要 GM_xmlhttpRequest
            const res = await fetch(`${PYTHON_SERVER}/tm/task?project_id=${projectId}`);
            if (!res.ok) return;
            const task = await res.json();
            if (task && task.task_id) {
                isWorking = true;
                addLog(`📥 收到任务: ${task.task_id.substring(0,8)}`);
                await processTask(task);
                isWorking = false;
            }
        } catch (e) { /* 忽略后端关闭的情况 */ }
    }

    async function processTask(task) {
        try {
            // 1. 获取 Token
            if (task.action && typeof grecaptcha !== 'undefined') {
                addLog("⏳ 正在请求 reCAPTCHA Token...");
                const token = await grecaptcha.enterprise.execute('6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV', {action: task.action});
                if (task.body?.clientContext?.recaptchaContext) {
                    task.body.clientContext.recaptchaContext.token = token;
                }
                addLog("✅ Token 获取成功");
            }

            // 2. 发送请求给 Google (使用修复后的 GM_xmlhttpRequest)
            addLog("📤 正在发送 API 请求到 Google...");

            if (typeof GM_xmlhttpRequest === 'undefined') {
                addLog("🚨 严重错误: GM_xmlhttpRequest 依然未定义！请检查油猴设置中的安全限制。");
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

                    // 回传结果给 Python
                    fetch(`${PYTHON_SERVER}/tm/result`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ task_id: task.task_id, status: res.status, data: resultData })
                    }).then(() => addLog("✨ 结果已成功回传 Python"));
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

    setInterval(checkTask, 2000);
})();