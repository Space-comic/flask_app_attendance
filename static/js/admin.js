document.addEventListener('DOMContentLoaded', () => {
    // 进入页面先强制清空搜索框，避免浏览器自动填充残留关键词（如 admin）
    // 导致翻页按钮因"仅 1 条匹配"而不显示
    const searchInput = document.getElementById('search-input');
    if (searchInput) searchInput.value = '';

    loadUsers();

    document.getElementById('btn-search')?.addEventListener('click', () => loadUsers(1));
    document.getElementById('search-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') loadUsers(1);
    });
    document.getElementById('btn-add')?.addEventListener('click', () => openModal());
    document.getElementById('btn-save-user')?.addEventListener('click', saveUser);
    document.getElementById('btn-open-batch')?.addEventListener('click', openBatchModal);
    document.getElementById('btn-do-batch')?.addEventListener('click', batchImport);

    document.querySelectorAll('#role-tabs .tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#role-tabs .tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentRole = btn.dataset.role || '';
            // 切换标签时自动清空搜索框，避免残留关键词导致"查不到人"
            const input = document.getElementById('search-input');
            if (input) input.value = '';
            loadUsers(1);
        });
    });

    document.getElementById('btn-clear-search')?.addEventListener('click', () => {
        const input = document.getElementById('search-input');
        if (input) input.value = '';
        loadUsers(1);
    });

    document.getElementById('check-all')?.addEventListener('change', (e) => {
        document.querySelectorAll('.row-check').forEach(c => c.checked = e.target.checked);
        updateBulkBar();
    });
    document.getElementById('btn-bulk-delete')?.addEventListener('click', bulkDelete);
    document.getElementById('btn-bulk-update')?.addEventListener('click', openBulkEditModal);
    document.getElementById('btn-do-bulk-edit')?.addEventListener('click', doBulkEdit);

    document.getElementById('field-face-file')?.addEventListener('change', (e) => {
        const f = e.target.files?.[0];
        if (!f) return;
        const reader = new FileReader();
        reader.onload = () => {
            const wrap = document.getElementById('face-preview-wrap');
            const img = document.getElementById('face-preview');
            const hint = document.getElementById('face-preview-hint');
            if (img) img.src = reader.result;
            if (hint) hint.textContent = '即将上传的人脸（保存后生效）';
            if (wrap) wrap.style.display = 'block';
        };
        reader.readAsDataURL(f);
    });
});

let editingId = null;
let currentRole = '';
let currentPage = 1;
const PAGE_SIZE = 20;

/**
 * 执行增删改类操作后，把搜索词 + 角色 tab 统一重置回"全部"，
 * 避免用户看到"全是 0 条"的错觉。
 */
function resetFiltersAndReload() {
    const input = document.getElementById('search-input');
    if (input) input.value = '';
    currentRole = '';
    document.querySelectorAll('#role-tabs .tab-btn').forEach(b => {
        b.classList.toggle('active', (b.dataset.role || '') === '');
    });
    loadUsers(1);
}

async function loadUsers(page = 1) {
    currentPage = page;
    const search = document.getElementById('search-input')?.value || '';
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    if (currentRole) params.set('role', currentRole);
    params.set('page', page);
    params.set('per_page', PAGE_SIZE);

    const res = await apiFetch(`/api/users?${params.toString()}`);
    if (!res) return;

    const data = await res.json();
    const tbody = document.getElementById('users-tbody');
    if (!tbody) return;

    tbody.innerHTML = (data.users || []).map(u => `
        <tr>
            <td><input type="checkbox" class="row-check" data-id="${u.id}"></td>
            <td>${u.id}</td>
            <td>${u.name}</td>
            <td>${u.class_name || ''}</td>
            <td><span class="badge ${u.role === 'teacher' ? 'bg-warning' : 'bg-primary'}">${u.role === 'teacher' ? '教师' : '学生'}</span></td>
            <td>${u.gender || ''}</td>
            <td>${u.age || ''}</td>
            <td>${u.address || ''}</td>
            <td>${u.ethnicity || ''}</td>
            <td>${u.face_image ? '<span class="badge bg-success">已录入</span>' : '<span class="badge bg-secondary">未录入</span>'}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="openModal('${u.id}')">编辑</button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteUser('${u.id}')">删除</button>
            </td>
        </tr>
    `).join('');

    // 行复选框：变更时刷新批量栏
    tbody.querySelectorAll('.row-check').forEach(cb => {
        cb.addEventListener('change', updateBulkBar);
    });
    const all = document.getElementById('check-all');
    if (all) all.checked = false;
    updateBulkBar();

    const counter = document.getElementById('user-count');
    if (counter) counter.textContent = `共 ${data.total || 0} 条 · 第 ${data.page || 1} / ${data.pages || 1} 页`;

    renderUsersPagination(data.pages || 1, data.page || 1);
}

function renderUsersPagination(pages, current) {
    const el = document.getElementById('users-pagination');
    if (!el) return;
    if (!pages || pages <= 1) { el.innerHTML = ''; return; }

    const mk = (label, target, disabled, active) =>
        `<button class="btn btn-sm ${active ? 'btn-primary' : 'btn-outline-primary'}" ${disabled ? 'disabled' : ''} onclick="loadUsers(${target})">${label}</button>`;

    let html = '';
    html += mk('上一页', Math.max(1, current - 1), current <= 1, false);

    const window_ = 2;
    const start = Math.max(1, current - window_);
    const end   = Math.min(pages, current + window_);
    if (start > 1) {
        html += mk(1, 1, false, current === 1);
        if (start > 2) html += `<span class="text-muted text-sm" style="align-self:center;">…</span>`;
    }
    for (let i = start; i <= end; i++) html += mk(i, i, false, i === current);
    if (end < pages) {
        if (end < pages - 1) html += `<span class="text-muted text-sm" style="align-self:center;">…</span>`;
        html += mk(pages, pages, false, current === pages);
    }
    html += mk('下一页', Math.min(pages, current + 1), current >= pages, false);
    el.innerHTML = html;
}

function openModal(uid = null) {
    editingId = uid;

    const form = document.getElementById('user-form');
    form?.reset();

    document.getElementById('modal-title').textContent = uid ? '编辑用户' : '新增用户';
    document.getElementById('field-id').disabled = !!uid;

    const wrap = document.getElementById('face-preview-wrap');
    const img = document.getElementById('face-preview');
    const hint = document.getElementById('face-preview-hint');
    if (wrap) wrap.style.display = 'none';
    if (img) img.src = '';
    if (hint) hint.textContent = '当前已录入的人脸';

    if (uid) {
        apiFetch(`/api/users/${uid}`).then(r => r?.json()).then(u => {
            if (!u) return;
            document.getElementById('field-id').value = u.id;
            document.getElementById('field-name').value = u.name;
            document.getElementById('field-role').value = u.role;
            document.getElementById('field-gender').value = u.gender || '';
            document.getElementById('field-age').value = u.age || '';
            document.getElementById('field-address').value = u.address || '';
            document.getElementById('field-ethnicity').value = u.ethnicity || '';
            const cn = document.getElementById('field-class');
            if (cn) cn.value = u.class_name || '';

            if (u.face_image && img && wrap) {
                img.src = `/face-images/${encodeURIComponent(u.face_image)}?t=${Date.now()}`;
                wrap.style.display = 'block';
            }
        });
    }

    document.getElementById('user-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('user-modal').classList.remove('active');
}

async function saveUser() {
    const payload = {
        id: document.getElementById('field-id').value.trim(),
        name: document.getElementById('field-name').value.trim(),
        password: document.getElementById('field-password').value,
        role: document.getElementById('field-role').value,
        gender: document.getElementById('field-gender').value,
        age: parseInt(document.getElementById('field-age').value, 10) || null,
        address: document.getElementById('field-address').value,
        ethnicity: document.getElementById('field-ethnicity').value,
        class_name: document.getElementById('field-class')?.value?.trim() || null,
    };

    const url = editingId ? `/api/users/${editingId}` : '/api/users';
    const method = editingId ? 'PUT' : 'POST';

    const res = await apiFetch(url, { method, body: JSON.stringify(payload) });
    if (!res) return;

    const data = await res.json();
    if (!res.ok) {
        alert(data.error || '保存失败');
        return;
    }

    const faceFile = document.getElementById('field-face-file')?.files?.[0];
    if (faceFile) {
        const uid = editingId || data.user.id;
        const faceOk = await uploadFace(uid, faceFile);
        if (!faceOk) alert('用户保存成功，但人脸上传失败，请重试。');
    }

    closeModal();
    resetFiltersAndReload();
}

async function uploadFace(uid, file) {
    try {
        // 先在浏览器侧压到 ≤1024，显著减少上传体积和后端检测耗时
        let b64;
        try {
            b64 = await compressImage(file, 1024, 0.88);
        } catch (_) {
            b64 = await fileToBase64(file);
        }
        const res = await apiFetch(`/api/users/${uid}/face`, {
            method: 'POST',
            body: JSON.stringify({ image: b64 })
        });
        if (!res) return false;
        const data = await res.json();
        if (!res.ok) {
            alert(data.error || '人脸上传失败');
            return false;
        }
        return true;
    } catch (e) {
        alert('人脸文件读取失败');
        return false;
    }
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = reader.result || '';
            const b64 = String(dataUrl).split(',')[1];
            if (!b64) reject(new Error('invalid image'));
            else resolve(b64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

/**
 * 将图片压缩到长边 ≤ 1024 px 再返回 base64。
 * 注册单人正脸图 1024 px 已经足够清晰，这样能显著降低上传体积与
 * 服务端 RetinaFace 推理时间。
 */
function compressImage(file, maxSide = 1024, quality = 0.88) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = reject;
        reader.onload = () => {
            const img = new Image();
            img.onerror = reject;
            img.onload = () => {
                const longSide = Math.max(img.width, img.height);
                let w = img.width, h = img.height;
                if (longSide > maxSide) {
                    const scale = maxSide / longSide;
                    w = Math.round(img.width * scale);
                    h = Math.round(img.height * scale);
                }
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                const ctx = canvas.getContext('2d');
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(img, 0, 0, w, h);
                const b64 = canvas.toDataURL('image/jpeg', quality).split(',')[1];
                if (!b64) reject(new Error('compress failed'));
                else resolve(b64);
            };
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}

async function deleteUser(uid) {
    if (!confirm(`确认删除用户 ${uid}？`)) return;
    const res = await apiFetch(`/api/users/${uid}`, { method: 'DELETE' });
    if (res?.ok) resetFiltersAndReload();
}

function openBatchModal() {
    const m = document.getElementById('batch-modal');
    const r = document.getElementById('batch-result');
    if (r) { r.style.display = 'none'; r.innerHTML = ''; }
    const zipInput = document.getElementById('batch-file');
    const photosInput = document.getElementById('batch-photos');
    if (zipInput) zipInput.value = '';
    if (photosInput) photosInput.value = '';
    const cnt = document.getElementById('batch-photos-count');
    if (cnt) cnt.textContent = '';
    m?.classList.add('active');
}

function closeBatchModal() {
    document.getElementById('batch-modal')?.classList.remove('active');
}

document.addEventListener('change', (e) => {
    if (e.target?.id === 'batch-photos') {
        const cnt = document.getElementById('batch-photos-count');
        const n = e.target.files?.length || 0;
        if (cnt) cnt.textContent = n ? `已选择 ${n} 张图片` : '';
    }
});

async function batchImport() {
    const zipFile = document.getElementById('batch-file')?.files?.[0];
    const photoFiles = Array.from(document.getElementById('batch-photos')?.files || []);
    if (!zipFile && photoFiles.length === 0) {
        alert('请选择 ZIP 文件或一组照片');
        return;
    }

    const defaultClass = document.getElementById('batch-default-class')?.value || '';
    const defaultPwd = document.getElementById('batch-default-password')?.value || '123456';

    const btn = document.getElementById('btn-do-batch');
    if (btn) { btn.disabled = true; btn.textContent = '导入中...'; }
    const resultEl = document.getElementById('batch-result');
    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<div class="alert alert-info">正在导入，请稍候...</div>';
    }

    const formData = new FormData();
    if (zipFile) formData.append('zip_file', zipFile);
    for (const p of photoFiles) formData.append('photos', p);
    formData.append('default_class', defaultClass);
    formData.append('default_password', defaultPwd);

    try {
        const res = await fetch('/api/users/batch-import', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getToken()}` },
            body: formData,
        });
        const data = await res.json();
        if (!res.ok) {
            if (resultEl) {
                resultEl.innerHTML = `<div class="alert alert-warning">${data.error || '导入失败'}</div>`;
            }
            return;
        }

        const errs = (data.errors || []).slice(0, 20);
        const errHtml = errs.length
            ? `<div class="mt-1" style="max-height:160px;overflow:auto;font-size:12px;color:#b45309;">${errs.map(e => `<div>• ${e}</div>`).join('')}</div>`
            : '';
        if (resultEl) {
            resultEl.innerHTML = `
                <div class="alert alert-success">
                    新增 <strong>${data.imported || 0}</strong> 人，
                    更新 <strong>${data.updated || 0}</strong> 人，
                    失败 <strong>${data.failed || 0}</strong> 条。
                </div>
                ${errHtml}`;
        }
        resetFiltersAndReload();
    } catch (e) {
        if (resultEl) {
            resultEl.innerHTML = '<div class="alert alert-warning">导入请求失败，请重试</div>';
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '开始导入'; }
    }
}

window.openModal = openModal;
window.closeModal = closeModal;
window.closeBatchModal = closeBatchModal;
window.closeBulkEditModal = closeBulkEditModal;
window.loadUsers = loadUsers;
window.deleteUser = deleteUser;

function _selectedIds() {
    return Array.from(document.querySelectorAll('.row-check:checked')).map(c => c.dataset.id);
}

function updateBulkBar() {
    const ids = _selectedIds();
    const n = ids.length;
    const label = document.getElementById('bulk-selected');
    if (label) label.textContent = `已选 ${n} 项`;
    document.getElementById('btn-bulk-delete').disabled = n === 0;
    document.getElementById('btn-bulk-update').disabled = n === 0;

    // 同步全选 checkbox 状态（半选/全选）
    const all = document.getElementById('check-all');
    const total = document.querySelectorAll('.row-check').length;
    if (all) {
        all.checked = total > 0 && n === total;
        all.indeterminate = n > 0 && n < total;
    }
}

async function bulkDelete() {
    const ids = _selectedIds();
    if (!ids.length) return;
    if (!confirm(`确定删除选中的 ${ids.length} 个用户？此操作会同时移除其考勤、情绪记录与人脸图，不可恢复。`)) return;

    const res = await apiFetch('/api/users/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids }),
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) {
        alert(data.error || '批量删除失败');
        return;
    }
    let msg = `已删除 ${data.deleted || 0} 个用户`;
    if (data.failed) msg += `，失败 ${data.failed} 条`;
    alert(msg);
    resetFiltersAndReload();
}

function openBulkEditModal() {
    const ids = _selectedIds();
    if (!ids.length) return;
    document.getElementById('bulk-edit-count').textContent = ids.length;
    ['bulk-class','bulk-address','bulk-ethnicity','bulk-password'].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = '';
    });
    ['bulk-role','bulk-gender'].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = '';
    });
    document.getElementById('bulk-edit-modal').classList.add('active');
}

function closeBulkEditModal() {
    document.getElementById('bulk-edit-modal')?.classList.remove('active');
}

async function doBulkEdit() {
    const ids = _selectedIds();
    if (!ids.length) { closeBulkEditModal(); return; }

    const updates = {
        class_name: document.getElementById('bulk-class').value.trim(),
        role:       document.getElementById('bulk-role').value,
        gender:     document.getElementById('bulk-gender').value,
        address:    document.getElementById('bulk-address').value.trim(),
        ethnicity:  document.getElementById('bulk-ethnicity').value.trim(),
        password:   document.getElementById('bulk-password').value,
    };
    // 过滤空字段
    Object.keys(updates).forEach(k => { if (!updates[k]) delete updates[k]; });

    if (!Object.keys(updates).length) {
        alert('请至少填写一项要修改的字段');
        return;
    }

    const res = await apiFetch('/api/users/batch-update', {
        method: 'POST',
        body: JSON.stringify({ ids, updates }),
    });
    if (!res) return;
    const data = await res.json();
    if (!res.ok) {
        alert(data.error || '批量更新失败');
        return;
    }
    alert(`已更新 ${data.updated} 个用户`);
    closeBulkEditModal();
    resetFiltersAndReload();
}
