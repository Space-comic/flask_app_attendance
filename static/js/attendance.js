let camera = null;
let liveness = null;
let autoTimer = null;
let recognizing = false;

function resetLivenessEntry() {
    window._sessionToken = null;
    const btnStart = document.getElementById('btn-start-liveness');
    const btnCapture = document.getElementById('btn-capture-checkin');
    const btnManual = document.getElementById('btn-manual');
    const btnAuto = document.getElementById('btn-auto');

    if (liveness) {
        liveness.stop();
        liveness = null;
    }
    if (autoTimer) {
        clearInterval(autoTimer);
        autoTimer = null;
    }

    if (btnStart) btnStart.disabled = false;
    if (btnCapture) btnCapture.disabled = true;
    if (btnManual) btnManual.disabled = true;
    if (btnAuto) {
        btnAuto.disabled = true;
        btnAuto.textContent = '开启自动识别';
        btnAuto.classList.remove('btn-danger');
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (!getToken()) {
        window.location.href = '/';
        return;
    }

    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');
    const statusEl = document.getElementById('liveness-status');
    const progressEl = document.getElementById('mouth-count');
    const btnManual = document.getElementById('btn-manual');
    const btnAuto = document.getElementById('btn-auto');
    const btnStart = document.getElementById('btn-start-liveness');
    const btnCapture = document.getElementById('btn-capture-checkin');

    camera = new CameraManager(video, canvas);

    const booted = await ensureCameraStarted(statusEl);
    if (!booted) {
        statusEl.textContent = '摄像头尚未就绪，可检查权限后点击“开始活体验证”重试。';
        statusEl.className = 'liveness-status status-warning';
    }

    btnStart.addEventListener('click', async () => {
        const ready = await ensureCameraStarted(statusEl);
        if (!ready) {
            return;
        }

        btnStart.disabled = true;
        liveness = new LivenessChecker(
            camera,
            statusEl,
            progressEl,
            onLivenessPassed,
            () => {
                resetLivenessEntry();
            }
        );

        const started = await liveness.start();
        if (!started) {
            btnStart.disabled = false;
        }
    });

    btnCapture.addEventListener('click', () => captureAndRecognize(window._sessionToken));
    btnManual.addEventListener('click', () => captureAndRecognize(window._sessionToken));

    btnAuto.addEventListener('click', () => {
        if (autoTimer) {
            clearInterval(autoTimer);
            autoTimer = null;
            btnAuto.textContent = '开启自动识别';
            btnAuto.classList.remove('btn-danger');
            return;
        }
        autoTimer = setInterval(() => captureAndRecognize(window._sessionToken), 1800);
        btnAuto.textContent = '停止自动识别';
        btnAuto.classList.add('btn-danger');
    });

    window.addEventListener('beforeunload', () => {
        if (autoTimer) {
            clearInterval(autoTimer);
            autoTimer = null;
        }
        if (liveness) {
            liveness.stop();
            liveness = null;
        }
        if (camera) {
            camera.stop();
        }
    });

    loadTodayLog();
});

async function ensureCameraStarted(statusEl) {
    try {
        await camera.start();
        return true;
    } catch (error) {
        if (statusEl) {
            statusEl.textContent = CameraManager.explainError(error);
            statusEl.className = 'liveness-status status-warning';
        }
        return false;
    }
}

function onLivenessPassed(sessionToken) {
    window._sessionToken = sessionToken;
    document.getElementById('btn-capture-checkin').disabled = false;
    document.getElementById('btn-manual').disabled = false;
    document.getElementById('btn-auto').disabled = false;

    const statusEl = document.getElementById('liveness-status');
    statusEl.textContent = '活体验证通过，请点击“拍照签到”完成识别。';
    statusEl.className = 'liveness-status status-success';

    const resultEl = document.getElementById('result-card');
    if (resultEl) {
        resultEl.innerHTML = '<div class="alert alert-info">活体验证通过，等待签到。</div>';
    }
}

async function captureAndRecognize(sessionToken) {
    if (!sessionToken || recognizing) {
        return;
    }

    const statusEl = document.getElementById('liveness-status');
    const ready = await ensureCameraStarted(statusEl);
    if (!ready) {
        return;
    }

    recognizing = true;
    const resultEl = document.getElementById('result-card');
    if (resultEl) {
        resultEl.innerHTML = '<div class="alert alert-info">正在识别，请稍候...</div>';
    }

    try {
        const b64 = camera.captureFrame(0.82);
        const res = await apiFetch('/api/attendance/recognize', {
            method: 'POST',
            body: JSON.stringify({ image: b64, session_token: sessionToken }),
        });
        if (!res) {
            return;
        }

        const data = await res.json();
        renderResult(data);
        await loadTodayLog();
    } catch (_) {
        renderResult({ message: '识别请求失败，请重试' });
    } finally {
        recognizing = false;
        resetLivenessEntry();
    }
}

function formatDisplayTime(isoLike) {
    if (!isoLike) {
        return '';
    }
    if (typeof isoLike === 'string') {
        return isoLike.replace('T', ' ').slice(0, 19);
    }
    return String(isoLike);
}

const EMOTION_CN = {
    angry: '愤怒',
    disgust: '厌恶',
    fear: '恐惧',
    happy: '开心',
    neutral: '平静',
    sad: '悲伤',
    surprise: '惊讶',
};

function renderResult(data) {
    const el = document.getElementById('result-card');
    if (!el) {
        return;
    }

    if (data.status === 'success') {
        const displayTime = formatDisplayTime(data.check_time) || new Date().toLocaleString();
        const emoCn = EMOTION_CN[data.emotion] || data.emotion || '未知';
        el.innerHTML = `
            <div class="alert alert-success">
                <h5>${data.student.name} 签到成功</h5>
                <p>学号：${data.student.id}</p>
                <p>情绪：<span class="badge bg-primary">${emoCn}</span></p>
                <p>时间：${displayTime}</p>
            </div>`;
        return;
    }

    if (data.status === 'already_checked') {
        el.innerHTML = `<div class="alert alert-info">${data.message}</div>`;
        return;
    }

    if (data.status === 'unknown') {
        el.innerHTML = `<div class="alert alert-warning">${data.message}</div>`;
        return;
    }

    if (data.status === 'fake' || data.status === 'spoof_detected') {
        el.innerHTML = `<div class="alert alert-danger">${data.message || '检测到疑似屏幕或视频重放攻击，请重新验证。'}</div>`;
        return;
    }

    el.innerHTML = `<div class="alert alert-warning">${data.message || data.error || '识别失败'}</div>`;
}

async function loadTodayLog() {
    const res = await apiFetch('/api/attendance/today-status');
    if (!res) {
        return;
    }

    const data = await res.json();
    const tbody = document.getElementById('today-log');
    if (!tbody) {
        return;
    }

    tbody.innerHTML = (data.checked_in || []).map((record) => `
        <tr>
            <td>${record.student_id}</td>
            <td>${record.student_name}</td>
            <td>${formatDisplayTime(record.check_time)}</td>
            <td><span class="badge bg-success">已签到</span></td>
        </tr>
    `).join('');

    const counter = document.getElementById('checked-count');
    if (counter) {
        counter.textContent = `今日已签到：${data.checked_count} / ${data.total_students}`;
    }
}
