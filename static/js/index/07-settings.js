        /* global accountsCache, allTags, closeAllModals, currentGroupId, currentGroupName, deleteCurrentAccount, ensureForwardingSettingsUI, escapeHtml, formatAbsoluteDateTime, getSelectedForwardChannels, groups, handleApiError, hideEditAccountModal, hideModal, hideSettingsModal, invalidateNormalMailRetentionCaches, isTempEmailGroup, isTempImportGroup, loadAccountsByGroup, loadGroups, loadTempEmails, normalizeSmtpForwardProvider, refreshVisibleAccountList, setAppTimeZone, setModalVisible, setSelectedForwardChannels, setShowAccountCreatedAt, setShowAccountSortOrder, setShowGroupId, setNormalMailLocalRetentionEnabled, showConfirmModal, showModal, showToast, syncSmtpProviderUI, toggleRefreshStrategy, updateEditAccountFields, updateGroupSelects, updateImportHint */

        // ==================== 设置相关 ====================
        let settingsScrollSyncBound = false;
        let settingsScrollSyncFrame = 0;
        let lastLoadedWebdavBackupSettings = null;
        let icloudHmeSourcesCache = [];
        let icloudHmeAddressCache = [];
        let icloudHmeAddressPagination = { limit: 50, offset: 0, total: 0 };
        let icloudHmeSelectedAddresses = new Set();
        let icloudHmeLongRunnerStatus = null;
        let icloudHmeDeactivationCandidates = [];
        let icloudHmeGroupsCache = [];
        let icloudHmeLongRunnerPollTimer = null;
        let icloudHmeSettingsEventsBound = false;
        let lastNormalMailRetentionStatus = null;
        let normalMailRetentionStatusPollTimer = null;
        let normalMailRetentionStatusPollDelayMs = 0;
        const NORMAL_MAIL_RETENTION_STATUS_INITIAL_POLL_MS = 2000;
        const NORMAL_MAIL_RETENTION_STATUS_MAX_POLL_MS = 10000;

        function parseSettingsBoolean(value) {
            return String(value).toLowerCase() === 'true';
        }

        function formatStorageBytes(bytes) {
            const value = Number(bytes) || 0;
            if (value < 1024) return `${value} B`;
            if (value < 1024 * 1024) return `${(value / 1024).toFixed(1).replace(/\.0$/, '')} KB`;
            if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1).replace(/\.0$/, '')} MB`;
            return `${(value / 1024 / 1024 / 1024).toFixed(1).replace(/\.0$/, '')} GB`;
        }

        function updateNormalMailRetentionStats(status = {}) {
            lastNormalMailRetentionStatus = status || {};
            const savedCount = Number(status.saved_message_count || 0);
            const cachedBodyCount = Number(status.cached_body_count || 0);
            const clearStatus = status.clear_status || {};

            const savedEl = document.getElementById('normalMailRetentionSavedCount');
            const cachedEl = document.getElementById('normalMailRetentionCachedBodyCount');
            const estimatedEl = document.getElementById('normalMailRetentionEstimatedBytes');
            const dbEl = document.getElementById('normalMailRetentionDbBytes');
            const clearEl = document.getElementById('normalMailRetentionClearStatus');
            const errorEl = document.getElementById('normalMailRetentionStatsError');

            if (savedEl) savedEl.textContent = String(savedCount);
            if (cachedEl) cachedEl.textContent = String(cachedBodyCount);
            if (estimatedEl) estimatedEl.textContent = formatStorageBytes(status.estimated_retained_bytes);
            if (dbEl) dbEl.textContent = formatStorageBytes(status.db_file_bytes);
            if (clearEl) clearEl.textContent = `清理状态：${clearStatus.message || clearStatus.state || '普通邮箱本地缓存清理空闲'}`;
            if (errorEl) {
                errorEl.style.display = 'none';
                errorEl.textContent = '';
            }

            if (clearStatus.state === 'running') {
                scheduleNormalMailRetentionStatusPoll();
            } else {
                stopNormalMailRetentionStatusPoll();
            }
        }

        function renderNormalMailRetentionStatusError(message) {
            const errorEl = document.getElementById('normalMailRetentionStatsError');
            if (!errorEl) return;
            errorEl.textContent = message || '加载普通邮箱本地保留统计失败';
            errorEl.style.display = 'block';
        }

        function stopNormalMailRetentionStatusPoll() {
            if (normalMailRetentionStatusPollTimer) {
                window.clearTimeout(normalMailRetentionStatusPollTimer);
                normalMailRetentionStatusPollTimer = null;
            }
            normalMailRetentionStatusPollDelayMs = 0;
        }

        function resetNormalMailRetentionStatusPollDelay() {
            normalMailRetentionStatusPollDelayMs = NORMAL_MAIL_RETENTION_STATUS_INITIAL_POLL_MS;
        }

        function nextNormalMailRetentionStatusPollDelay() {
            if (!normalMailRetentionStatusPollDelayMs) {
                resetNormalMailRetentionStatusPollDelay();
                return normalMailRetentionStatusPollDelayMs;
            }
            const delay = normalMailRetentionStatusPollDelayMs;
            normalMailRetentionStatusPollDelayMs = Math.min(
                NORMAL_MAIL_RETENTION_STATUS_MAX_POLL_MS,
                normalMailRetentionStatusPollDelayMs * 1.5
            );
            return delay;
        }

        function scheduleNormalMailRetentionStatusPoll() {
            if (normalMailRetentionStatusPollTimer) return;
            normalMailRetentionStatusPollTimer = window.setTimeout(async () => {
                normalMailRetentionStatusPollTimer = null;
                await loadNormalMailRetentionStatus({ silent: true });
            }, nextNormalMailRetentionStatusPollDelay());
        }

        async function loadNormalMailRetentionStatus(options = {}) {
            try {
                const response = await fetch('/api/settings/normal-mail-retention/status', { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载普通邮箱本地保留统计失败');
                }
                updateNormalMailRetentionStats(data.status || {});
                return data.status || {};
            } catch (error) {
                if (!options.silent) {
                    renderNormalMailRetentionStatusError(error.message || '加载普通邮箱本地保留统计失败');
                }
                return null;
            }
        }

        async function clearNormalMailRetentionCache() {
            const confirmed = await showConfirmModal(
                '确定要清理普通邮箱本地缓存吗？这只会删除本机 SQLite 中保留的普通邮箱列表和正文缓存，不会关闭本地保留开关。',
                { title: '清理普通邮箱本地缓存', confirmText: '确认清理' }
            );
            if (!confirmed) return false;
            try {
                const response = await fetch('/api/settings/normal-mail-retention/clear', { method: 'POST' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '启动普通邮箱本地缓存清理失败');
                }
                if (typeof invalidateNormalMailRetentionCaches === 'function') {
                    invalidateNormalMailRetentionCaches({ resetCurrentView: true });
                }
                resetNormalMailRetentionStatusPollDelay();
                updateNormalMailRetentionStats({
                    ...(lastNormalMailRetentionStatus || {}),
                    clear_status: data.status || { state: 'running', message: '正在清理普通邮箱本地缓存…' }
                });
                showToast(data.already_running ? '普通邮箱本地缓存正在清理中' : '已开始清理普通邮箱本地缓存', 'success');
                scheduleNormalMailRetentionStatusPoll();
                return true;
            } catch (error) {
                renderNormalMailRetentionStatusError(error.message || '启动普通邮箱本地缓存清理失败');
                showToast('启动普通邮箱本地缓存清理失败', 'error');
                return false;
            }
        }


        function getSettingsScrollContainer() {
            return document.querySelector('#settingsModal .settings-modal-body')
                || document.querySelector('#settingsModal .settings-modal-content');
        }

        function populateTimeZoneOptions(selectedTimeZone = getAppTimeZone()) {
            const select = document.getElementById('settingsAppTimezone');
            if (!select) {
                return;
            }

            const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            const normalizedTimeZone = isValidAppTimeZone(selectedTimeZone)
                ? selectedTimeZone
                : getAppTimeZone();
            const availableTimeZones = getAvailableAppTimeZones();
            if (!availableTimeZones.includes(normalizedTimeZone)) {
                availableTimeZones.unshift(normalizedTimeZone);
            }

            select.innerHTML = '';
            availableTimeZones.forEach(timeZone => {
                const option = document.createElement('option');
                option.value = timeZone;
                option.textContent = timeZone === browserTimeZone
                    ? `${timeZone} (System)`
                    : timeZone;
                option.selected = timeZone === normalizedTimeZone;
                select.appendChild(option);
            });
        }

        // 显示设置模态框
        async function showSettingsModal() {
            ensureSettingsScrollSync();
            ensureIcloudHmeSettingsEvents();
            showModal('settingsModal');
            scrollSettingsSection('settingsGeneralSection');
            populateTimeZoneOptions(getAppTimeZone());
            await loadSettings();
            await Promise.all([
                loadIcloudHmeSources(),
                loadIcloudHmeGroups()
            ]);
            await loadIcloudHmeLongRunnerStatus();
            scheduleSettingsSidebarSync();
        }

        async function openIcloudHmeSettings() {
            await showSettingsModal();
            window.setTimeout(() => scrollSettingsSection('settingsIcloudHmeSection'), 80);
        }

        // 隐藏设置模态框
        function hideSettingsModal() {
            stopIcloudHmeLongRunnerPolling();
            hideModal('settingsModal');
            // 清空密码输入框
            const passwordInput = document.getElementById('settingsPassword');
            if (passwordInput) {
                passwordInput.value = '';
            }
            const backupVerifyInput = document.getElementById('webdavBackupVerifyPassword');
            if (backupVerifyInput) {
                backupVerifyInput.value = '';
            }
        }

        function updateSettingsSidebarActive(sectionId) {
            document.querySelectorAll('#settingsModal .settings-sidebar-link').forEach(link => {
                link.classList.toggle('is-active', link.dataset.target === sectionId);
            });
        }

        function getSettingsSidebarSectionIds() {
            return Array.from(document.querySelectorAll('#settingsModal .settings-sidebar-link'))
                .map(link => link.dataset.target)
                .filter(Boolean);
        }

        function syncSettingsSidebarActiveByScroll() {
            const scrollContainer = getSettingsScrollContainer();
            if (!scrollContainer) {
                return;
            }

            const sectionIds = getSettingsSidebarSectionIds();
            if (!sectionIds.length) {
                return;
            }

            const modalContent = document.querySelector('#settingsModal .settings-modal-content');
            const header = modalContent?.querySelector('.modal-header');
            const headerHeight = scrollContainer === modalContent && header ? header.offsetHeight : 0;
            const anchorTop = scrollContainer.getBoundingClientRect().top + headerHeight + 28;
            let activeSectionId = sectionIds[0];
            let closestAboveId = '';
            let closestAboveOffset = Number.NEGATIVE_INFINITY;
            let closestBelowId = '';
            let closestBelowOffset = Number.POSITIVE_INFINITY;

            sectionIds.forEach(sectionId => {
                const section = document.getElementById(sectionId);
                if (!section) {
                    return;
                }

                const offset = section.getBoundingClientRect().top - anchorTop;
                if (offset <= 0 && offset > closestAboveOffset) {
                    closestAboveOffset = offset;
                    closestAboveId = sectionId;
                }
                if (offset > 0 && offset < closestBelowOffset) {
                    closestBelowOffset = offset;
                    closestBelowId = sectionId;
                }
            });

            if (closestAboveId) {
                activeSectionId = closestAboveId;
            } else if (closestBelowId) {
                activeSectionId = closestBelowId;
            }

            updateSettingsSidebarActive(activeSectionId);
        }

        function scheduleSettingsSidebarSync() {
            if (settingsScrollSyncFrame) {
                return;
            }

            settingsScrollSyncFrame = window.requestAnimationFrame(() => {
                settingsScrollSyncFrame = 0;
                syncSettingsSidebarActiveByScroll();
            });
        }

        function ensureSettingsScrollSync() {
            if (settingsScrollSyncBound) {
                return;
            }

            const scrollContainer = getSettingsScrollContainer();
            if (!scrollContainer) {
                return;
            }

            scrollContainer.addEventListener('scroll', scheduleSettingsSidebarSync, { passive: true });
            window.addEventListener('resize', scheduleSettingsSidebarSync);
            settingsScrollSyncBound = true;
        }

        function scrollSettingsSection(sectionId, triggerEl = null) {
            const scrollContainer = getSettingsScrollContainer();
            const section = document.getElementById(sectionId);
            if (!scrollContainer || !section) {
                return;
            }

            const modalContent = document.querySelector('#settingsModal .settings-modal-content');
            const header = modalContent.querySelector('.modal-header');
            const headerHeight = scrollContainer === modalContent && header ? header.offsetHeight : 0;
            const sectionTop = section.getBoundingClientRect().top - scrollContainer.getBoundingClientRect().top + scrollContainer.scrollTop;
            const targetTop = Math.max(sectionTop - headerHeight - 18, 0);

            scrollContainer.scrollTo({
                top: targetTop,
                behavior: 'smooth'
            });

            if (triggerEl?.dataset?.target) {
                updateSettingsSidebarActive(triggerEl.dataset.target);
            } else {
                updateSettingsSidebarActive(sectionId);
            }

            if (sectionId === 'settingsIcloudHmeSection') {
                void loadIcloudHmeLongRunnerStatus();
                const addressDrawer = document.getElementById('icloudHmeAddressDrawer');
                if (addressDrawer?.dataset.loaded !== 'true') {
                    void loadIcloudHmeAddresses({ refresh: true, offset: 0 });
                }
            }
        }

        // 生成随机对外 API Key
        function generateExternalApiKey() {
            const array = new Uint8Array(16);
            crypto.getRandomValues(array);
            const key = Array.from(array, b => b.toString(16).padStart(2, '0')).join('');
            document.getElementById('settingsExternalApiKey').value = key;
            showToast('已生成随机 API Key，请保存设置', 'success');
        }

        // 切换刷新策略
        function toggleRefreshStrategy() {
            const strategy = document.querySelector('input[name="refreshStrategy"]:checked').value;
            document.getElementById('daysStrategyContainer').style.display = strategy === 'days' ? 'block' : 'none';
            document.getElementById('cronStrategyContainer').style.display = strategy === 'cron' ? 'block' : 'none';
        }

        // 选择 Cron 样例
        async function selectCronExample(cronExpr) {
            document.getElementById('refreshCron').value = cronExpr;
            await validateCronExpression();
        }

        // 验证 Cron 表达式
        async function validateCronExpression() {
            const cronExpr = document.getElementById('refreshCron').value.trim();
            const resultEl = document.getElementById('cronValidationResult');

            if (!cronExpr) {
                resultEl.innerHTML = '';
                resultEl.style.display = 'none';
                return;
            }

            const selectedTimeZone = document.getElementById('settingsAppTimezone')?.value || getAppTimeZone();
            if (!isValidAppTimeZone(selectedTimeZone)) {
                resultEl.style.display = 'block';
                resultEl.innerHTML = `
                    <div style="color: #dc3545;">
                        Invalid time zone
                    </div>
                `;
                return;
            }

            try {
                const response = await fetch('/api/settings/validate-cron', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cron_expression: cronExpr,
                        time_zone: selectedTimeZone
                    })
                });

                const data = await response.json();

                if (data.success && data.valid) {
                    const previewTimeZone = data.time_zone || selectedTimeZone;
                    const nextRun = new Date(data.next_run).toLocaleString('zh-CN', {
                        timeZone: previewTimeZone,
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                    resultEl.style.display = 'block';
                    resultEl.innerHTML = `
                        <div style="color: #28a745;">
                            ✓ 表达式有效<br>
                            下次执行: ${nextRun}
                        </div>
                    `;
                } else {
                    resultEl.style.display = 'block';
                    resultEl.innerHTML = `
                        <div style="color: #dc3545;">
                            ✗ ${data.error && data.error.message ? data.error.message : (data.error || '表达式无效')}
                        </div>
                    `;
                }
            } catch (error) {
                resultEl.style.display = 'block';
                resultEl.innerHTML = `
                    <div style="color: #dc3545;">
                        ✗ 验证失败: ${error.message}
                    </div>
                `;
            }
        }

        function normalizeWebdavBackupSettings(settings) {
            return {
                webdav_backup_enabled: String(settings?.webdav_backup_enabled) === 'true' || settings?.webdav_backup_enabled === true ? 'true' : 'false',
                webdav_backup_url: String(settings?.webdav_backup_url || '').trim(),
                webdav_backup_username: String(settings?.webdav_backup_username || '').trim(),
                webdav_backup_password: String(settings?.webdav_backup_password || '').trim(),
                webdav_backup_cron: String(settings?.webdav_backup_cron || '').trim()
            };
        }

        function getWebdavBackupFormSettings() {
            return normalizeWebdavBackupSettings({
                webdav_backup_enabled: !!document.getElementById('webdavBackupEnabled')?.checked,
                webdav_backup_url: document.getElementById('webdavBackupUrl')?.value || '',
                webdav_backup_username: document.getElementById('webdavBackupUsername')?.value || '',
                webdav_backup_password: document.getElementById('webdavBackupPassword')?.value || '',
                webdav_backup_cron: document.getElementById('webdavBackupCron')?.value || ''
            });
        }

        function hasWebdavBackupSettingsChanged(currentSettings) {
            if (!lastLoadedWebdavBackupSettings) {
                return Object.values(currentSettings).some(value => value !== '' && value !== 'false');
            }
            return Object.keys(currentSettings).some(key => currentSettings[key] !== lastLoadedWebdavBackupSettings[key]);
        }

        function buildWebdavBackupDraftConfig() {
            return {
                url: document.getElementById('webdavBackupUrl')?.value.trim() || '',
                username: document.getElementById('webdavBackupUsername')?.value.trim() || '',
                password: document.getElementById('webdavBackupPassword')?.value || ''
            };
        }

        function renderWebdavBackupStatus(settings) {
            const statusEl = document.getElementById('webdavBackupStatus');
            if (!statusEl) return;

            const lines = [];
            if (settings.webdav_backup_next_run) {
                lines.push(`下次执行：${formatAbsoluteDateTime(settings.webdav_backup_next_run)}（${settings.app_timezone || getAppTimeZone()}）`);
            }
            if (settings.webdav_backup_last_run_at) {
                const statusText = settings.webdav_backup_last_status === 'success' ? '成功' : (settings.webdav_backup_last_status || '未知');
                lines.push(`上次执行：${formatAbsoluteDateTime(settings.webdav_backup_last_run_at)}，状态：${statusText}`);
            }
            if (settings.webdav_backup_last_filename) {
                lines.push(`最近文件：${settings.webdav_backup_last_filename}`);
            }
            if (settings.webdav_backup_last_message) {
                lines.push(settings.webdav_backup_last_message);
            }

            statusEl.style.display = 'block';
            statusEl.textContent = lines.length ? lines.join('\n') : '尚未执行备份。保存设置后，调度器重启时会加载新的 Cron 计划。';
        }

        async function selectWebdavBackupCronExample(cronExpr) {
            const input = document.getElementById('webdavBackupCron');
            if (input) {
                input.value = cronExpr;
            }
            await validateWebdavBackupCronExpression();
        }

        async function validateWebdavBackupCronExpression() {
            const cronExpr = document.getElementById('webdavBackupCron')?.value.trim() || '';
            const resultEl = document.getElementById('webdavBackupCronValidationResult');
            if (!resultEl) return;

            if (!cronExpr) {
                resultEl.innerHTML = '';
                resultEl.style.display = 'none';
                return;
            }

            const selectedTimeZone = document.getElementById('settingsAppTimezone')?.value || getAppTimeZone();
            if (!isValidAppTimeZone(selectedTimeZone)) {
                resultEl.style.display = 'block';
                resultEl.innerHTML = '<div style="color: #dc3545;">Invalid time zone</div>';
                return;
            }

            try {
                const response = await fetch('/api/settings/validate-cron', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        cron_expression: cronExpr,
                        time_zone: selectedTimeZone,
                        expected_fields: 5
                    })
                });
                const data = await response.json();
                if (data.success && data.valid) {
                    const previewTimeZone = data.time_zone || selectedTimeZone;
                    const nextRun = new Date(data.next_run).toLocaleString('zh-CN', {
                        timeZone: previewTimeZone,
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                    resultEl.style.display = 'block';
                    resultEl.innerHTML = `
                        <div style="color: #28a745;">
                            ✓ 表达式有效<br>
                            下次执行: ${nextRun}
                        </div>
                    `;
                } else {
                    resultEl.style.display = 'block';
                    resultEl.innerHTML = `
                        <div style="color: #dc3545;">
                            ✗ ${data.error && data.error.message ? data.error.message : (data.error || '表达式无效')}
                        </div>
                    `;
                }
            } catch (error) {
                resultEl.style.display = 'block';
                resultEl.innerHTML = `
                    <div style="color: #dc3545;">
                        ✗ 验证失败: ${error.message}
                    </div>
                `;
            }
        }

        async function testWebdavBackup() {
            const btn = document.getElementById('testWebdavBackupBtn');
            const resultEl = document.getElementById('webdavBackupTestResult');
            if (!btn || btn.disabled) return;

            const draft = buildWebdavBackupDraftConfig();

            if (!draft.url) {
                showToast('请先填写 WebDAV 目录 URL', 'error');
                return;
            }
            try {
                const backupUrl = new URL(draft.url);
                if (!['http:', 'https:'].includes(backupUrl.protocol)) {
                    showToast('WebDAV 目录 URL 必须是 http(s) 地址', 'error');
                    return;
                }
            } catch (error) {
                showToast('WebDAV 目录 URL 无效', 'error');
                return;
            }

            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '测试中...';
            if (resultEl) {
                resultEl.style.display = 'block';
                resultEl.style.color = '';
                resultEl.textContent = '正在上传测试文件...';
            }

            try {
                const response = await fetch('/api/settings/test-webdav-backup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        config: draft
                    })
                });
                const data = await response.json();
                if (data.success) {
                    const message = data.message || 'WebDAV 测试成功';
                    showToast(message, 'success');
                    if (resultEl) {
                        resultEl.style.display = 'block';
                        resultEl.style.color = '#28a745';
                        resultEl.textContent = `✓ ${message}`;
                    }
                } else {
                    const message = data.error && data.error.message ? data.error.message : (data.error || 'WebDAV 测试失败');
                    handleApiError(data, 'WebDAV 测试失败');
                    if (resultEl) {
                        resultEl.style.display = 'block';
                        resultEl.style.color = '#dc3545';
                        resultEl.textContent = `✗ ${message}`;
                    }
                }
            } catch (error) {
                showToast('WebDAV 测试失败', 'error');
                if (resultEl) {
                    resultEl.style.display = 'block';
                    resultEl.style.color = '#dc3545';
                    resultEl.textContent = `✗ WebDAV 测试失败: ${error.message}`;
                }
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        }

        async function uploadWebdavBackupNow() {
            const btn = document.getElementById('uploadWebdavBackupBtn');
            const resultEl = document.getElementById('webdavBackupTestResult');
            if (!btn || btn.disabled) return;

            const draft = buildWebdavBackupDraftConfig();
            const loginPassword = document.getElementById('webdavBackupVerifyPassword')?.value || '';

            if (!draft.url) {
                showToast('请先填写 WebDAV 目录 URL', 'error');
                return;
            }
            try {
                const backupUrl = new URL(draft.url);
                if (!['http:', 'https:'].includes(backupUrl.protocol)) {
                    showToast('WebDAV 目录 URL 必须是 http(s) 地址', 'error');
                    return;
                }
            } catch (error) {
                showToast('WebDAV 目录 URL 无效', 'error');
                return;
            }
            if (!loginPassword) {
                showToast('手动上传备份需要输入登录密码', 'error');
                return;
            }

            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '上传中...';
            if (resultEl) {
                resultEl.style.display = 'block';
                resultEl.style.color = '';
                resultEl.textContent = '正在上传真实备份文件...';
            }

            try {
                const response = await fetch('/api/settings/upload-webdav-backup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        config: draft,
                        login_password: loginPassword
                    })
                });
                const data = await response.json();
                if (data.success) {
                    const message = data.message || 'WebDAV 备份已上传';
                    showToast(message, 'success');
                    if (resultEl) {
                        resultEl.style.display = 'block';
                        resultEl.style.color = '#28a745';
                        resultEl.textContent = `✓ ${message}`;
                    }
                    await loadSettings();
                } else {
                    const message = data.error && data.error.message ? data.error.message : (data.error || 'WebDAV 备份上传失败');
                    handleApiError(data, '手动上传失败');
                    if (resultEl) {
                        resultEl.style.display = 'block';
                        resultEl.style.color = '#dc3545';
                        resultEl.textContent = `✗ ${message}`;
                    }
                }
            } catch (error) {
                showToast('手动上传失败', 'error');
                if (resultEl) {
                    resultEl.style.display = 'block';
                    resultEl.style.color = '#dc3545';
                    resultEl.textContent = `✗ 手动上传失败: ${error.message}`;
                }
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        }

        function ensureEditForwardToggle() {
            if (document.getElementById('editForwardEnabled')) return;
            const statusGroup = document.getElementById('editStatus')?.closest('.form-group');
            if (!statusGroup) return;
            statusGroup.insertAdjacentHTML('afterend', `
                <div class="form-group">
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                        <input type="checkbox" id="editForwardEnabled">
                        <span class="form-label" style="margin: 0;">启用邮件转发</span>
                    </label>
                    <div class="form-hint">开启后会按系统设置转发到邮箱或 Telegram。</div>
                </div>
            `);
        }

        function renderImportTagOptions() {
            const container = document.getElementById('importTagOptions');
            if (!container) return;

            const tags = typeof allTags !== 'undefined' && Array.isArray(allTags) ? allTags : [];
            if (!tags.length) {
                container.innerHTML = '<div class="import-tag-empty">暂无标签</div>';
                updateImportTagSummary();
                return;
            }

            container.innerHTML = tags.map(tag => `
                <label class="import-tag-option">
                    <input type="checkbox" class="import-tag-checkbox" value="${tag.id}" onchange="updateImportTagSummary()">
                    <span class="import-tag-dot" style="background-color: ${escapeHtml(tag.color || '#9ca3af')};"></span>
                    <span>${escapeHtml(tag.name || '')}</span>
                </label>
            `).join('');
            updateImportTagSummary();
        }

        function updateImportTagSummary() {
            const summaryEl = document.getElementById('importTagSummary');
            const countEl = document.getElementById('importTagCount');
            if (!summaryEl || !countEl) return;

            const selected = Array.from(document.querySelectorAll('.import-tag-checkbox:checked'))
                .map(checkbox => {
                    const label = checkbox.closest('.import-tag-option');
                    return label ? label.textContent.trim() : '';
                })
                .filter(Boolean);

            if (!selected.length) {
                summaryEl.textContent = '未选择标签';
                countEl.style.display = 'none';
                countEl.textContent = '';
                return;
            }

            summaryEl.textContent = selected.length <= 2 ? selected.join('、') : `已选 ${selected.length} 个标签`;
            countEl.style.display = 'inline-flex';
            countEl.textContent = String(selected.length);
        }

        function toggleImportTagDropdown(event) {
            event?.stopPropagation();
            const dropdown = document.getElementById('importTagDropdown');
            if (!dropdown) return;
            dropdown.classList.toggle('open');
        }

        function resetImportDefaults() {
            const remarkInput = document.getElementById('importRemark');
            const statusSelect = document.getElementById('importStatus');
            const forwardInput = document.getElementById('importForwardEnabled');
            const tagDropdown = document.getElementById('importTagDropdown');

            if (remarkInput) remarkInput.value = '';
            if (statusSelect) statusSelect.value = 'active';
            if (forwardInput) forwardInput.checked = false;
            tagDropdown?.classList.remove('open');
            renderImportTagOptions();
        }

        function getImportSelectedTagIds() {
            return Array.from(document.querySelectorAll('.import-tag-checkbox:checked'))
                .map(checkbox => parseInt(checkbox.value, 10))
                .filter(Number.isFinite);
        }

        function getIcloudHmeSourceLabel(source) {
            if (!source) return '未命名 HME 源';
            const name = source.name || source.receiver_email || `Source #${source.id}`;
            const receiver = source.receiver_email ? ` (${source.receiver_email})` : '';
            return `${name}${receiver}`;
        }

        function renderIcloudHmeSourceOptions(select, selectedId = '') {
            if (!select) return;
            const currentValue = String(selectedId || select.value || '');
            const options = ['<option value="">请选择 HME 源...</option>'];
            icloudHmeSourcesCache.forEach(source => {
                const value = String(source.id);
                options.push(`<option value="${escapeHtml(value)}">${escapeHtml(getIcloudHmeSourceLabel(source))}</option>`);
            });
            select.innerHTML = options.join('');
            if (currentValue && icloudHmeSourcesCache.some(source => String(source.id) === currentValue)) {
                select.value = currentValue;
            } else if (!currentValue && icloudHmeSourcesCache.length === 1) {
                select.value = String(icloudHmeSourcesCache[0].id);
            }
        }

        function renderIcloudHmeSourceList() {
            const listEl = document.getElementById('icloudHmeSourceList');
            if (!listEl) return;
            if (!icloudHmeSourcesCache.length) {
                listEl.innerHTML = '<div class="hme-source-empty">暂无 HME 源，请先新建。</div>';
                return;
            }
            listEl.innerHTML = icloudHmeSourcesCache.map(source => `
                <div class="hme-source-card">
                    <div class="hme-source-card__main">
                        <strong>${escapeHtml(source.name || '未命名 HME 源')}</strong>
                        <span>${escapeHtml(source.receiver_email || '')}</span>
                        <small>同步状态：${escapeHtml(source.last_sync_status || '未同步')}</small>
                    </div>
                    <div class="hme-source-card__actions">
                        <button class="btn btn-sm btn-secondary" type="button" onclick="openIcloudHmeSourceModal(${Number(source.id)})">编辑</button>
                        <button class="btn btn-sm btn-secondary" type="button" onclick="syncIcloudHmeSource(${Number(source.id)})">同步</button>
                    </div>
                </div>
            `).join('');
        }

        async function loadIcloudHmeSources(selectedId = '') {
            try {
                const response = await fetch('/api/icloud-hme/sources', { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载 iCloud HME 源失败');
                }
                icloudHmeSourcesCache = Array.isArray(data.sources) ? data.sources : [];
                renderIcloudHmeSourceOptions(document.getElementById('importIcloudHmeSourceSelect'), selectedId);
                renderIcloudHmeSourceOptions(document.getElementById('editIcloudHmeSourceSelect'), selectedId);
                renderIcloudHmeSourceOptions(document.getElementById('icloudHmeLongRunnerSourceId'), selectedId);
                const addressSourceSelect = document.getElementById('icloudHmeAddressSourceId');
                renderIcloudHmeSourceOptions(addressSourceSelect, selectedId);
                if (addressSourceSelect && !addressSourceSelect.value && icloudHmeSourcesCache.length) {
                    addressSourceSelect.value = String(icloudHmeSourcesCache[0].id);
                }
                renderIcloudHmeSourceList();
                renderIcloudHmeGroupOptions();
                return icloudHmeSourcesCache;
            } catch (error) {
                showToast(error.message || '加载 iCloud HME 源失败', 'error');
                return [];
            }
        }

        function getIcloudHmeAccountGroups() {
            if (icloudHmeGroupsCache.length) {
                return icloudHmeGroupsCache;
            }
            if (typeof getGroupsByMailboxType === 'function') {
                return getGroupsByMailboxType('account');
            }
            return Array.isArray(groups)
                ? groups.filter(group => String(group?.mailbox_type || 'account').toLowerCase() === 'account')
                : [];
        }

        async function loadIcloudHmeGroups() {
            try {
                const response = await fetch('/api/groups', { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载目标分组失败');
                }
                icloudHmeGroupsCache = (Array.isArray(data.groups) ? data.groups : [])
                    .filter(group => String(group?.mailbox_type || 'account').toLowerCase() === 'account');
                renderIcloudHmeGroupOptions();
                return icloudHmeGroupsCache;
            } catch (error) {
                icloudHmeGroupsCache = [];
                renderIcloudHmeGroupOptions();
                showToast(error.message || '加载目标分组失败', 'error');
                return [];
            }
        }

        function getIcloudHmeGroupLabel(group) {
            if (!group) return '';
            const name = typeof normalizeGroupName === 'function'
                ? normalizeGroupName(group.name)
                : (group.name || `Group ${group.id}`);
            return `${name} (Group ID ${group.id})`;
        }

        function renderIcloudHmeGroupOptions() {
            const accountGroups = getIcloudHmeAccountGroups();
            const filterSelect = document.getElementById('icloudHmeAddressGroupFilter');
            if (filterSelect) {
                const currentValue = filterSelect.value || '';
                const options = ['<option value="">全部分组</option>'];
                accountGroups.forEach(group => {
                    options.push(`<option value="${Number(group.id)}">${escapeHtml(getIcloudHmeGroupLabel(group))}</option>`);
                });
                filterSelect.innerHTML = options.join('');
                if (currentValue && Array.from(filterSelect.options).some(option => option.value === currentValue)) {
                    filterSelect.value = currentValue;
                }
            }
            renderIcloudHmeImportGroupOptions(accountGroups);
            renderIcloudHmeLongRunnerGroupOptions();
        }

        function renderIcloudHmeImportGroupOptions(accountGroups = getIcloudHmeAccountGroups()) {
            const select = document.getElementById('icloudHmeAddressImportGroupId');
            if (!select) return;
            const currentValue = select.value || '';
            const options = ['<option value="">请选择现有普通邮箱分组...</option>'];
            accountGroups.forEach(group => {
                options.push(`<option value="${Number(group.id)}">${escapeHtml(getIcloudHmeGroupLabel(group))}</option>`);
            });
            select.innerHTML = options.join('');
            const hasOption = value => Array.from(select.options).some(option => option.value === String(value));
            if (currentValue && hasOption(currentValue)) {
                select.value = currentValue;
            } else if (currentGroupId && hasOption(currentGroupId)) {
                select.value = String(currentGroupId);
            } else if (accountGroups[0]) {
                select.value = String(accountGroups[0].id);
            }
        }

        function renderIcloudHmeLongRunnerGroupOptions() {
            const select = document.getElementById('icloudHmeLongRunnerTargetGroupId');
            if (!select) return;
            const currentValue = select.value || '';
            const options = ['<option value="">请选择目标分组...</option>'];
            getIcloudHmeAccountGroups().forEach(group => {
                options.push(`<option value="${Number(group.id)}">${escapeHtml(getIcloudHmeGroupLabel(group))}</option>`);
            });
            select.innerHTML = options.join('');
            const hasOption = (value) => Array.from(select.options).some(option => option.value === String(value));
            if (currentValue && hasOption(currentValue)) {
                select.value = currentValue;
            } else if (currentGroupId && hasOption(currentGroupId)) {
                select.value = String(currentGroupId);
            } else {
                const firstGroup = getIcloudHmeAccountGroups()[0];
                if (firstGroup && hasOption(firstGroup.id)) {
                    select.value = String(firstGroup.id);
                }
            }
        }

        function getSelectedIcloudHmeSourceId({ allowFallback = true } = {}) {
            const addressSourceId = document.getElementById('icloudHmeAddressSourceId')?.value || '';
            if (addressSourceId) return addressSourceId;
            const formSourceId = document.getElementById('icloudHmeSourceId')?.value || '';
            if (formSourceId) return formSourceId;
            const importSourceId = document.getElementById('importIcloudHmeSourceSelect')?.value || '';
            if (importSourceId) return importSourceId;
            if (allowFallback && icloudHmeSourcesCache.length) {
                return String(icloudHmeSourcesCache[0].id);
            }
            return '';
        }

        function getIcloudHmeAddressFilters() {
            const importStateValue = document.getElementById('icloudHmeAddressImportStateFilter')?.value || '';
            const importStateMap = {
                pending: 'not_imported',
                failed: 'conflict'
            };
            return {
                source_id: getSelectedIcloudHmeSourceId(),
                keyword: document.getElementById('icloudHmeAddressSearchInput')?.value.trim() || '',
                active: document.getElementById('icloudHmeAddressActiveFilter')?.value || '',
                import_state: importStateMap[importStateValue] || importStateValue,
                group_id: document.getElementById('icloudHmeAddressGroupFilter')?.value || ''
            };
        }

        async function loadIcloudHmeAddresses({ refresh = false, offset = 0 } = {}) {
            if (!icloudHmeSourcesCache.length) {
                await loadIcloudHmeSources();
            }
            const filters = getIcloudHmeAddressFilters();
            if (!filters.source_id) {
                renderIcloudHmeAddressSummary({ total: 0, imported: 0, not_imported: 0, conflict: 0 });
                renderIcloudHmeAddressList([]);
                showToast('请先新建或选择 iCloud HME 源', 'error');
                return [];
            }

            const safeOffset = Math.max(0, Number(offset) || 0);
            const pageLimit = icloudHmeAddressPagination.limit || 50;
            const params = new URLSearchParams({
                source_id: filters.source_id,
                limit: String(pageLimit),
                offset: String(safeOffset),
                refresh: refresh ? 'true' : 'false'
            });
            if (filters.keyword) params.set('keyword', filters.keyword);
            if (filters.active) params.set('active', filters.active);
            if (filters.import_state) params.set('import_state', filters.import_state);
            if (filters.group_id) params.set('group_id', filters.group_id);

            const bodyEl = document.getElementById('icloudHmeAddressTableBody');
            if (bodyEl) {
                bodyEl.innerHTML = '<tr><td colspan="8">正在加载 HME 地址...</td></tr>';
            }

            try {
                const response = await fetch(`/api/icloud-hme/addresses?${params.toString()}`, { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载 iCloud HME 地址失败');
                }

                const pageItems = Array.isArray(data.items) ? data.items : [];
                const counts = data.counts || {};
                icloudHmeAddressCache = pageItems;
                icloudHmeAddressPagination = {
                    limit: pageLimit,
                    offset: safeOffset,
                    total: Number(data.pagination?.total ?? counts.filtered ?? pageItems.length)
                };
                const visibleEmails = new Set(pageItems.map(item => item.hme));
                icloudHmeSelectedAddresses = new Set(
                    Array.from(icloudHmeSelectedAddresses).filter(email => visibleEmails.has(email))
                );
                document.getElementById('icloudHmeAddressDrawer')?.setAttribute('data-loaded', 'true');
                renderIcloudHmeAddressSummary({
                    ...counts,
                    total: counts.total,
                    filtered: icloudHmeAddressPagination.total,
                    active: counts.active
                });
                renderIcloudHmeAddressList(pageItems);
                renderIcloudHmeAddressPagination();
                if (data.refresh_error) {
                    showToast(data.refresh_error, 'warning');
                }
                return pageItems;
            } catch (error) {
                renderIcloudHmeAddressList([]);
                showToast(error.message || '加载 iCloud HME 地址失败', 'error');
                return [];
            }
        }

        function renderIcloudHmeAddressSummary(summary = {}) {
            const container = document.getElementById('icloudHmeSummaryCards');
            if (!container) return;
            const total = Number(summary.filtered ?? summary.total ?? 0);
            const active = Number(summary.active ?? 0);
            const imported = Number(summary.imported ?? 0);
            const pending = Number(summary.not_imported ?? summary.pending ?? 0);
            const conflict = Number(summary.conflict ?? 0);
            container.innerHTML = `
                <article class="hme-settings-summary-card">
                    <span>使用中地址</span>
                    <strong>${active}</strong>
                    <small>当前筛选共 ${total} 个地址</small>
                </article>
                <article class="hme-settings-summary-card">
                    <span>已导入账号</span>
                    <strong>${imported}</strong>
                    <small>导入后显示目标分组</small>
                </article>
                <article class="hme-settings-summary-card">
                    <span>待处理</span>
                    <strong>${pending + conflict}</strong>
                    <small>未导入 ${pending} 个，冲突 ${conflict} 个</small>
                </article>
            `;
        }

        function formatIcloudHmeTimestamp(value) {
            if (!value) return '--';
            if (typeof formatAbsoluteDateTime === 'function') {
                return formatAbsoluteDateTime(value);
            }
            return String(value);
        }

        function renderIcloudHmeAddressImportCell(item) {
            const state = item.import_state || 'not_imported';
            if (state === 'imported') {
                return '<span class="status-badge success">已导入</span>';
            }
            if (state === 'conflict' || item.conflict) {
                const details = [
                    item.account_id ? `existing account #${item.account_id}` : '',
                    item.existing_source_id ? `source #${item.existing_source_id}` : ''
                ].filter(Boolean).join(' / ') || 'existing account/source';
                return `<span class="status-badge warning">冲突</span><small>${escapeHtml(details)}</small>`;
            }
            return '<span class="status-badge">未导入</span>';
        }

        function renderIcloudHmeAddressList(addresses) {
            const bodyEl = document.getElementById('icloudHmeAddressTableBody');
            if (!bodyEl) return;
            if (!Array.isArray(addresses) || !addresses.length) {
                bodyEl.innerHTML = '<tr><td colspan="8">暂无 HME 地址，请选择 HME 源并刷新地址。</td></tr>';
                return;
            }

            bodyEl.innerHTML = addresses.map(item => {
                const email = item.hme || '';
                const checked = icloudHmeSelectedAddresses.has(email) ? 'checked' : '';
                const importable = (item.import_state || 'not_imported') === 'not_imported' && !item.conflict;
                const groupText = item.group_id
                    ? `Group ID ${item.group_id}${item.group_name ? ` / ${item.group_name}` : ''}`
                    : (item.conflict ? '已被其他账号占用' : '--');
                const action = item.import_state === 'imported'
                    ? '<button class="btn btn-sm btn-secondary" type="button" disabled>已导入</button>'
                    : item.conflict
                        ? '<button class="btn btn-sm btn-secondary" type="button" disabled>冲突</button>'
                        : `<button class="btn btn-sm btn-primary" type="button" data-action="import-address" data-email="${escapeHtml(email)}">导入到所选分组</button>`;
                return `
                    <tr>
                        <td><input type="checkbox" class="icloud-hme-address-checkbox" data-email="${escapeHtml(email)}" ${checked} ${importable ? '' : 'disabled'}></td>
                        <td>
                            <div class="hme-address-main">
                                <strong>${escapeHtml(email)}</strong>
                                ${item.label ? `<small>Label: ${escapeHtml(item.label)}</small>` : ''}
                                ${item.note ? `<small>备注: ${escapeHtml(item.note)}</small>` : ''}
                            </div>
                        </td>
                        <td>${item.is_active ? '<span class="status-badge success">使用中</span>' : '<span class="status-badge">已停用</span>'}</td>
                        <td>${renderIcloudHmeAddressImportCell(item)}</td>
                        <td>${item.group_id ? `<span class="hme-group-badge">${escapeHtml(groupText)}</span>` : `<span class="hme-address-meta">${escapeHtml(groupText)}</span>`}</td>
                        <td>${escapeHtml(formatIcloudHmeTimestamp(item.created_at))}</td>
                        <td class="mono">${escapeHtml(item.anonymous_id || '--')}</td>
                        <td>${action}</td>
                    </tr>
                `;
            }).join('');
            const selectAll = document.getElementById('icloudHmeAddressSelectAll');
            if (selectAll) selectAll.checked = false;
        }

        function renderIcloudHmeAddressPagination() {
            const { offset, limit, total } = icloudHmeAddressPagination;
            const start = total ? offset + 1 : 0;
            const end = Math.min(offset + limit, total);
            const info = document.getElementById('icloudHmeAddressPaginationInfo');
            if (info) info.textContent = `显示 ${start}-${end} / ${total}`;
            const prevBtn = document.getElementById('icloudHmeAddressPrevBtn');
            const nextBtn = document.getElementById('icloudHmeAddressNextBtn');
            if (prevBtn) prevBtn.disabled = offset <= 0;
            if (nextBtn) nextBtn.disabled = offset + limit >= total;
        }

        function escapeCssAttributeValue(value) {
            if (typeof window !== 'undefined' && window.CSS && typeof window.CSS.escape === 'function') {
                return window.CSS.escape(value);
            }
            return String(value || '').replace(/["\\]/g, '\\$&');
        }

        function toggleIcloudHmeAddressSelection(email, checked) {
            const normalized = String(email || '').trim().toLowerCase();
            if (!normalized) return;
            if (checked) {
                icloudHmeSelectedAddresses.add(normalized);
            } else {
                icloudHmeSelectedAddresses.delete(normalized);
            }
            document.querySelectorAll(`.icloud-hme-address-checkbox[data-email="${escapeCssAttributeValue(normalized)}"]`).forEach(checkbox => {
                checkbox.checked = icloudHmeSelectedAddresses.has(normalized);
            });
        }

        function toggleAllIcloudHmeAddressSelection(checked) {
            document.querySelectorAll('.icloud-hme-address-checkbox:not(:disabled)').forEach(checkbox => {
                checkbox.checked = !!checked;
                toggleIcloudHmeAddressSelection(checkbox.dataset.email, !!checked);
            });
        }

        async function importSelectedIcloudHmeAddresses() {
            const sourceId = getSelectedIcloudHmeSourceId();
            const groupId = document.getElementById('icloudHmeAddressImportGroupId')?.value || '';
            const addresses = Array.from(icloudHmeSelectedAddresses);
            if (!sourceId) {
                showToast('请先选择 HME 源', 'error');
                return;
            }
            if (!groupId) {
                showToast('请选择目标分组', 'error');
                return;
            }
            if (!addresses.length) {
                showToast('请至少选择一个 HME 地址', 'error');
                return;
            }

            try {
                const response = await fetch('/api/icloud-hme/addresses/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source_id: sourceId,
                        group_id: groupId,
                        addresses
                    })
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '导入 HME 地址失败');
                    return;
                }
                const imported = Number(data.imported_count || 0);
                const updated = Number(data.updated_count || 0);
                const conflicts = Number(data.conflict_count || 0);
                const errors = Number(data.error_count || 0);
                showToast(`导入完成：新增 ${imported} 个，更新 ${updated} 个，冲突 ${conflicts} 个，失败 ${errors} 个`, errors ? 'warning' : 'success');
                icloudHmeSelectedAddresses.clear();
                delete accountsCache[groupId];
                await loadGroups();
                await loadIcloudHmeGroups();
                if (currentGroupId) {
                    await loadAccountsByGroup(currentGroupId, true);
                } else {
                    await refreshVisibleAccountList(false);
                }
                await loadIcloudHmeAddresses({ refresh: false, offset: icloudHmeAddressPagination.offset });
            } catch (error) {
                showToast('导入 HME 地址失败', 'error');
            }
        }

        async function importIcloudHmeAddress(email) {
            icloudHmeSelectedAddresses = new Set([String(email || '').trim().toLowerCase()].filter(Boolean));
            renderIcloudHmeAddressList(icloudHmeAddressCache.slice(
                icloudHmeAddressPagination.offset,
                icloudHmeAddressPagination.offset + icloudHmeAddressPagination.limit
            ));
            await importSelectedIcloudHmeAddresses();
        }

        function getIcloudHmeLongRunnerPayload() {
            const getValue = (id) => document.getElementById(id)?.value?.trim() || '';
            const getNumber = (id, fallback) => {
                const value = parseInt(getValue(id), 10);
                return Number.isFinite(value) ? value : fallback;
            };
            const sourceId = getValue('icloudHmeLongRunnerSourceId') || getSelectedIcloudHmeSourceId();
            const groupSelectValue = getValue('icloudHmeLongRunnerTargetGroupId');
            const fallbackGroupId = currentGroupId || getIcloudHmeAccountGroups()[0]?.id || '';
            const targetCount = getNumber('icloudHmeLongRunnerTargetCount', 1);
            return {
                source_id: sourceId,
                target_group_id: groupSelectValue || fallbackGroupId,
                target_count: targetCount,
                total_requested: targetCount,
                label_prefix: getValue('icloudHmeLongRunnerLabelPrefix') || 'OutlookEmail',
                note: getValue('icloudHmeLongRunnerNote'),
                success_delay_seconds: getNumber('icloudHmeLongRunnerSuccessDelaySeconds', 780),
                failure_delay_seconds: getNumber('icloudHmeLongRunnerFailureDelaySeconds', 3900),
                run_window: getValue('icloudHmeLongRunnerRunWindow')
            };
        }

        async function loadIcloudHmeLongRunnerStatus() {
            try {
                const response = await fetch('/api/icloud-hme/long-runner/status', { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载 HME 长时任务状态失败');
                }
                icloudHmeLongRunnerStatus = data.task || { status: 'idle' };
                renderIcloudHmeLongRunnerStatus(icloudHmeLongRunnerStatus);
                await loadIcloudHmeLongRunnerLogs();
                syncIcloudHmeLongRunnerPolling(icloudHmeLongRunnerStatus);
                return icloudHmeLongRunnerStatus;
            } catch (error) {
                renderIcloudHmeLongRunnerStatus({ status: 'failed', last_error: error.message });
                stopIcloudHmeLongRunnerPolling();
                return null;
            }
        }

        function isIcloudHmeLongRunnerActive(status = icloudHmeLongRunnerStatus) {
            return ['pending', 'running', 'stopping'].includes(status?.status || 'idle');
        }

        function stopIcloudHmeLongRunnerPolling() {
            if (icloudHmeLongRunnerPollTimer !== null) {
                window.clearInterval(icloudHmeLongRunnerPollTimer);
                icloudHmeLongRunnerPollTimer = null;
            }
        }

        function syncIcloudHmeLongRunnerPolling(status = icloudHmeLongRunnerStatus) {
            if (!isIcloudHmeLongRunnerActive(status)) {
                stopIcloudHmeLongRunnerPolling();
                return;
            }
            if (icloudHmeLongRunnerPollTimer !== null) return;
            icloudHmeLongRunnerPollTimer = window.setInterval(() => {
                void loadIcloudHmeLongRunnerStatus();
            }, 3000);
        }

        function renderIcloudHmeLongRunnerStatus(status) {
            const currentStatus = status?.status || 'idle';
            const statusEl = document.getElementById('icloudHmeLongRunnerStatus');
            const startBtn = document.getElementById('icloudHmeLongRunnerStartBtn');
            const refreshBtn = document.getElementById('icloudHmeLongRunnerRefreshBtn');
            const stopBtn = document.getElementById('icloudHmeLongRunnerStopBtn');
            const active = ['pending', 'running', 'stopping'].includes(currentStatus);
            const startDisabled = active;
            const stopEnabled = active;
            if (statusEl) {
                const progress = status?.total_requested
                    ? `进度：${Number(status.success_count || 0) + Number(status.failed_count || 0)}/${status.total_requested}`
                    : '尚未启动';
                const errorText = status?.last_error ? `，错误：${status.last_error}` : '';
                statusEl.textContent = `长时任务状态：${currentStatus}，${progress}${errorText}`;
            }
            if (startBtn) startBtn.disabled = startDisabled;
            if (refreshBtn) refreshBtn.disabled = false;
            if (stopBtn) stopBtn.disabled = !stopEnabled;
            document.querySelector('#settingsIcloudHmeSection .hme-status-pill')?.replaceChildren(document.createTextNode(active ? currentStatus : '空闲'));
        }

        async function loadIcloudHmeLongRunnerLogs() {
            const logsEl = document.getElementById('icloudHmeLongRunnerLogs');
            if (!logsEl) return [];
            const taskId = icloudHmeLongRunnerStatus?.id;
            const params = new URLSearchParams({ limit: '200' });
            if (taskId) params.set('task_id', String(taskId));
            try {
                const response = await fetch(`/api/icloud-hme/long-runner/logs?${params.toString()}`, { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '加载 HME 长时任务日志失败');
                }
                const logs = Array.isArray(data.logs) ? data.logs : [];
                logsEl.textContent = logs.length
                    ? logs.map(log => `[${formatIcloudHmeTimestamp(log.created_at)}] ${log.level || 'info'}: ${log.message || ''}`).join('\n')
                    : '暂无运行日志';
                return logs;
            } catch (error) {
                logsEl.textContent = error.message || '加载 HME 长时任务日志失败';
                return [];
            }
        }

        async function startIcloudHmeLongRunner() {
            const payload = getIcloudHmeLongRunnerPayload();
            if (!payload.source_id) {
                showToast('请先选择 HME 源', 'error');
                return;
            }
            if (!payload.target_group_id) {
                showToast('请选择目标分组', 'error');
                return;
            }
            try {
                const response = await fetch('/api/icloud-hme/long-runner/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '启动 HME 长时注册任务失败');
                    return;
                }
                icloudHmeLongRunnerStatus = data.task || { status: 'pending' };
                renderIcloudHmeLongRunnerStatus(icloudHmeLongRunnerStatus);
                await loadIcloudHmeLongRunnerLogs();
                syncIcloudHmeLongRunnerPolling(icloudHmeLongRunnerStatus);
                showToast('HME 长时注册任务已启动', 'success');
            } catch (error) {
                showToast('启动 HME 长时注册任务失败', 'error');
            }
        }

        async function stopIcloudHmeLongRunner() {
            const payload = icloudHmeLongRunnerStatus?.id ? { task_id: icloudHmeLongRunnerStatus.id } : {};
            try {
                const response = await fetch('/api/icloud-hme/long-runner/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '停止 HME 长时注册任务失败');
                    return;
                }
                icloudHmeLongRunnerStatus = data.task || { status: 'stopping' };
                renderIcloudHmeLongRunnerStatus(icloudHmeLongRunnerStatus);
                await loadIcloudHmeLongRunnerLogs();
                syncIcloudHmeLongRunnerPolling(icloudHmeLongRunnerStatus);
                showToast('已请求停止 HME 长时注册任务', 'success');
            } catch (error) {
                showToast('停止 HME 长时注册任务失败', 'error');
            }
        }

        function getIcloudHmeDeactivationScanPayload() {
            return {
                source_id: getSelectedIcloudHmeSourceId(),
                group_id: document.getElementById('icloudHmeAddressGroupFilter')?.value || '',
                folder: 'all',
                subject_contains: 'OpenAI - Access Deactivated',
                limit: 200,
                refresh: false
            };
        }

        async function scanIcloudHmeDeactivationCandidates() {
            const payload = getIcloudHmeDeactivationScanPayload();
            if (!payload.source_id) {
                showToast('请先选择 HME 源', 'error');
                return;
            }
            try {
                const response = await fetch('/api/icloud-hme/deactivation-candidates/scan', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '扫描 HME 停用候选失败');
                    return;
                }
                showToast(`扫描完成：发现 ${Number(data.candidate_count || 0)} 个候选`, 'success');
                await loadIcloudHmeDeactivationCandidates();
            } catch (error) {
                showToast('扫描 HME 停用候选失败', 'error');
            }
        }

        async function loadIcloudHmeDeactivationCandidates() {
            const sourceId = getSelectedIcloudHmeSourceId();
            if (!sourceId) {
                renderIcloudHmeDeactivationCandidates([]);
                return [];
            }
            const params = new URLSearchParams({ source_id: sourceId, limit: '200' });
            try {
                const response = await fetch(`/api/icloud-hme/deactivation-candidates?${params.toString()}`, { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || '获取 HME 停用候选失败');
                }
                icloudHmeDeactivationCandidates = Array.isArray(data.candidates) ? data.candidates : [];
                renderIcloudHmeDeactivationCandidates(icloudHmeDeactivationCandidates);
                return icloudHmeDeactivationCandidates;
            } catch (error) {
                renderIcloudHmeDeactivationCandidates([]);
                showToast(error.message || '获取 HME 停用候选失败', 'error');
                return [];
            }
        }

        function renderIcloudHmeDeactivationCandidates(candidates) {
            const bodyEl = document.getElementById('icloudHmeDeactivationCandidateTableBody');
            if (!bodyEl) return;
            if (!Array.isArray(candidates) || !candidates.length) {
                bodyEl.innerHTML = '<tr><td colspan="5">暂无 Access Deactivated 候选。</td></tr>';
                return;
            }
            bodyEl.innerHTML = candidates.map(candidate => {
                const statusText = candidate.error
                    ? `${candidate.status || 'error'}：${candidate.error}`
                    : (candidate.status || 'pending');
                return `
                    <tr>
                        <td>
                            <label class="checkbox-label">
                                <input type="checkbox" class="icloud-hme-candidate-checkbox" data-id="${Number(candidate.id)}">
                                <span>
                                    <strong>${escapeHtml(candidate.hme || '')}</strong>
                                    ${candidate.anonymous_id ? `<small>anonymousId: ${escapeHtml(candidate.anonymous_id)}</small>` : ''}
                                    ${candidate.group_id ? `<small>Group ID ${Number(candidate.group_id)}${candidate.group_name ? ` / ${escapeHtml(candidate.group_name)}` : ''}</small>` : ''}
                                </span>
                            </label>
                        </td>
                        <td>${escapeHtml(formatIcloudHmeTimestamp(candidate.detected_at || candidate.updated_at || candidate.created_at))}</td>
                        <td>${escapeHtml(candidate.reason || 'OpenAI - Access Deactivated')}</td>
                        <td>${escapeHtml(statusText)}</td>
                        <td>
                            <button class="btn btn-sm btn-danger" type="button" data-action="delete-candidate" data-id="${Number(candidate.id)}">删除</button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function deleteSelectedIcloudHmeCandidates() {
            const sourceId = getSelectedIcloudHmeSourceId();
            const ids = Array.from(document.querySelectorAll('.icloud-hme-candidate-checkbox:checked'))
                .map(checkbox => parseInt(checkbox.dataset.id || '', 10))
                .filter(Number.isFinite);
            if (!sourceId) {
                showToast('请先选择 HME 源', 'error');
                return;
            }
            if (!ids.length) {
                showToast('请选择要删除的停用候选', 'error');
                return;
            }
            const confirmed = await showConfirmModal(
                `确定要停用并删除 HME 地址，此操作不可逆。将处理 ${ids.length} 个候选项，是否继续？`,
                { title: '删除 HME 停用候选', confirmText: '确认停用并删除' }
            );
            if (!confirmed) return;

            try {
                const response = await fetch('/api/icloud-hme/deactivation-candidates/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source_id: sourceId,
                        candidate_ids: ids
                    })
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '删除 HME 停用候选失败');
                    return;
                }
                showToast(`删除完成：成功 ${Number(data.deleted_count || 0)} 个，失败 ${Number(data.error_count || 0)} 个`, data.error_count ? 'warning' : 'success');
                await loadIcloudHmeDeactivationCandidates();
                await loadIcloudHmeAddresses({ refresh: false, offset: icloudHmeAddressPagination.offset });
                if (currentGroupId) {
                    delete accountsCache[currentGroupId];
                    await loadAccountsByGroup(currentGroupId, true);
                }
            } catch (error) {
                showToast('删除 HME 停用候选失败', 'error');
            }
        }

        function ensureIcloudHmeSettingsEvents() {
            if (icloudHmeSettingsEventsBound) return;
            const section = document.getElementById('settingsIcloudHmeSection');
            if (!section) return;

            const bindSectionAction = (id, action, handler) => {
                const element = document.getElementById(id) || section.querySelector(`[data-action="${action}"]`);
                element?.addEventListener('click', handler);
            };

            bindSectionAction('icloudHmeRefreshAddressesBtn', 'refresh-hme-addresses', () => loadIcloudHmeAddresses({ refresh: true, offset: 0 }));
            bindSectionAction('icloudHmeImportSelectedAddressesBtn', 'import-selected-hme-addresses', () => importSelectedIcloudHmeAddresses());

            const drawer = document.getElementById('icloudHmeAddressDrawer');
            const loadDrawerOnce = () => {
                if (drawer?.dataset.loaded === 'true') return;
                loadIcloudHmeAddresses({ refresh: true, offset: 0 });
            };
            drawer?.addEventListener('click', loadDrawerOnce);
            drawer?.addEventListener('focusin', loadDrawerOnce);

            let addressFilterTimer = null;
            document.getElementById('icloudHmeAddressSearchInput')?.addEventListener('input', () => {
                if (drawer?.dataset.loaded !== 'true') return;
                window.clearTimeout(addressFilterTimer);
                addressFilterTimer = window.setTimeout(() => loadIcloudHmeAddresses({ refresh: false, offset: 0 }), 250);
            });
            document.getElementById('icloudHmeAddressSourceId')?.addEventListener('change', () => {
                icloudHmeAddressCache = [];
                icloudHmeSelectedAddresses.clear();
                if (drawer) drawer.dataset.loaded = 'false';
                loadIcloudHmeAddresses({ refresh: true, offset: 0 });
            });
            ['icloudHmeAddressActiveFilter', 'icloudHmeAddressImportStateFilter', 'icloudHmeAddressGroupFilter'].forEach(id => {
                document.getElementById(id)?.addEventListener('change', () => {
                    if (drawer?.dataset.loaded === 'true') {
                        loadIcloudHmeAddresses({ refresh: false, offset: 0 });
                    }
                });
            });

            const addressBody = document.getElementById('icloudHmeAddressTableBody');
            document.getElementById('icloudHmeAddressSelectAll')?.addEventListener('change', event => {
                toggleAllIcloudHmeAddressSelection(event.target.checked);
            });
            document.getElementById('icloudHmeAddressPrevBtn')?.addEventListener('click', () => {
                const nextOffset = Math.max(0, icloudHmeAddressPagination.offset - icloudHmeAddressPagination.limit);
                loadIcloudHmeAddresses({ refresh: false, offset: nextOffset });
            });
            document.getElementById('icloudHmeAddressNextBtn')?.addEventListener('click', () => {
                const nextOffset = icloudHmeAddressPagination.offset + icloudHmeAddressPagination.limit;
                if (nextOffset < icloudHmeAddressPagination.total) {
                    loadIcloudHmeAddresses({ refresh: false, offset: nextOffset });
                }
            });
            addressBody?.addEventListener('change', event => {
                const checkbox = event.target.closest?.('.icloud-hme-address-checkbox');
                if (checkbox) {
                    toggleIcloudHmeAddressSelection(checkbox.dataset.email, checkbox.checked);
                }
            });
            addressBody?.addEventListener('click', event => {
                const actionEl = event.target.closest?.('[data-action]');
                if (!actionEl) return;
                if (actionEl.dataset.action === 'select-address') {
                    toggleIcloudHmeAddressSelection(actionEl.dataset.email, true);
                } else if (actionEl.dataset.action === 'import-address') {
                    importIcloudHmeAddress(actionEl.dataset.email);
                }
            });

            bindSectionAction('icloudHmeLongRunnerStartBtn', 'start-long-runner', () => startIcloudHmeLongRunner());
            bindSectionAction('icloudHmeLongRunnerRefreshBtn', 'refresh-long-runner', async () => {
                await loadIcloudHmeLongRunnerStatus();
            });
            bindSectionAction('icloudHmeLongRunnerStopBtn', 'stop-long-runner', () => stopIcloudHmeLongRunner());

            bindSectionAction('icloudHmeScanCandidatesBtn', 'scan-hme-candidates', () => scanIcloudHmeDeactivationCandidates());
            bindSectionAction('icloudHmeDeleteCandidatesBtn', 'delete-selected-hme-candidates', () => deleteSelectedIcloudHmeCandidates());
            const candidateBody = document.getElementById('icloudHmeDeactivationCandidateTableBody');
            candidateBody?.addEventListener('click', event => {
                const actionEl = event.target.closest?.('[data-action="delete-candidate"]');
                if (!actionEl) return;
                document.querySelectorAll('.icloud-hme-candidate-checkbox').forEach(checkbox => {
                    checkbox.checked = checkbox.dataset.id === actionEl.dataset.id;
                });
                deleteSelectedIcloudHmeCandidates();
            });

            icloudHmeSettingsEventsBound = true;
        }

        function resetIcloudHmeSourceForm() {
            const defaults = {
                icloudHmeSourceId: '',
                icloudHmeSourceName: '',
                icloudHmeSourceRegion: 'global',
                icloudHmeReceiverEmail: '',
                icloudHmeReceiverProvider: 'custom',
                icloudHmeReceiverImapHost: '',
                icloudHmeReceiverImapPort: '993',
                icloudHmeReceiverImapPassword: '',
                icloudHmeReceiverFolder: 'INBOX',
                icloudHmeMaildomainHost: '',
                icloudHmeCookie: ''
            };
            Object.entries(defaults).forEach(([id, value]) => {
                const el = document.getElementById(id);
                if (el) el.value = value;
            });
            const useSsl = document.getElementById('icloudHmeUseSsl');
            if (useSsl) useSsl.checked = true;
            const title = document.getElementById('icloudHmeSourceModalTitle');
            if (title) title.textContent = '新建 iCloud HME 源';
            const deleteBtn = document.getElementById('icloudHmeSourceDeleteBtn');
            const syncBtn = document.getElementById('icloudHmeSourceSyncBtn');
            if (deleteBtn) deleteBtn.disabled = true;
            if (syncBtn) syncBtn.disabled = true;
        }

        function fillIcloudHmeSourceForm(source) {
            if (!source) {
                resetIcloudHmeSourceForm();
                return;
            }
            const values = {
                icloudHmeSourceId: source.id || '',
                icloudHmeSourceName: source.name || '',
                icloudHmeSourceRegion: source.region || 'global',
                icloudHmeReceiverEmail: source.receiver_email || '',
                icloudHmeReceiverProvider: source.receiver_provider || 'custom',
                icloudHmeReceiverImapHost: source.receiver_imap_host || '',
                icloudHmeReceiverImapPort: source.receiver_imap_port || 993,
                icloudHmeReceiverImapPassword: source.receiver_imap_password || '',
                icloudHmeReceiverFolder: source.receiver_folder || 'INBOX',
                icloudHmeMaildomainHost: source.maildomain_host || '',
                icloudHmeCookie: source.cookie || ''
            };
            Object.entries(values).forEach(([id, value]) => {
                const el = document.getElementById(id);
                if (el) el.value = value;
            });
            const useSsl = document.getElementById('icloudHmeUseSsl');
            if (useSsl) useSsl.checked = source.use_ssl !== false;
            const title = document.getElementById('icloudHmeSourceModalTitle');
            if (title) title.textContent = '编辑 iCloud HME 源';
            const deleteBtn = document.getElementById('icloudHmeSourceDeleteBtn');
            const syncBtn = document.getElementById('icloudHmeSourceSyncBtn');
            if (deleteBtn) deleteBtn.disabled = false;
            if (syncBtn) syncBtn.disabled = false;
        }

        async function openIcloudHmeSourceModal(sourceId = null) {
            const settingsSection = document.getElementById('settingsIcloudHmeSection');
            if (settingsSection) {
                const settingsModal = document.getElementById('settingsModal');
                if (!settingsModal?.classList.contains('show')) {
                    await openIcloudHmeSettings();
                } else {
                    scrollSettingsSection('settingsIcloudHmeSection');
                }
            } else {
                showModal('icloudHmeSourceModal');
            }
            resetIcloudHmeSourceForm();
            const sources = await loadIcloudHmeSources(sourceId || '');
            if (!sourceId) return;
            const cached = sources.find(source => String(source.id) === String(sourceId));
            if (cached) {
                fillIcloudHmeSourceForm(cached);
            }
            try {
                const response = await fetch(`/api/icloud-hme/sources/${sourceId}`, { cache: 'no-store' });
                const data = await response.json();
                if (response.ok && data.success) {
                    fillIcloudHmeSourceForm(data.source);
                }
            } catch (error) {
                showToast('加载 iCloud HME 源详情失败', 'error');
            }
        }

        function hideIcloudHmeSourceModal() {
            hideModal('icloudHmeSourceModal');
        }

        function getIcloudHmeSourceFormPayload() {
            const port = parseInt(document.getElementById('icloudHmeReceiverImapPort')?.value || '993', 10);
            return {
                name: document.getElementById('icloudHmeSourceName')?.value.trim() || '',
                region: document.getElementById('icloudHmeSourceRegion')?.value || 'global',
                receiver_email: document.getElementById('icloudHmeReceiverEmail')?.value.trim() || '',
                receiver_provider: document.getElementById('icloudHmeReceiverProvider')?.value || 'custom',
                receiver_imap_host: document.getElementById('icloudHmeReceiverImapHost')?.value.trim() || '',
                receiver_imap_port: Number.isFinite(port) ? port : 993,
                receiver_imap_password: document.getElementById('icloudHmeReceiverImapPassword')?.value || '',
                receiver_folder: document.getElementById('icloudHmeReceiverFolder')?.value.trim() || 'INBOX',
                use_ssl: !!document.getElementById('icloudHmeUseSsl')?.checked,
                cookie: document.getElementById('icloudHmeCookie')?.value.trim() || '',
                maildomain_host: document.getElementById('icloudHmeMaildomainHost')?.value.trim() || ''
            };
        }

        async function saveIcloudHmeSource() {
            const sourceId = document.getElementById('icloudHmeSourceId')?.value || '';
            const payload = getIcloudHmeSourceFormPayload();
            if (!payload.name || !payload.receiver_email || !payload.receiver_imap_host || (!sourceId && !payload.receiver_imap_password)) {
                showToast('源名称、接收邮箱、IMAP 服务器和 IMAP 密码不能为空', 'error');
                return;
            }
            try {
                const response = await fetch(sourceId ? `/api/icloud-hme/sources/${sourceId}` : '/api/icloud-hme/sources', {
                    method: sourceId ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '保存 iCloud HME 源失败');
                    return;
                }
                showToast(data.message || 'iCloud HME 源已保存', 'success');
                fillIcloudHmeSourceForm(data.source);
                await loadIcloudHmeSources(data.source?.id || sourceId);
                if (document.getElementById('icloudHmeAddressDrawer')?.dataset.loaded === 'true') {
                    await loadIcloudHmeAddresses({ refresh: false, offset: 0 });
                }
            } catch (error) {
                showToast('保存 iCloud HME 源失败', 'error');
            }
        }

        async function deleteIcloudHmeSource(sourceId = null) {
            const id = sourceId || document.getElementById('icloudHmeSourceId')?.value;
            if (!id) return;
            if (!(await showConfirmModal('确定要删除这个 iCloud HME 源吗？已绑定账号的源不能删除。', { title: '删除 HME 源', confirmText: '确认删除' }))) {
                return;
            }
            try {
                const response = await fetch(`/api/icloud-hme/sources/${id}`, { method: 'DELETE' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '删除 iCloud HME 源失败');
                    return;
                }
                showToast(data.message || 'iCloud HME 源已删除', 'success');
                resetIcloudHmeSourceForm();
                await loadIcloudHmeSources();
            } catch (error) {
                showToast('删除 iCloud HME 源失败', 'error');
            }
        }

        async function testIcloudHmeSourceImap() {
            const payload = getIcloudHmeSourceFormPayload();
            try {
                const response = await fetch('/api/icloud-hme/sources/test-imap', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await response.json();
                if (response.ok && data.success) {
                    showToast('IMAP 连接测试成功', 'success');
                    return;
                }
                handleApiError(data, 'IMAP 连接测试失败');
            } catch (error) {
                showToast('IMAP 连接测试失败', 'error');
            }
        }

        async function syncIcloudHmeSource(sourceId = null) {
            const id = sourceId || document.getElementById('icloudHmeSourceId')?.value;
            if (!id) {
                showToast('请先选择 HME 源', 'error');
                return;
            }
            try {
                const response = await fetch(`/api/icloud-hme/sources/${id}/sync`, { method: 'POST' });
                const data = await response.json();
                if (!response.ok || !data.success) {
                    handleApiError(data, '同步 iCloud HME 源失败');
                    return;
                }
                const imported = Number(data.imported_count || 0);
                const updated = Number(data.updated_count || 0);
                showToast(`同步完成：新增 ${imported} 个，更新 ${updated} 个`, 'success');
                await loadIcloudHmeSources(id);
                if (document.getElementById('icloudHmeAddressDrawer')?.dataset.loaded === 'true') {
                    await loadIcloudHmeAddresses({ refresh: false, offset: 0 });
                }
                if (currentGroupId) {
                    delete accountsCache[currentGroupId];
                    await loadAccountsByGroup(currentGroupId, true);
                }
            } catch (error) {
                showToast('同步 iCloud HME 源失败', 'error');
            }
        }

        function showAddAccountModal() {
            showModal('addAccountModal');
            document.getElementById('accountInput').value = '';
            updateGroupSelects();
            if (document.getElementById('importProviderSelect')) {
                document.getElementById('importProviderSelect').value = 'outlook';
            }
            if (document.getElementById('importImapHost')) {
                document.getElementById('importImapHost').value = '';
            }
            if (document.getElementById('importImapPort')) {
                document.getElementById('importImapPort').value = '993';
            }
            if (currentGroupId) {
                document.getElementById('importGroupSelect').value = currentGroupId;
            }
            resetImportDefaults();
            updateImportHint();
            loadIcloudHmeSources();
        }

        async function submitAccountImport({
            input,
            groupId,
            provider,
            imapHost,
            imapPort,
            forwardEnabled,
            remark,
            status,
            tagIds,
            isTempGroup
        }) {
            if (isTempGroup) {
                const tempProvider = document.getElementById('importChannelSelect').value || 'gptmail';
                return fetch('/api/temp-emails/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ account_string: input, provider: tempProvider, group_id: groupId })
                });
            }
            if (provider === 'icloud_hme') {
                const sourceId = document.getElementById('importIcloudHmeSourceSelect')?.value || '';
                return fetch('/api/icloud-hme/accounts/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        account_string: input,
                        source_id: sourceId,
                        group_id: groupId,
                        remark,
                        status,
                        tag_ids: tagIds
                    })
                });
            }
            return fetch('/api/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    account_string: input,
                    group_id: groupId,
                    provider,
                    imap_host: imapHost,
                    imap_port: Number.isFinite(imapPort) ? imapPort : 993,
                    forward_enabled: forwardEnabled,
                    remark,
                    status,
                    tag_ids: tagIds
                })
            });
        }

        async function addAccount() {
            const input = document.getElementById('accountInput').value.trim();
            const groupId = parseInt(document.getElementById('importGroupSelect').value);
            const provider = document.getElementById('importProviderSelect')?.value || 'outlook';
            const imapHost = document.getElementById('importImapHost')?.value.trim() || '';
            const imapPort = parseInt(document.getElementById('importImapPort')?.value || '993', 10);
            const forwardEnabled = !!document.getElementById('importForwardEnabled')?.checked;
            const remark = document.getElementById('importRemark')?.value.trim() || '';
            const status = document.getElementById('importStatus')?.value || 'active';
            const tagIds = getImportSelectedTagIds();
            const importButton = document.querySelector('#addAccountModal .btn.btn-primary');

            if (!input) {
                showToast('请输入账号信息', 'error');
                return;
            }

            const isTempGroup = isTempImportGroup();
            if (!isTempGroup && provider === 'custom' && !imapHost) {
                showToast('自定义 IMAP 必须填写服务器地址', 'error');
                return;
            }
            if (!isTempGroup && provider === 'icloud_hme' && !document.getElementById('importIcloudHmeSourceSelect')?.value) {
                showToast('请选择 iCloud HME 源', 'error');
                return;
            }

            try {
                if (importButton) {
                    importButton.disabled = true;
                    importButton.textContent = '导入中...';
                }
                const response = await submitAccountImport({
                    input,
                    groupId,
                    provider,
                    imapHost,
                    imapPort,
                    forwardEnabled,
                    remark,
                    status,
                    tagIds,
                    isTempGroup
                });

                const data = await response.json();
                if (data.success) {
                    const imported = Number(data.imported_count || data.added_count || 0);
                    const updated = Number(data.updated_count || 0);
                    showToast(data.message || `导入完成：新增 ${imported} 个，更新 ${updated} 个`, 'success');
                    hideAddAccountModal();
                    delete accountsCache[groupId];
                    await loadGroups();
                    if (isTempGroup) {
                        await loadTempEmails(true);
                    } else {
                        await loadAccountsByGroup(groupId, true);
                    }
                } else {
                    handleApiError(data, '导入失败');
                }
            } catch (error) {
                showToast('导入失败', 'error');
            } finally {
                if (importButton) {
                    importButton.disabled = false;
                    importButton.textContent = '导入';
                }
            }
        }

        async function showEditAccountModal(accountId) {
            try {
                ensureEditForwardToggle();
                const response = await fetch(`/api/accounts/${accountId}`);
                const data = await response.json();

                if (data.success) {
                    closeAllModals();
                    const acc = data.account;
                    document.getElementById('editAccountId').value = acc.id;
                    document.getElementById('editEmail').value = acc.email || '';
                    document.getElementById('editPassword').value = acc.password || '';
                    document.getElementById('editClientId').value = acc.client_id || '';
                    document.getElementById('editRefreshToken').value = acc.refresh_token || '';
                    document.getElementById('editImapPassword').value = acc.imap_password || '';
                    document.getElementById('editImapHost').value = acc.imap_host || '';
                    document.getElementById('editImapPort').value = acc.imap_port || 993;
                    document.getElementById('editGroupSelect').value = acc.group_id || 1;
                    document.getElementById('editProxyUrl').value = acc.proxy_url || '';
                    document.getElementById('editFallbackProxyUrl1').value = acc.fallback_proxy_url_1 || '';
                    document.getElementById('editFallbackProxyUrl2').value = acc.fallback_proxy_url_2 || '';
                    document.getElementById('editSortOrder').value = Number(acc.sort_order || 0);
                    document.getElementById('editRemark').value = acc.remark || '';
                    document.getElementById('editAliases').value = Array.isArray(acc.aliases) ? acc.aliases.join('\n') : '';
                    document.getElementById('editStatus').value = acc.status || 'active';
                    if (document.getElementById('editForwardEnabled')) {
                        document.getElementById('editForwardEnabled').checked = !!acc.forward_enabled;
                    }
                    if (document.getElementById('editProviderSelect')) {
                        document.getElementById('editProviderSelect').value = acc.provider || (acc.account_type === 'icloud_hme' ? 'icloud_hme' : (acc.account_type === 'imap' ? 'custom' : 'outlook'));
                    }
                    if (acc.provider === 'icloud_hme' || acc.account_type === 'icloud_hme') {
                        await loadIcloudHmeSources(acc.icloud_hme_source_id || '');
                        const hmeSourceSelect = document.getElementById('editIcloudHmeSourceSelect');
                        if (hmeSourceSelect && acc.icloud_hme_source_id) {
                            hmeSourceSelect.value = String(acc.icloud_hme_source_id);
                        }
                    }
                    updateEditAccountFields();
                    setModalVisible('editAccountModal', true);
                }
            } catch (error) {
                showToast('加载账号信息失败', 'error');
            }
        }

        async function updateAccount() {
            const accountId = document.getElementById('editAccountId').value;
            const oldGroupId = currentGroupId;
            const newGroupId = parseInt(document.getElementById('editGroupSelect').value);
            const provider = document.getElementById('editProviderSelect')?.value || 'outlook';
            const isOutlook = provider === 'outlook';
            const isIcloudHme = provider === 'icloud_hme';
            const imapPort = parseInt(document.getElementById('editImapPort')?.value || '993', 10);
            const sortOrder = parseInt(document.getElementById('editSortOrder')?.value || '0', 10);

            const data = {
                email: document.getElementById('editEmail').value.trim(),
                password: document.getElementById('editPassword').value,
                client_id: document.getElementById('editClientId').value.trim(),
                refresh_token: document.getElementById('editRefreshToken').value.trim(),
                account_type: isIcloudHme ? 'icloud_hme' : (isOutlook ? 'outlook' : 'imap'),
                provider,
                icloud_hme_source_id: document.getElementById('editIcloudHmeSourceSelect')?.value || '',
                imap_host: document.getElementById('editImapHost')?.value.trim() || '',
                imap_port: Number.isFinite(imapPort) ? imapPort : 993,
                imap_password: document.getElementById('editImapPassword')?.value || '',
                group_id: newGroupId,
                proxy_url: document.getElementById('editProxyUrl')?.value.trim() || '',
                fallback_proxy_url_1: document.getElementById('editFallbackProxyUrl1')?.value.trim() || '',
                fallback_proxy_url_2: document.getElementById('editFallbackProxyUrl2')?.value.trim() || '',
                sort_order: Number.isFinite(sortOrder) ? Math.max(0, sortOrder) : 0,
                remark: document.getElementById('editRemark').value.trim(),
                aliases: document.getElementById('editAliases')?.value
                    .split('\n')
                    .map(item => item.trim())
                    .filter(Boolean),
                status: document.getElementById('editStatus').value,
                forward_enabled: !!document.getElementById('editForwardEnabled')?.checked
            };

            if (isOutlook) {
                if (!data.email || !data.client_id || !data.refresh_token) {
                    showToast('邮箱、Client ID 和 Refresh Token 不能为空', 'error');
                    return;
                }
            } else {
                if (!data.email || (!isIcloudHme && !data.imap_password)) {
                    showToast('邮箱和 IMAP 密码不能为空', 'error');
                    return;
                }
                if (provider === 'custom' && !data.imap_host) {
                    showToast('自定义 IMAP 必须填写服务器地址', 'error');
                    return;
                }
            }
            if (!Number.isFinite(sortOrder) || sortOrder < 0) {
                showToast('排序值不能小于 0', 'error');
                return;
            }

            try {
                const response = await fetch(`/api/accounts/${accountId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });

                const result = await response.json();
                if (result.success) {
                    showToast(result.message, 'success');
                    hideEditAccountModal();
                    delete accountsCache[oldGroupId];
                    if (oldGroupId !== newGroupId) {
                        delete accountsCache[newGroupId];
                    }
                    loadGroups();
                    if (currentGroupId) {
                        loadAccountsByGroup(currentGroupId, true);
                    }
                } else {
                    handleApiError(result, '更新失败');
                }
            } catch (error) {
                showToast('更新失败', 'error');
            }
        }

        async function loadSettings() {
            ensureForwardingSettingsUI();
            try {
                const response = await fetch('/api/settings');
                const data = await response.json();

                if (data.success) {
                    const appTimeZone = data.settings.app_timezone || getAppTimeZone();
                    setAppTimeZone(appTimeZone);
                    populateTimeZoneOptions(appTimeZone);
                    document.getElementById('settingsApiKey').value = data.settings.gptmail_api_key || '';
                    document.getElementById('settingsExternalApiKey').value = data.settings.external_api_key || '';
                    document.getElementById('settingsDuckmailBaseUrl').value = data.settings.duckmail_base_url || '';
                    document.getElementById('settingsDuckmailApiKey').value = data.settings.duckmail_api_key || '';
                    document.getElementById('settingsCloudflareWorkerDomain').value = data.settings.cloudflare_worker_domain || '';
                    document.getElementById('settingsCloudflareEmailDomains').value = data.settings.cloudflare_email_domains || '';
                    document.getElementById('settingsCloudflareAdminPassword').value = data.settings.cloudflare_admin_password || '';
                    document.getElementById('settingsAppTimezone').value = appTimeZone;
                    document.getElementById('settingsPassword').value = '';

                    document.getElementById('refreshIntervalDays').value = data.settings.refresh_interval_days || '30';
                    document.getElementById('refreshDelaySeconds').value = data.settings.refresh_delay_seconds || '5';
                    document.getElementById('refreshCron').value = data.settings.refresh_cron || '0 2 * * *';
                    document.getElementById('enableScheduledRefresh').checked = data.settings.enable_scheduled_refresh !== 'false';
                    document.getElementById('settingsShowAccountCreatedAt').checked = String(data.settings.show_account_created_at) !== 'false';
                    document.getElementById('settingsShowAccountSortOrder').checked = String(data.settings.show_account_sort_order) === 'true';
                    document.getElementById('settingsShowGroupId').checked = String(data.settings.show_group_id) !== 'false';
                    const retentionEnabled = parseSettingsBoolean(data.settings.normal_mail_local_retention_enabled);
                    document.getElementById('normalMailLocalRetentionEnabled').checked = retentionEnabled;
                    setNormalMailLocalRetentionEnabled(retentionEnabled);
                    document.getElementById('forwardCheckIntervalMinutes').value = data.settings.forward_check_interval_minutes || '5';
                    document.getElementById('forwardAccountDelaySeconds').value = data.settings.forward_account_delay_seconds || '0';
                    document.getElementById('forwardEmailWindowMinutes').value = data.settings.forward_email_window_minutes || '0';
                    document.getElementById('forwardIncludeJunkemail').checked = String(data.settings.forward_include_junkemail) === 'true';
                    document.getElementById('settingsEmailForwardRecipient').value = data.settings.email_forward_recipient || '';
                    document.getElementById('settingsSmtpHost').value = data.settings.smtp_host || '';
                    document.getElementById('settingsSmtpPort').value = data.settings.smtp_port || '465';
                    document.getElementById('settingsSmtpUsername').value = data.settings.smtp_username || '';
                    document.getElementById('settingsSmtpPassword').value = data.settings.smtp_password || '';
                    document.getElementById('settingsSmtpProvider').value = normalizeSmtpForwardProvider(data.settings.smtp_provider || 'custom');
                    document.getElementById('settingsSmtpFromEmail').value = data.settings.smtp_from_email || '';
                    document.getElementById('settingsSmtpUseTls').checked = String(data.settings.smtp_use_tls) === 'true';
                    document.getElementById('settingsSmtpUseSsl').checked = String(data.settings.smtp_use_ssl) !== 'false';
                    document.getElementById('settingsTelegramBotToken').value = data.settings.telegram_bot_token || '';
                    document.getElementById('settingsTelegramChatId').value = data.settings.telegram_chat_id || '';
                    document.getElementById('settingsTelegramProxyUrl').value = data.settings.telegram_proxy_url || '';
                    document.getElementById('settingsWecomWebhookUrl').value = data.settings.wecom_webhook_url || '';
                    document.getElementById('webdavBackupEnabled').checked = String(data.settings.webdav_backup_enabled) === 'true';
                    document.getElementById('webdavBackupUrl').value = data.settings.webdav_backup_url || '';
                    document.getElementById('webdavBackupUsername').value = data.settings.webdav_backup_username || '';
                    document.getElementById('webdavBackupPassword').value = data.settings.webdav_backup_password || '';
                    document.getElementById('webdavBackupCron').value = data.settings.webdav_backup_cron || '0 3 * * *';
                    document.getElementById('webdavBackupVerifyPassword').value = '';
                    lastLoadedWebdavBackupSettings = normalizeWebdavBackupSettings(data.settings);
                    renderWebdavBackupStatus(data.settings);
                    setSelectedForwardChannels(data.settings.forward_channels || []);

                    const useCron = data.settings.use_cron_schedule === 'true';
                    document.querySelector('input[name="refreshStrategy"][value="' + (useCron ? 'cron' : 'days') + '"]').checked = true;
                    toggleRefreshStrategy();
                    syncSmtpProviderUI(false);
                    await loadNormalMailRetentionStatus();
                }
            } catch (error) {
                showToast('加载设置失败', 'error');
            }
        }

        async function saveSettings() {
            ensureForwardingSettingsUI();
            const password = document.getElementById('settingsPassword').value;
            const apiKey = document.getElementById('settingsApiKey').value.trim();
            const externalApiKey = document.getElementById('settingsExternalApiKey').value.trim();
            const refreshDays = document.getElementById('refreshIntervalDays').value;
            const refreshDelay = document.getElementById('refreshDelaySeconds').value;
            const refreshCron = document.getElementById('refreshCron').value.trim();
            const appTimeZone = document.getElementById('settingsAppTimezone').value.trim();
            const strategy = document.querySelector('input[name="refreshStrategy"]:checked').value;
            const enableScheduled = document.getElementById('enableScheduledRefresh').checked;
            const showAccountCreatedAt = !!document.getElementById('settingsShowAccountCreatedAt')?.checked;
            const showAccountSortOrder = !!document.getElementById('settingsShowAccountSortOrder')?.checked;
            const showGroupId = !!document.getElementById('settingsShowGroupId')?.checked;
            const normalMailLocalRetentionEnabled = !!document.getElementById('normalMailLocalRetentionEnabled')?.checked;
            const settings = {};
            const forwardChannels = getSelectedForwardChannels();

            if (password) {
                settings.login_password = password;
            }

            settings.gptmail_api_key = apiKey;
            settings.external_api_key = externalApiKey;
            settings.duckmail_base_url = document.getElementById('settingsDuckmailBaseUrl').value.trim();
            settings.duckmail_api_key = document.getElementById('settingsDuckmailApiKey').value.trim();
            settings.cloudflare_worker_domain = document.getElementById('settingsCloudflareWorkerDomain').value.trim();
            settings.cloudflare_email_domains = document.getElementById('settingsCloudflareEmailDomains').value.trim();
            settings.cloudflare_admin_password = document.getElementById('settingsCloudflareAdminPassword').value.trim();

            const days = parseInt(refreshDays, 10);
            const delay = parseInt(refreshDelay, 10);
            const forwardMinutes = parseInt(document.getElementById('forwardCheckIntervalMinutes').value || '5', 10);
            const forwardAccountDelaySeconds = parseInt(document.getElementById('forwardAccountDelaySeconds').value || '0', 10);
            const forwardWindowMinutes = parseInt(document.getElementById('forwardEmailWindowMinutes').value || '0', 10);
            const forwardIncludeJunkemail = !!document.getElementById('forwardIncludeJunkemail')?.checked;
            const smtpPortValue = document.getElementById('settingsSmtpPort').value.trim();
            const smtpPort = parseInt(smtpPortValue || '465', 10);
            const smtpRecipient = document.getElementById('settingsEmailForwardRecipient').value.trim();
            const smtpHost = document.getElementById('settingsSmtpHost').value.trim();
            const smtpProvider = normalizeSmtpForwardProvider(document.getElementById('settingsSmtpProvider')?.value || 'custom');
            const smtpFromEmail = document.getElementById('settingsSmtpFromEmail').value.trim();
            const smtpUsername = document.getElementById('settingsSmtpUsername').value.trim();
            const telegramBotToken = document.getElementById('settingsTelegramBotToken').value.trim();
            const telegramChatId = document.getElementById('settingsTelegramChatId').value.trim();
            const telegramProxyUrl = document.getElementById('settingsTelegramProxyUrl').value.trim();
            const wecomWebhookUrl = document.getElementById('settingsWecomWebhookUrl').value.trim();
            const webdavBackupSettings = getWebdavBackupFormSettings();
            const webdavBackupChanged = hasWebdavBackupSettingsChanged(webdavBackupSettings);
            const webdavBackupVerifyPassword = document.getElementById('webdavBackupVerifyPassword')?.value || '';

            if (Number.isNaN(days) || days < 1 || days > 90) {
                showToast('刷新周期必须在 1-90 天之间', 'error');
                return;
            }
            if (Number.isNaN(delay) || delay < 0 || delay > 60) {
                showToast('刷新间隔必须在 0-60 秒之间', 'error');
                return;
            }
            if (!isValidAppTimeZone(appTimeZone)) {
                showToast('Invalid time zone', 'error');
                return;
            }
            if (Number.isNaN(forwardMinutes) || forwardMinutes < 1 || forwardMinutes > 60) {
                showToast('转发轮询间隔必须在 1-60 分钟之间', 'error');
                return;
            }
            if (Number.isNaN(forwardAccountDelaySeconds) || forwardAccountDelaySeconds < 0 || forwardAccountDelaySeconds > 60) {
                showToast('账号间拉取间隔必须在 0-60 秒之间', 'error');
                return;
            }
            if (Number.isNaN(forwardWindowMinutes) || forwardWindowMinutes < 0 || forwardWindowMinutes > 10080) {
                showToast('转发邮件时间范围必须在 0-10080 分钟之间', 'error');
                return;
            }
            if (forwardChannels.includes('smtp') && !smtpRecipient) {
                showToast('启用 SMTP 转发时必须填写转发到邮箱', 'error');
                return;
            }
            if (forwardChannels.includes('smtp') && !smtpHost) {
                showToast('启用 SMTP 转发时必须填写 SMTP 主机', 'error');
                return;
            }
            if (forwardChannels.includes('smtp') && !smtpUsername && !smtpFromEmail) {
                showToast('至少需要填写 SMTP 用户名或发件人邮箱之一', 'error');
                return;
            }
            if (forwardChannels.includes('smtp') && (Number.isNaN(smtpPort) || smtpPort < 1 || smtpPort > 65535)) {
                showToast('SMTP 端口无效', 'error');
                return;
            }
            if (forwardChannels.includes('telegram') && !telegramBotToken) {
                showToast('启用 TG 转发时必须填写 Telegram Bot Token', 'error');
                return;
            }
            if (forwardChannels.includes('telegram') && !telegramChatId) {
                showToast('启用 TG 转发时必须填写 Telegram Chat ID', 'error');
                return;
            }
            if (forwardChannels.includes('wecom') && !wecomWebhookUrl) {
                showToast('启用企业微信转发时必须填写 Webhook 地址', 'error');
                return;
            }
            if (webdavBackupChanged) {
                if (!webdavBackupVerifyPassword) {
                    showToast('修改 WebDAV 备份设置需要输入登录密码', 'error');
                    return;
                }
                if (webdavBackupSettings.webdav_backup_enabled === 'true' && !webdavBackupSettings.webdav_backup_url) {
                    showToast('启用 WebDAV 备份时必须填写 WebDAV 目录 URL', 'error');
                    return;
                }
                if (webdavBackupSettings.webdav_backup_enabled === 'true') {
                    try {
                        const backupUrl = new URL(webdavBackupSettings.webdav_backup_url);
                        if (!['http:', 'https:'].includes(backupUrl.protocol)) {
                            showToast('WebDAV 目录 URL 必须是 http(s) 地址', 'error');
                            return;
                        }
                    } catch (error) {
                        showToast('WebDAV 目录 URL 无效', 'error');
                        return;
                    }
                }
                if (webdavBackupSettings.webdav_backup_enabled === 'true' && !webdavBackupSettings.webdav_backup_cron) {
                    showToast('请输入 WebDAV 备份 Cron 表达式', 'error');
                    return;
                }
            }

            settings.refresh_interval_days = days;
            settings.refresh_delay_seconds = delay;
            settings.use_cron_schedule = strategy === 'cron';
            settings.enable_scheduled_refresh = enableScheduled;
            settings.app_timezone = appTimeZone;
            settings.show_account_created_at = showAccountCreatedAt;
            settings.show_account_sort_order = showAccountSortOrder;
            settings.show_group_id = showGroupId;
            settings.normal_mail_local_retention_enabled = normalMailLocalRetentionEnabled;
            settings.forward_channels = forwardChannels;
            settings.forward_check_interval_minutes = forwardMinutes;
            settings.forward_account_delay_seconds = forwardAccountDelaySeconds;
            settings.forward_email_window_minutes = forwardWindowMinutes;
            settings.forward_include_junkemail = forwardIncludeJunkemail;
            settings.email_forward_recipient = smtpRecipient;
            settings.smtp_host = smtpHost;
            settings.smtp_port = Number.isNaN(smtpPort) ? 465 : smtpPort;
            settings.smtp_username = smtpUsername;
            settings.smtp_password = document.getElementById('settingsSmtpPassword').value;
            settings.smtp_provider = smtpProvider;
            settings.smtp_from_email = smtpFromEmail;
            settings.smtp_use_tls = document.getElementById('settingsSmtpUseTls').checked;
            settings.smtp_use_ssl = document.getElementById('settingsSmtpUseSsl').checked;
            settings.telegram_bot_token = telegramBotToken;
            settings.telegram_chat_id = telegramChatId;
            settings.telegram_proxy_url = telegramProxyUrl;
            settings.wecom_webhook_url = wecomWebhookUrl;

            if (webdavBackupChanged) {
                Object.assign(settings, webdavBackupSettings);
                settings.webdav_backup_verify_password = webdavBackupVerifyPassword;
            }

            if (strategy === 'cron') {
                if (!refreshCron) {
                    showToast('请输入 Cron 表达式', 'error');
                    return;
                }
                settings.refresh_cron = refreshCron;
            }

            const savedMessageCount = Number(lastNormalMailRetentionStatus?.saved_message_count || 0);
            const wasRetentionEnabled = isNormalMailLocalRetentionEnabled();
            let shouldClearNormalMailRetentionCache = false;
            if (wasRetentionEnabled && !normalMailLocalRetentionEnabled && savedMessageCount > 0) {
                const confirmed = await showConfirmModal(
                    '关闭普通邮箱本地保留将清理已保存的普通邮箱本地缓存数据。此操作不可恢复，是否继续？',
                    { title: '关闭普通邮箱本地保留', confirmText: '确认关闭并清理' }
                );
                if (!confirmed) {
                    const switchEl = document.getElementById('normalMailLocalRetentionEnabled');
                    if (switchEl) switchEl.checked = true;
                    return;
                }
                shouldClearNormalMailRetentionCache = true;
            }

            let data;
            try {
                const response = await fetch('/api/settings', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings)
                });

                data = await response.json();
            } catch (error) {
                showToast('保存设置失败', 'error');
                return;
            }

            if (!data.success) {
                handleApiError(data, '保存设置失败');
                return;
            }

            setAppTimeZone(appTimeZone);
            setShowAccountCreatedAt(showAccountCreatedAt);
            setShowAccountSortOrder(showAccountSortOrder);
            setShowGroupId(showGroupId);
            setNormalMailLocalRetentionEnabled(normalMailLocalRetentionEnabled);
            if (wasRetentionEnabled && !normalMailLocalRetentionEnabled
                && typeof invalidateNormalMailRetentionCaches === 'function') {
                invalidateNormalMailRetentionCaches({ resetCurrentView: true });
            }
            if (shouldClearNormalMailRetentionCache) {
                try {
                    const clearResponse = await fetch('/api/settings/normal-mail-retention/clear', { method: 'POST' });
                    const clearData = await clearResponse.json();
                    if (!clearResponse.ok || !clearData.success) {
                        throw new Error(clearData.error || '启动普通邮箱本地缓存清理失败');
                    }
                    if (typeof invalidateNormalMailRetentionCaches === 'function') {
                        invalidateNormalMailRetentionCaches({ resetCurrentView: true });
                    }
                    resetNormalMailRetentionStatusPollDelay();
                    updateNormalMailRetentionStats({
                        ...(lastNormalMailRetentionStatus || {}),
                        clear_status: clearData.status || { state: 'running', message: '正在清理普通邮箱本地缓存…' }
                    });
                    scheduleNormalMailRetentionStatusPoll();
                } catch (error) {
                    showToast('设置已保存，但启动普通邮箱本地缓存清理失败', 'warning');
                }
            } else {
                await loadNormalMailRetentionStatus({ silent: true });
            }
            try {
                await loadGroups();
                await refreshVisibleAccountList(false);
            } catch (error) {
                showToast('设置已保存，但列表刷新失败，请刷新页面', 'warning');
                hideSettingsModal();
                return;
            }

            showToast('时间展示已生效，定时任务重启后生效', 'success');
            hideSettingsModal();
        }

        function buildForwardingDraftConfig() {
            const smtpPortValue = document.getElementById('settingsSmtpPort').value.trim();
            const smtpPort = parseInt(smtpPortValue || '465', 10);
            return {
                smtp: {
                    provider: document.getElementById('settingsSmtpProvider')?.value || 'custom',
                    recipient: document.getElementById('settingsEmailForwardRecipient').value.trim(),
                    host: document.getElementById('settingsSmtpHost').value.trim(),
                    port: Number.isNaN(smtpPort) ? null : smtpPort,
                    username: document.getElementById('settingsSmtpUsername').value.trim(),
                    password: document.getElementById('settingsSmtpPassword').value,
                    from_email: document.getElementById('settingsSmtpFromEmail').value.trim(),
                    use_tls: !!document.getElementById('settingsSmtpUseTls')?.checked,
                    use_ssl: !!document.getElementById('settingsSmtpUseSsl')?.checked,
                },
                telegram: {
                    bot_token: document.getElementById('settingsTelegramBotToken').value.trim(),
                    chat_id: document.getElementById('settingsTelegramChatId').value.trim(),
                    proxy_url: document.getElementById('settingsTelegramProxyUrl').value.trim(),
                },
                wecom: {
                    webhook_url: document.getElementById('settingsWecomWebhookUrl').value.trim(),
                }
            };
        }

        async function testForwardChannel(channel) {
            const btn = document.getElementById(
                channel === 'smtp'
                    ? 'testSmtpBtn'
                    : (channel === 'telegram' ? 'testTelegramBtn' : 'testWecomBtn')
            );
            if (!btn || btn.disabled) return;

            const draft = buildForwardingDraftConfig();
            if (channel === 'smtp') {
                if (!draft.smtp.recipient) {
                    showToast('请先填写 SMTP 转发到邮箱', 'error');
                    return;
                }
                if (!draft.smtp.host) {
                    showToast('请先填写 SMTP 主机', 'error');
                    return;
                }
                if (!draft.smtp.username && !draft.smtp.from_email) {
                    showToast('请至少填写 SMTP 用户名或发件人邮箱', 'error');
                    return;
                }
                if (!draft.smtp.port || draft.smtp.port < 1 || draft.smtp.port > 65535) {
                    showToast('SMTP 端口无效', 'error');
                    return;
                }
            } else if (channel === 'telegram') {
                if (!draft.telegram.bot_token) {
                    showToast('请先填写 Telegram Bot Token', 'error');
                    return;
                }
                if (!draft.telegram.chat_id) {
                    showToast('请先填写 Telegram Chat ID', 'error');
                    return;
                }
            } else if (channel === 'wecom') {
                if (!draft.wecom.webhook_url) {
                    showToast('请先填写企业微信 Webhook 地址', 'error');
                    return;
                }
            } else {
                showToast('未知转发渠道', 'error');
                return;
            }

            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '发送中...';

            try {
                const response = await fetch('/api/settings/test-forward-channel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        channel,
                        config: draft
                    })
                });
                const data = await response.json();
                if (data.success) {
                    showToast(data.message || '测试成功', 'success');
                } else {
                    handleApiError(data, '测试失败');
                }
            } catch (error) {
                showToast('测试失败', 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        }

        function formatRelativeTime(timestamp) {
            if (!timestamp) return '从未刷新';

            const now = new Date();
            let dateStr = timestamp;
            if (typeof dateStr === 'string' && !dateStr.includes('Z') && !dateStr.includes('+') && !dateStr.includes('-', 10)) {
                dateStr = dateStr + 'Z';
            }
            const past = new Date(dateStr);
            const diffMs = now - past;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);

            if (diffMins < 1) return '刚刚';
            if (diffMins < 60) return `${diffMins} 分钟前`;
            if (diffHours < 24) return `${diffHours} 小时前`;
            if (diffDays < 30) return `${diffDays} 天前`;
            return `${Math.floor(diffDays / 30)} 月前`;
        }
