const EMOTION_LABELS = {
    angry: '愤怒', disgust: '厌恶', fear: '恐惧',
    happy: '快乐', neutral: '平静', sad: '悲伤', surprise: '惊讶'
};
const EMOTION_COLORS = ['#EF4444','#F97316','#EAB308','#22C55E','#3B82F6','#8B5CF6','#EC4899'];

function isTeacher() {
    try {
        const token = localStorage.getItem('token');
        if (!token) return false;
        const payload = JSON.parse(atob(token.split('.')[1]));
        return payload.role === 'teacher';
    } catch (e) { return false; }
}

document.addEventListener('DOMContentLoaded', () => {
    if (isTeacher()) {
        const toolbar = document.getElementById('emotion-bulk-toolbar');
        if (toolbar) toolbar.style.display = 'flex';
        const thead = document.getElementById('emotion-thead');
        if (thead) {
            thead.innerHTML = '<tr><th style="width:32px;"><input type="checkbox" id="emotion-check-all"></th><th>学号</th><th>姓名</th><th>时间</th><th>情绪</th><th>来源</th><th>操作</th></tr>';
            document.getElementById('emotion-check-all').addEventListener('change', (e) => {
                document.querySelectorAll('.emotion-row-check').forEach(c => c.checked = e.target.checked);
                updateEmotionBulkBar();
            });
        }
    }

    const btnToggle = document.getElementById('btn-toggle-emotion-table');
    btnToggle?.addEventListener('click', () => {
        const wrap = document.getElementById('emotion-table-wrap');
        if (!wrap) return;
        const hidden = wrap.style.display === 'none';
        wrap.style.display = hidden ? '' : 'none';
        btnToggle.textContent = hidden ? '收起明细' : '展开明细';
    });

    loadStats();
    loadRecords();

    document.getElementById('btn-filter').addEventListener('click', () => {
        loadStats();
        loadRecords();
    });

    document.getElementById('btn-export')?.addEventListener('click', () => {
        const params = buildParams();
        downloadFile(`/api/emotion/export${params ? '?' + params : ''}`, '情绪记录.xlsx');
    });
});

function renderSummary(distribution) {
    const summary = document.getElementById('emotion-summary');
    const topEl = document.getElementById('emotion-top');
    if (!summary || !topEl) return;

    const entries = Object.entries(distribution || {});
    const total = entries.reduce((s, [, v]) => s + Number(v || 0), 0);
    const sorted = [...entries].sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0));
    const top3 = sorted.slice(0, 3);

    summary.innerHTML = `
        <div class="card" style="padding:.75rem;"><div class="text-sm text-muted">总记录</div><div class="font-bold" style="font-size:1.25rem;">${total}</div></div>
        <div class="card" style="padding:.75rem;"><div class="text-sm text-muted">情绪类别</div><div class="font-bold" style="font-size:1.25rem;">${entries.filter(([, v]) => Number(v || 0) > 0).length}</div></div>
    `;

    if (!top3.length || total === 0) {
        topEl.textContent = '暂无情绪分布数据';
        return;
    }
    topEl.textContent = `Top 情绪：${top3.map(([k, v]) => `${EMOTION_LABELS[k] || k} ${v}次`).join(' / ')}`;
}

async function loadStats() {
    const params = buildParams();
    const res = await apiFetch(`/api/emotion/stats?${params}`);
    if (!res) return;
    const data = await res.json();

    const labels = Object.keys(data.distribution || {}).map(k => EMOTION_LABELS[k] || k);
    const values = Object.values(data.distribution || {});

    renderSummary(data.distribution || {});

    const ctx = document.getElementById('emotion-pie');
    if (ctx) {
        if (window._pieChart) window._pieChart.destroy();
        window._pieChart = new Chart(ctx, {
            type: 'pie',
            data: { labels, datasets: [{ data: values, backgroundColor: EMOTION_COLORS }] },
            options: { responsive: true },
        });
    }

}

async function loadRecords() {
    const params = buildParams();
    const res = await apiFetch(`/api/emotion/records?${params}`);
    if (!res) return;
    const data = await res.json();
    const tbody = document.getElementById('emotion-tbody');
    if (!tbody) return;
    const teacher = isTeacher();

    tbody.innerHTML = (data.records || []).map(r => {
        const label = EMOTION_LABELS[r.emotion] || r.emotion;
        const src = r.source === 'attendance' ? '考勤' : '合照';
        const time = r.recorded_at ? r.recorded_at.slice(0, 19).replace('T', ' ') : '';
        if (teacher) {
            return `<tr>
                <td><input type="checkbox" class="emotion-row-check" data-id="${r.id}"></td>
                <td>${r.student_id}</td>
                <td>${r.student_name}</td>
                <td>${time}</td>
                <td><span class="badge bg-primary">${label}</span></td>
                <td>${src}</td>
                <td><button class="btn btn-sm btn-outline-danger" onclick="deleteEmotionOne(${r.id})">删除</button></td>
            </tr>`;
        }
        return `<tr>
            <td>${r.student_id}</td>
            <td>${r.student_name}</td>
            <td>${time}</td>
            <td><span class="badge bg-primary">${label}</span></td>
            <td>${src}</td>
        </tr>`;
    }).join('');

    if (teacher) {
        document.querySelectorAll('.emotion-row-check').forEach(cb => cb.addEventListener('change', updateEmotionBulkBar));
        const all = document.getElementById('emotion-check-all'); if (all) all.checked = false;
        updateEmotionBulkBar();
    }
}

function buildParams() {
    const p = new URLSearchParams();
    const sid = document.getElementById('filter-student')?.value;
    const df = document.getElementById('filter-date-from')?.value;
    const dt = document.getElementById('filter-date-to')?.value;
    const src = document.getElementById('filter-source')?.value;
    if (sid) p.set('student_id', sid);
    if (df) p.set('date_from', df);
    if (dt) p.set('date_to', dt);
    if (src) p.set('source', src);
    return p.toString();
}

function updateEmotionBulkBar() {
    const n = document.querySelectorAll('.emotion-row-check:checked').length;
    const lbl = document.getElementById('emotion-bulk-selected');
    if (lbl) lbl.textContent = `已选 ${n} 条`;
    const btn = document.getElementById('btn-emotion-bulk-delete');
    if (btn) btn.disabled = n === 0;
}

async function deleteEmotionOne(id) {
    if (!confirm('确定删除这条情绪记录吗？')) return;
    const res = await apiFetch(`/api/emotion/records/${id}`, { method: 'DELETE' });
    if (!res) return;
    if (res.ok) { loadRecords(); loadStats(); }
    else { const d = await res.json(); alert(d.error || '删除失败'); }
}

async function bulkDeleteEmotion() {
    const ids = Array.from(document.querySelectorAll('.emotion-row-check:checked')).map(c => parseInt(c.dataset.id, 10));
    if (!ids.length) return;
    if (!confirm(`确定删除选中的 ${ids.length} 条情绪记录？`)) return;
    const res = await apiFetch('/api/emotion/records/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids }),
    });
    if (!res) return;
    const d = await res.json();
    if (res.ok) { alert(`已删除 ${d.deleted || 0} 条`); loadRecords(); loadStats(); }
    else { alert(d.error || '删除失败'); }
}
