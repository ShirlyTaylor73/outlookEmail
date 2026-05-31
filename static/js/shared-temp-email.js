const token = document.body.dataset.shareToken;

const sharedState = {
    selectedMessageId: null,
    loading: false,
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function formatSharedDate(value) {
    if (!value) {
        return '未知时间';
    }

    let date;
    if (typeof value === 'number' || /^\d+$/.test(String(value))) {
        const numeric = Number(value);
        date = new Date(numeric > 100000000000 ? numeric : numeric * 1000);
    } else {
        date = new Date(value);
    }

    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    return new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    }).format(date);
}

function setSharedStatus(message, type = 'info') {
    const statusEl = document.getElementById('sharedStatus');
    if (!statusEl) {
        return;
    }
    statusEl.textContent = message || '';
    statusEl.className = `shared-status ${message ? 'is-visible' : ''} is-${type}`;
}

async function fetchSharedJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
        throw new Error(data.error || `请求失败 (${response.status})`);
    }
    return data;
}

async function loadSharedTempEmail() {
    try {
        const data = await fetchSharedJson(`/api/shared/${encodeURIComponent(token)}`);
        const email = data.email || {};
        const addressEl = document.getElementById('sharedEmailAddress');
        const expiresEl = document.getElementById('sharedExpiresAt');

        if (addressEl) {
            addressEl.textContent = email.email || '临时邮箱';
        }
        if (expiresEl) {
            const provider = email.provider_label || email.provider || '临时邮箱';
            const expires = email.expires_at ? `有效期至 ${formatSharedDate(email.expires_at)}` : '永久有效';
            expiresEl.textContent = `${provider} · ${expires}`;
        }
        setSharedStatus('');
        return data;
    } catch (error) {
        renderSharedError(error.message || '分享链接不可用');
        throw error;
    }
}

async function loadSharedMessages() {
    try {
        const data = await fetchSharedJson(`/api/shared/${encodeURIComponent(token)}/messages`);
        renderSharedMessageList(data.emails || []);
        return data;
    } catch (error) {
        renderSharedError(error.message || '无法加载邮件列表');
        throw error;
    }
}

async function refreshSharedMessages() {
    const button = document.getElementById('sharedRefreshBtn');
    if (sharedState.loading) {
        return;
    }

    sharedState.loading = true;
    if (button) {
        button.disabled = true;
        button.textContent = '刷新中...';
    }
    setSharedStatus('正在刷新邮件...', 'info');

    try {
        const data = await fetchSharedJson(`/api/shared/${encodeURIComponent(token)}/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        renderSharedMessageList(data.emails || []);
        setSharedStatus(
            data.throttled ? '刷新过于频繁，已显示缓存邮件' : `刷新完成，共 ${data.count || 0} 封邮件`,
            data.throttled ? 'warning' : 'success'
        );
    } catch (error) {
        renderSharedError(error.message || '刷新失败');
    } finally {
        sharedState.loading = false;
        if (button) {
            button.disabled = false;
            button.textContent = '刷新邮件';
        }
    }
}

function renderSharedMessageList(emails) {
    const listEl = document.getElementById('sharedEmailList');
    const countEl = document.getElementById('sharedEmailCount');
    if (countEl) {
        countEl.textContent = String(emails.length);
    }
    if (!listEl) {
        return;
    }

    if (!emails.length) {
        listEl.innerHTML = '<div class="shared-empty">暂无邮件</div>';
        return;
    }

    listEl.innerHTML = emails.map((email) => {
        const id = escapeHtml(email.id || '');
        const selected = email.id === sharedState.selectedMessageId ? ' is-selected' : '';
        const htmlBadge = email.has_html ? '<span class="message-badge">HTML</span>' : '';
        return `
            <button class="message-item${selected}" type="button" data-message-id="${id}">
                <span class="message-row">
                    <span class="message-from">${escapeHtml(email.from || '未知发件人')}</span>
                    ${htmlBadge}
                </span>
                <span class="message-subject">${escapeHtml(email.subject || '无主题')}</span>
                <span class="message-preview">${escapeHtml(email.body_preview || '')}</span>
                <span class="message-date">${escapeHtml(formatSharedDate(email.timestamp || email.date))}</span>
            </button>
        `;
    }).join('');

    listEl.querySelectorAll('[data-message-id]').forEach((item) => {
        item.addEventListener('click', () => loadSharedMessageDetail(item.dataset.messageId));
    });
}

async function loadSharedMessageDetail(messageId) {
    if (!messageId) {
        return;
    }
    sharedState.selectedMessageId = messageId;
    document.querySelectorAll('.message-item').forEach((item) => {
        item.classList.toggle('is-selected', item.dataset.messageId === messageId);
    });

    const detailEl = document.getElementById('sharedEmailDetail');
    if (detailEl) {
        detailEl.innerHTML = '<div class="shared-empty">正在加载邮件详情...</div>';
    }

    try {
        const data = await fetchSharedJson(
            `/api/shared/${encodeURIComponent(token)}/messages/${encodeURIComponent(messageId)}`
        );
        renderSharedMessageDetail(data.email || {});
    } catch (error) {
        renderSharedError(error.message || '无法加载邮件详情');
    }
}

function renderSharedMessageDetail(email) {
    const detailEl = document.getElementById('sharedEmailDetail');
    if (!detailEl) {
        return;
    }

    detailEl.innerHTML = `
        <header class="detail-header">
            <h2>${escapeHtml(email.subject || '无主题')}</h2>
            <dl class="detail-meta">
                <div><dt>发件人</dt><dd>${escapeHtml(email.from || '未知')}</dd></div>
                <div><dt>收件人</dt><dd>${escapeHtml(email.to || '')}</dd></div>
                <div><dt>时间</dt><dd>${escapeHtml(formatSharedDate(email.timestamp || email.date))}</dd></div>
            </dl>
        </header>
        <div class="detail-body" id="sharedMessageBody"></div>
    `;

    const bodyEl = document.getElementById('sharedMessageBody');
    if (email.body_type === 'html') {
        bodyEl.innerHTML = DOMPurify.sanitize(email.body || '');
    } else {
        bodyEl.textContent = email.body || '';
    }
}

function renderSharedError(message) {
    setSharedStatus(message || '分享链接不可用', 'error');
    const listEl = document.getElementById('sharedEmailList');
    const detailEl = document.getElementById('sharedEmailDetail');
    const button = document.getElementById('sharedRefreshBtn');

    if (listEl) {
        listEl.innerHTML = '<div class="shared-empty">无法显示邮件列表</div>';
    }
    if (detailEl) {
        detailEl.innerHTML = `<div class="shared-error">${escapeHtml(message || '分享链接不可用')}</div>`;
    }
    if (button) {
        button.disabled = true;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const button = document.getElementById('sharedRefreshBtn');
    if (button) {
        button.addEventListener('click', refreshSharedMessages);
    }

    try {
        await loadSharedTempEmail();
        await loadSharedMessages();
    } catch (error) {
        // Individual loaders have already rendered the public error state.
    }
});
