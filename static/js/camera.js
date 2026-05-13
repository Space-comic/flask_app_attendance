class CameraManager {
    constructor(videoEl, canvasEl) {
        this.video = videoEl;
        this.canvas = canvasEl;
        this.ctx = canvasEl.getContext('2d');
        this.stream = null;
        this.startPromise = null;
    }

    static explainError(error) {
        const name = error && error.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') {
            return '摄像头权限被拒绝，请检查浏览器权限设置。';
        }
        if (name === 'NotFoundError' || name === 'OverconstrainedError') {
            return '未找到可用摄像头，请检查设备连接。';
        }
        if (name === 'NotReadableError' || name === 'AbortError') {
            return '摄像头当前被其他程序占用，请关闭占用后重试。';
        }
        return '摄像头启动失败，请稍后重试。';
    }

    async start() {
        if (this.stream) {
            return this.stream;
        }
        if (this.startPromise) {
            return this.startPromise;
        }

        this.startPromise = this._startInternal();
        try {
            return await this.startPromise;
        } finally {
            this.startPromise = null;
        }
    }

    async _startInternal() {
        this.stop();

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('camera_unsupported');
        }

        const attempts = [
            {
                video: {
                    facingMode: { ideal: 'user' },
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                },
            },
            {
                video: {
                    width: { ideal: 640 },
                    height: { ideal: 480 },
                },
            },
            { video: true },
        ];

        let lastError = null;
        for (const constraints of attempts) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia(constraints);
                await this._attachStream(stream);
                return stream;
            } catch (error) {
                lastError = error;
                if (!this._shouldRetry(error)) {
                    break;
                }
            }
        }

        throw lastError || new Error('camera_start_failed');
    }

    _shouldRetry(error) {
        const retryable = ['OverconstrainedError', 'NotFoundError', 'AbortError', 'NotReadableError'];
        return retryable.includes(error && error.name);
    }

    async _attachStream(stream) {
        this.stream = stream;
        this.video.muted = true;
        this.video.playsInline = true;
        this.video.srcObject = stream;

        if (this.video.readyState < 1) {
            await new Promise((resolve, reject) => {
                const onLoaded = () => {
                    cleanup();
                    resolve();
                };
                const onError = () => {
                    cleanup();
                    reject(new Error('video_metadata_failed'));
                };
                const cleanup = () => {
                    this.video.removeEventListener('loadedmetadata', onLoaded);
                    this.video.removeEventListener('error', onError);
                };
                this.video.addEventListener('loadedmetadata', onLoaded);
                this.video.addEventListener('error', onError);
            });
        }

        try {
            await this.video.play();
        } catch (_) {
            // 某些浏览器在自动播放策略下会抛错，但有了 srcObject 后通常仍可继续取帧。
        }
    }

    captureFrame(quality = 0.8) {
        if (!this.stream || this.video.readyState < 2) {
            throw new Error('camera_not_ready');
        }

        this.canvas.width = this.video.videoWidth || 640;
        this.canvas.height = this.video.videoHeight || 480;
        this.ctx.drawImage(this.video, 0, 0);
        return this.canvas.toDataURL('image/jpeg', quality).split(',')[1];
    }

    stop() {
        if (this.stream) {
            this.stream.getTracks().forEach((track) => track.stop());
            this.stream = null;
        }
        if (this.video) {
            this.video.pause();
            this.video.srcObject = null;
        }
    }
}

function getToken() {
    return localStorage.getItem('token');
}

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`,
    };
}

async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
        headers: authHeaders(),
        ...options,
    });
    if (res.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/';
        return null;
    }
    return res;
}

async function downloadFile(url, fallbackName) {
    const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (res.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/';
        return;
    }
    if (!res.ok) {
        let msg = `导出失败 (HTTP ${res.status})`;
        try {
            const data = await res.json();
            if (data && (data.error || data.msg)) {
                msg = data.error || data.msg;
            }
        } catch (_) {}
        alert(msg);
        return;
    }

    let filename = fallbackName;
    const disp = res.headers.get('Content-Disposition') || '';
    const match = disp.match(/filename\*=UTF-8''([^;]+)/i) || disp.match(/filename="?([^"]+)"?/i);
    if (match) {
        try {
            filename = decodeURIComponent(match[1]);
        } catch (_) {
            filename = match[1];
        }
    }

    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
}
