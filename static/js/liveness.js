class LivenessChecker {
    constructor(camera, statusEl, progressEl, onPassed, onFailed = null) {
        this.camera = camera;
        this.statusEl = statusEl;
        this.progressEl = progressEl;
        this.onPassed = onPassed;
        this.onFailed = onFailed;
        this.sessionToken = null;
        this.required = 1;
        this.timer = null;
        this.running = false;
        this.inflight = false;
        this.intervalMs = 140;
        this.currentStep = 'mouth';
        this.timeoutMs = 15000;
        this.startedAt = 0;
    }

    async start() {
        try {
            const res = await fetch('/api/attendance/liveness/start', { method: 'POST' });
            const data = await res.json();
            if (!res.ok) {
                this._setStatus(data.error || '活体会话启动失败', 'warning');
                return false;
            }

            this.sessionToken = data.session_token;
            this.required = data.required_mouths || data.required_blinks || 1;
            this.currentStep = data.current_step || 'mouth';
            this.running = true;
            this.startedAt = Date.now();
            this._updateProgress(0);
            this._setStatus(data.instruction || `请完成 ${this.required} 次张嘴动作`, 'info');
            this.timer = setInterval(() => this._tick(), this.intervalMs);
            return true;
        } catch (e) {
            this._setStatus('网络异常，无法启动活体检测', 'warning');
            return false;
        }
    }

    async _tick() {
        if (!this.running || this.inflight) return;
        if (this.startedAt && (Date.now() - this.startedAt) >= this.timeoutMs) {
            this.stop();
            this._setStatus('活体检测超时（15秒），请重新检测', 'warning');
            if (this.onFailed) this.onFailed({ status: 'timeout', error: 'liveness_timeout' });
            return;
        }
        this.inflight = true;
        try {
            const b64 = this.camera.captureFrame(0.65);
            const res = await fetch('/api/attendance/liveness/check-frame', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_token: this.sessionToken, image: b64 }),
            });
            const data = await res.json();

            if (!res.ok) {
                this.stop();
                const errMsg = data.status === 'fake'
                    ? (data.error || data.message || 'fake: 检测到疑似视频攻击，请重新开始活体验证')
                    : (data.error || '活体检测失败，请重试');
                this._setStatus(errMsg, 'warning');
                if (this.onFailed) this.onFailed(data);
                return;
            }

            const progress = data.mouth_count ?? data.action_count ?? data.blink_count ?? 0;
            this.required = data.required_mouths || data.required_blinks || this.required;
            this.currentStep = data.current_step || this.currentStep;
            this._updateProgress(progress);

            if (data.passed) {
                this.stop();
                this._setStatus(data.instruction || '活体验证通过', 'success');
                this.onPassed(this.sessionToken, data);
                return;
            }

            if (!data.face_found) {
                this._setStatus(data.instruction || '未检测到人脸，请正对摄像头', 'warning');
                return;
            }

            if ((data.current_step || '').startsWith('move')) {
                this._setStatus(data.instruction || '请按提示移动头部完成防重放验证', 'warning');
                return;
            }

            if (data.mouth_open_detected) {
                this._setStatus(data.instruction || '检测到张嘴，请继续下一步', 'info');
                return;
            }

            this._setStatus(data.instruction || `请完成 ${this.required} 次张嘴动作`, 'info');
        } catch (e) {
            this.stop();
            this._setStatus('检测中断，请重新开始活体验证', 'warning');
            if (this.onFailed) this.onFailed({ error: '检测中断，请重新开始活体验证' });
        } finally {
            this.inflight = false;
        }
    }

    stop() {
        this.running = false;
        this.startedAt = 0;
        if (this.timer) {
            clearInterval(this.timer);
            this.timer = null;
        }
    }

    _updateProgress(done) {
        if (!this.progressEl) return;
        this.progressEl.textContent = `${done} / ${this.required}`;
    }

    _setStatus(msg, type) {
        if (!this.statusEl) return;
        this.statusEl.textContent = msg;
        this.statusEl.className = `liveness-status status-${type}`;
    }
}
