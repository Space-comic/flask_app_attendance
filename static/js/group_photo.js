let camera = null;
let busy = false;

document.addEventListener('DOMContentLoaded', async () => {
    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');
    camera = new CameraManager(video, canvas);

    document.getElementById('btn-start-camera')?.addEventListener('click', async () => {
        try {
            await camera.start();
        } catch (e) {
            alert('摄像头启动失败，请检查浏览器权限');
        }
    });

    document.getElementById('btn-capture')?.addEventListener('click', () => {
        try {
            const b64 = camera.captureFrame(0.85);
            document.getElementById('preview-img').src = `data:image/jpeg;base64,${b64}`;
            document.getElementById('captured-b64').value = b64;
            document.getElementById('preview-section').classList.remove('d-none');
        } catch (e) {
            alert('拍照失败，请先开启摄像头');
        }
    });

    document.getElementById('btn-recognize-camera')?.addEventListener('click', async () => {
        const b64 = document.getElementById('captured-b64').value;
        if (!b64) {
            alert('请先拍照');
            return;
        }
        const name = (document.getElementById('activity-name-camera').value || '').trim() || '未命名活动';
        await recognizeBase64(b64, name);
    });

    document.getElementById('btn-recognize-file')?.addEventListener('click', async () => {
        const file = document.getElementById('photo-file').files[0];
        if (!file) {
            alert('请选择图片文件');
            return;
        }
        const name = (document.getElementById('activity-name-file').value || '').trim() || '未命名活动';
        await recognizeFile(file, name);
    });

    await loadActivities();
    await loadStats();
});

async function recognizeBase64(b64, activityName) {
    if (busy) return;
    busy = true;
    showLoading(true);

    try {
        const res = await apiFetch('/api/group-photo/recognize', {
            method: 'POST',
            body: JSON.stringify({ image: b64, activity_name: activityName }),
        });
        showLoading(false);
        if (!res) return;
        const data = await res.json();
        renderResult(data);

        if (data.status === 'success') {
            await loadActivities();
            await loadStats();
            if (data.activity_id) {
                await viewParticipants(data.activity_id);
            }
        }
    } catch (e) {
        showLoading(false);
        document.getElementById('result-section').innerHTML =
            '<div class="alert alert-warning">识别请求失败，请重试</div>';
    } finally {
        busy = false;
    }
}

async function recognizeFile(file, activityName) {
    if (busy) return;
    busy = true;
    showLoading(true);

    try {
        const formData = new FormData();
        formData.append('image', file);
        formData.append('activity_name', activityName);

        const res = await fetch('/api/group-photo/recognize', {
            method: 'POST',
            headers: { Authorization: `Bearer ${getToken()}` },
            body: formData,
        });

        showLoading(false);
        const data = await res.json();
        renderResult(data);

        if (res.ok && data.status === 'success') {
            await loadActivities();
            await loadStats();
            if (data.activity_id) {
                await viewParticipants(data.activity_id);
            }
        }
    } catch (e) {
        showLoading(false);
        document.getElementById('result-section').innerHTML =
            '<div class="alert alert-warning">上传识别失败，请重试</div>';
    } finally {
        busy = false;
    }
}

function renderResult(data) {
    const el = document.getElementById('result-section');

    if (!data || data.status !== 'success') {
        el.innerHTML = `<div class="alert alert-warning">${(data && (data.message || data.error)) || '识别失败'}</div>`;
        return;
    }

    const annotated = `<img src="data:image/jpeg;base64,${data.annotated_image}" class="img-fluid rounded mb-2">`;
    const rows = (data.detections || [])
        .map((p) => `<tr><td>${p.index}</td><td>${p.student_id || '-'}</td><td>${p.student_name || '未知'}</td><td>${p.distance ?? '-'}</td><td>${p.confidence ?? '-'}</td></tr>`)
        .join('');

    el.innerHTML = `
        <div class="mb-2">
            <div class="alert alert-success">
                检测到 <strong>${data.total_faces || 0}</strong> 张人脸，
                识别到 <strong>${data.recognized_count || 0}</strong> 人，
                未识别 <strong>${data.unknown_count || 0}</strong> 人
                <div class="text-sm mt-1">检测后端：${data.detector_backend || '-'}</div>
            </div>
            ${annotated}
        </div>
        <div style="overflow-x:auto;">
            <table class="table">
                <thead><tr><th>序号</th><th>学号</th><th>姓名</th><th>距离</th><th>置信度</th></tr></thead>
                <tbody>${rows || '<tr><td colspan="5" class="text-muted">暂无检测结果</td></tr>'}</tbody>
            </table>
        </div>`;
}

async function loadActivities() {
    const res = await apiFetch('/api/group-photo/activities');
    if (!res) return;

    const data = await res.json();
    const tbody = document.getElementById('activities-tbody');
    if (!tbody) return;

    tbody.innerHTML = (data.activities || []).map((a) => `
        <tr>
            <td>${a.id}</td>
            <td>${a.name}</td>
            <td>${a.participant_count}</td>
            <td>${a.created_at ? a.created_at.slice(0, 19).replace('T', ' ') : ''}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="viewParticipants(${a.id})">名单</button>
                <button class="btn btn-sm btn-outline-primary" onclick="exportActivity(${a.id})">导出</button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteActivity(${a.id})">删除</button>
            </td>
        </tr>`).join('');
}

async function viewParticipants(activityId) {
    const res = await apiFetch(`/api/group-photo/activities/${activityId}/participants`);
    if (!res) return;

    const data = await res.json();
    const title = document.getElementById('participants-title');
    if (title) title.textContent = `活动 ID ${activityId} 参与名单`;

    const tbody = document.getElementById('participants-tbody');
    if (!tbody) return;

    const rows = data.participants || [];
    tbody.innerHTML = rows.length
        ? rows.map((p, i) => `<tr><td>${i + 1}</td><td>${p.student_id}</td><td>${p.student_name}</td></tr>`).join('')
        : '<tr><td colspan="3" class="text-muted">该活动暂无参与名单</td></tr>';
}

async function loadStats() {
    const res = await apiFetch('/api/group-photo/stats');
    if (!res) return;

    const data = await res.json();
    const labels = (data.chart_data && data.chart_data.labels) || [];
    const values = (data.chart_data && data.chart_data.values) || [];

    const ctx = document.getElementById('stats-chart');
    if (!ctx) return;

    if (window._statsChart) window._statsChart.destroy();

    window._statsChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: '参与次数',
                data: values,
                backgroundColor: '#2563EB'
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
        },
    });
}

function exportActivity(activityId) {
    downloadFile(`/api/group-photo/activities/${activityId}/export`, `activity_${activityId}_participants.xlsx`);
}

function exportStats() {
    downloadFile('/api/group-photo/export/stats', 'activity_stats.xlsx');
}

async function deleteActivity(activityId) {
    if (!confirm(`确定删除活动 ID ${activityId} 吗？该操作不可恢复。`)) return;
    const res = await apiFetch(`/api/group-photo/activities/${activityId}`, { method: 'DELETE' });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) {
        alert(data.error || '删除失败');
        return;
    }
    await loadActivities();
    await loadStats();
    const title = document.getElementById('participants-title');
    if (title) title.textContent = '请先在活动记录中点击“名单”';
    const tbody = document.getElementById('participants-tbody');
    if (tbody) tbody.innerHTML = '';
}

function showLoading(show) {
    const el = document.getElementById('loading');
    if (el) el.classList.toggle('d-none', !show);
}
