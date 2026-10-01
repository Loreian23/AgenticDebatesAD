/**
 * Agent Debate System - Frontend API Client
 * M2: Frontend/Backend Integration
 */

// Default to same-origin in production. Can be overridden globally if needed.
const API_BASE = window.__AGENTDEBATE_API_BASE__ || '';

function normalizeApiError(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map(item => {
            if (typeof item === 'string') return item;
            if (item?.msg) return item.msg;
            if (item?.message) return item.message;
            return JSON.stringify(item);
        }).join('; ');
    }
    if (typeof detail === 'object') {
        if (detail.message) return String(detail.message);
        if (detail.msg) return String(detail.msg);
        if (detail.detail) return normalizeApiError(detail.detail);
        return JSON.stringify(detail);
    }
    return String(detail);
}

// ============== API Client ==============

async function apiRequest(endpoint, options = {}) {
    const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;
    const response = await fetch(url, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        },
        ...options
    });
    
    if (!response.ok) {
        let detail = response.statusText;
        try {
            const err = await response.json();
            detail = err?.detail ?? err?.message ?? err;
        } catch {
            try {
                detail = await response.text();
            } catch {
                detail = response.statusText;
            }
        }
        const msg = normalizeApiError(detail) || `HTTP ${response.status}`;
        throw new Error(msg);
    }
    
    return response.json();
}

// ============== Debate API ==============

async function createDebate(debateData) {
    return apiRequest('/debates', {
        method: 'POST',
        body: JSON.stringify(debateData)
    });
}

async function getDebate(debateId) {
    return apiRequest(`/debates/${debateId}`);
}

async function listDebates(limit = 10) {
    return apiRequest(`/debates?limit=${limit}`);
}

async function startDebate(debateId, hostId) {
    return apiRequest(`/debates/${debateId}/start?host_id=${encodeURIComponent(hostId)}`, {
        method: 'POST'
    });
}

async function finalizeDebate(debateId, hostId) {
    return apiRequest(`/debates/${debateId}/finalize?host_id=${encodeURIComponent(hostId)}`, {
        method: 'POST'
    });
}

// ============== Participant API ==============

async function joinDebate(token, name, participantType = 'human') {
    return apiRequest('/debates/join', {
        method: 'POST',
        body: JSON.stringify({
            token,
            name,
            participant_type: participantType
        })
    });
}

// ============== Turn API ==============

async function submitTurn(debateId, participantId, content) {
    return apiRequest(`/debates/${debateId}/turns?participant_id=${encodeURIComponent(participantId)}`, {
        method: 'POST',
        body: JSON.stringify({ content })
    });
}

async function listTurns(debateId) {
    return apiRequest(`/debates/${debateId}/turns`);
}

// ============== Score API ==============

async function submitScore(debateId, judgeId, scoreData) {
    return apiRequest(`/debates/${debateId}/scores?judge_id=${encodeURIComponent(judgeId)}`, {
        method: 'POST',
        body: JSON.stringify(scoreData)
    });
}

async function listScores(debateId) {
    return apiRequest(`/debates/${debateId}/scores`);
}

// ============== Invite Token API ==============

async function createInviteToken(debateId, tokenData, createdBy) {
    return apiRequest(`/debates/${debateId}/invite-tokens`, {
        method: 'POST',
        body: JSON.stringify({
            ...tokenData,
            created_by: createdBy
        })
    });
}

async function listInviteTokens(debateId) {
    return apiRequest(`/debates/${debateId}/invite-tokens`);
}

// ============== Results API ==============

async function getResults(debateId) {
    return apiRequest(`/debates/${debateId}/results`);
}

// ============== Debate Log API ==============

async function getDebateLog(debateId) {
    return apiRequest(`/debates/${debateId}/log`);
}

async function exportDebate(debateId, format = 'json') {
    const response = await fetch(`${API_BASE}/debates/${debateId}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            format: format,
            include_scores: true,
            include_turns: true
        })
    });
    if (!response.ok) {
        throw new Error(`Export failed: HTTP ${response.status}`);
    }
    const ext = format === 'csv' ? 'csv' : format === 'markdown' ? 'md' : 'json';
    const mime = format === 'csv' ? 'text/csv' : format === 'markdown' ? 'text/markdown' : 'application/json';
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `debate_${debateId}.${ext}`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
}

// ============== UI Helpers ==============

function showError(message) {
    const errorDiv = document.getElementById('error-message');
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.style.display = 'block';
        // Ensure it's visible and scrolled into view
        errorDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        setTimeout(() => errorDiv.style.display = 'none', 5000);
    } else {
        // Fallback: inline banner — create one if none exists
        const body = document.body;
        const banner = Object.assign(document.createElement('div'), {
            className: 'message error',
            textContent: message,
            style: 'position:fixed;top:1rem;left:50%;transform:translateX(-50%);z-index:9999;max-width:90vw;'
        });
        body.appendChild(banner);
        banner.style.display = 'block';
        setTimeout(() => banner.remove(), 5000);
    }
}

function showSuccess(message) {
    const successDiv = document.getElementById('success-message');
    if (successDiv) {
        successDiv.textContent = message;
        successDiv.style.display = 'block';
        successDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        setTimeout(() => successDiv.style.display = 'none', 3000);
    }
}

function showInfo(message) {
    const infoDiv = document.getElementById('info-message') 
                 || Object.assign(document.createElement('div'), { id: 'info-message', className: 'message info' });
    infoDiv.textContent = message;
    infoDiv.style.display = 'block';
    if (!document.getElementById('info-message')) {
        infoDiv.style.marginBottom = '1rem';
        document.querySelector('.container')?.prepend(infoDiv) || document.body.prepend(infoDiv);
    }
    infoDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(() => infoDiv.style.display = 'none', 4000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function parseServerDate(value) {
    if (!value) return null;
    if (typeof value !== 'string') return new Date(value);
    const hasTimezone = /([zZ]|[+\-]\d\d:?\d\d)$/.test(value);
    const normalized = hasTimezone ? value : `${value}Z`;
    return new Date(normalized);
}

function formatDate(dateString) {
    const d = parseServerDate(dateString);
    if (!d || Number.isNaN(d.getTime())) return 'Unknown time';
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
}

function getStatusBadge(status) {
    const colors = {
        'pending': { bg: '#fef3c7', text: '#92400e' },
        'opening': { bg: '#dbeafe', text: '#1e40af' },
        'rebuttal_1': { bg: '#fce7f3', text: '#9d174d' },
        'rebuttal_2': { bg: '#fce7f3', text: '#9d174d' },
        'cross_exam': { bg: '#e0e7ff', text: '#3730a3' },
        'closing': { bg: '#f3e8ff', text: '#6b21a8' },
        'judging': { bg: '#f3e8ff', text: '#6b21a8' },
        'complete': { bg: '#d1fae5', text: '#065f46' },
        'cancelled': { bg: '#fee2e2', text: '#991b1b' }
    };
    const c = colors[status] || { bg: '#f3f4f6', text: '#374151' };
    return `<span style="background: ${c.bg}; color: ${c.text}; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 500; text-transform: uppercase;">${status}</span>`;
}

// ============== Debate Room State ==============

let currentDebate = null;
let currentParticipant = null;

function getCurrentDebateParticipant() {
    if (!currentDebate || !currentParticipant || !Array.isArray(currentDebate.participants)) return null;
    const byId = currentDebate.participants.find(p => p.id === currentParticipant.id);
    if (byId) return byId;
    const currentName = (currentParticipant.name || '').trim().toLowerCase();
    if (!currentName) return null;
    return currentDebate.participants.find(p => (p.name || '').trim().toLowerCase() === currentName) || null;
}

function hostKey(debateId) {
    return `agentdebate_host_${debateId}`;
}

function storeHostIdentity(debateId, hostId) {
    if (!debateId || !hostId) return;
    try {
        localStorage.setItem(hostKey(debateId), hostId);
    } catch (_) {
        // ignore storage failures
    }
}

function getStoredHostIdentity(debateId) {
    if (!debateId) return null;
    try {
        return localStorage.getItem(hostKey(debateId));
    } catch (_) {
        return null;
    }
}

function resolveHostId() {
    if (!currentDebate || !currentParticipant) return null;

    // Primary: exact host id match
    if (currentParticipant.id === currentDebate.created_by) {
        return currentDebate.created_by;
    }

    // Recovery: same-name host rejoined as participant
    if ((currentParticipant.name || '').trim() === (currentDebate.created_by || '').trim()) {
        return currentDebate.created_by;
    }

    // Recovery: host identity stored in browser
    const stored = getStoredHostIdentity(currentDebate.id);
    if (stored && stored === currentDebate.created_by) {
        return stored;
    }

    return null;
}
let refreshInterval = null;
let currentScores = [];
let currentResults = null;

async function loadDebateOutcomeData() {
    currentScores = [];
    currentResults = null;
    if (!currentDebate || !currentDebate.id) return;

    if (currentDebate.status === 'judging' || currentDebate.status === 'complete') {
        try {
            currentScores = await listScores(currentDebate.id);
        } catch (error) {
            console.warn('Score load failed:', error);
        }
    }

    if (currentDebate.status === 'complete') {
        try {
            currentResults = await getResults(currentDebate.id);
        } catch (error) {
            console.warn('Result load failed:', error);
        }
    }
}

// ============== Debate Room UI ==============

async function loadDebateRoom(debateId) {
    try {
        currentDebate = await getDebate(debateId);
        await loadDebateOutcomeData();

        // Recover host controls on refresh/new tab if host identity was stored.
        if (!currentParticipant) {
            const storedHost = getStoredHostIdentity(debateId);
            if (storedHost && storedHost === currentDebate.created_by) {
                currentParticipant = { id: storedHost, name: storedHost };
            }
        }

        // Switch from form view to debate room view
        const joinSection = document.getElementById('join-section');
        const createSection = document.getElementById('create-section');
        const debateRoom = document.getElementById('debate-room');
        if (joinSection) joinSection.style.display = 'none';
        if (createSection) createSection.style.display = 'none';
        if (debateRoom) debateRoom.style.display = 'block';

        renderDebateRoom();
        
        // Auto-refresh for live updates (10s interval — reduced from 3s to limit server load)
        if (refreshInterval) clearInterval(refreshInterval);
        refreshInterval = setInterval(() => refreshDebate(debateId), 10000);

        // Clear interval on page unload to prevent memory leaks
        window.addEventListener('beforeunload', () => {
            if (refreshInterval) {
                clearInterval(refreshInterval);
                refreshInterval = null;
            }
        });
        
    } catch (error) {
        showError(`Failed to load debate: ${error.message}`);
    }
}

async function refreshDebate(debateId) {
    try {
        const debate = await getDebate(debateId);
        if (JSON.stringify(debate) !== JSON.stringify(currentDebate)) {
            currentDebate = debate;
            await loadDebateOutcomeData();
            renderDebateRoom();
        }
    } catch (error) {
        console.error('Refresh error:', error);
    }
}

function renderDebateRoom() {
    if (!currentDebate) return;
    
    const container = document.getElementById('debate-room');
    if (!container) return;
    
    const d = currentDebate;
    const requiredPerSide = d.format_mode === '2v2' ? 2 : 1;
    const requiredJudges = d.judges_required || 1;
    const modeBadge = d.content_mode ? `<span>Content mode: ${escapeHtml(d.content_mode)}</span>` : '';

    container.innerHTML = `
        <div class="debate-header">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;">
                <h2>${escapeHtml(d.title)}</h2>
                <a href="/" class="btn btn-secondary" style="text-decoration:none;">Back to Home</a>
            </div>
            <p>${escapeHtml(d.proposition)}</p>
            <div class="debate-meta">
                ${getStatusBadge(d.status)}
                <span class="debate-visibility ${d.is_public ? 'public' : 'private'}" style="padding:0.2rem 0.6rem;border-radius:9999px;font-size:0.75rem;font-weight:500;${d.is_public ? 'background:#d1fae5;color:#065f46;' : 'background:#fee2e2;color:#991b1b;'}">${d.is_public ? '🌐 Public' : '🔒 Private'}</span>
                <span>Created: ${formatDate(d.created_at)}</span>
                <span>Phase: ${d.current_phase}</span>
                <span>Format: ${escapeHtml(d.format_mode || '1v1')} (${requiredPerSide}v${requiredPerSide})</span>
                <span>Roster: ${requiredPerSide} PRO, ${requiredPerSide} CON, ${requiredJudges} JUDGE</span>
                ${modeBadge}
            </div>
        </div>
        
        <div class="debate-participants">
            <h3>Participants (${d.participants.length})</h3>
            <div class="participants-grid">
                ${d.participants.map(p => `
                    <div class="participant-card ${p.side}">
                        <strong>${escapeHtml(p.name)}</strong>
                        <span class="side-badge ${p.side}">${p.side}</span>
                        <small>${p.participant_type}</small>
                    </div>
                `).join('')}
            </div>
        </div>
        
        <div class="debate-turns">
            <h3>Turns (${d.turns.length})</h3>
            <div class="turns-list">
                ${d.turns.map(t => `
                    <div class="turn-item ${t.phase}">
                        <div class="turn-header">
                            <strong>${escapeHtml(t.participant_name)}</strong>
                            <span class="turn-phase">${t.phase}</span>
                            <small>${formatDate(t.submitted_at)}</small>
                        </div>
                        <div class="turn-content">${escapeHtml(t.content)}</div>
                        ${t.char_limit_violation ? '<span class="violation">⚠️ Character limit exceeded</span>' : ''}
                    </div>
                `).join('')}
            </div>
        </div>

        ${renderScoresPanel()}
        
        ${renderActionPanel()}
        
        ${renderDebateLogPanel()}
        
        ${renderExportPanel()}
    `;
}

function renderScoresPanel() {
    if (!currentDebate) return '';
    const d = currentDebate;
    const scores = Array.isArray(currentScores) ? currentScores : [];
    if (d.status !== 'judging' && d.status !== 'complete' && scores.length === 0) {
        return '';
    }

    const participantMap = new Map((d.participants || []).map(p => [p.id, p]));
    const grouped = {};
    for (const s of scores) {
        if (!grouped[s.participant_id]) grouped[s.participant_id] = [];
        grouped[s.participant_id].push(s);
    }

    const rows = Object.entries(grouped).map(([participantId, participantScores]) => {
        const p = participantMap.get(participantId);
        const avg = (arr, key) => arr.length ? (arr.reduce((sum, x) => sum + (parseFloat(x[key]) || 0), 0) / arr.length).toFixed(2) : '0.00';
        const latestRationale = participantScores.find(x => x.rationale)?.rationale || '';
        return `
            <div class="turn-item" style="border-left-color:#10b981;">
                <div class="turn-header">
                    <strong>${escapeHtml((p && p.name) || 'Unknown')}</strong>
                    <span class="turn-phase">${escapeHtml((p && p.side) ? p.side.toUpperCase() : 'UNKNOWN')}</span>
                    <small>Judges: ${participantScores.length}</small>
                </div>
                <div style="margin-top:0.5rem;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.4rem;">
                    <span>Argument: <strong>${avg(participantScores, 'argument_quality')}</strong></span>
                    <span>Evidence: <strong>${avg(participantScores, 'evidence_quality')}</strong></span>
                    <span>Rebuttal: <strong>${avg(participantScores, 'rebuttal_strength')}</strong></span>
                    <span>Clarity: <strong>${avg(participantScores, 'clarity')}</strong></span>
                    <span>Compliance: <strong>${avg(participantScores, 'compliance')}</strong></span>
                    <span>Weighted: <strong>${avg(participantScores, 'weighted_score')}</strong></span>
                </div>
                <div style="margin-top:0.5rem;color:#4b5563;font-size:0.92rem;">
                    Judges: ${participantScores.map(s => escapeHtml(s.judge_name || 'Unknown')).join(', ')}
                </div>
                ${latestRationale ? `<div style="margin-top:0.45rem;color:#374151;font-size:0.92rem;"><strong>Sample rationale:</strong> ${escapeHtml(latestRationale)}</div>` : ''}
            </div>
        `;
    }).join('');

    const winner = currentResults?.winner ? String(currentResults.winner).toUpperCase() : null;
    const confidence = typeof currentResults?.confidence === 'number' ? `${(currentResults.confidence * 100).toFixed(1)}%` : null;
    const headline = winner
        ? `Winner: ${winner}${confidence ? ` (${confidence} confidence)` : ''}`
        : (d.status === 'complete' ? 'Debate complete' : 'Judging in progress');

    return `
        <div class="debate-turns">
            <h3>Judge Scores</h3>
            <p style="margin-bottom:0.75rem;color:#374151;">${escapeHtml(headline)}</p>
            <div class="turns-list">
                ${rows || '<p>No scores submitted yet.</p>'}
            </div>
        </div>
    `;
}

function renderDebateLogPanel() {
    if (!currentDebate) return '';
    
    return `
        <div class="debate-log-section">
            <h3>Debate Log <button class="btn btn-secondary" onclick="DebateClient.loadDebateLog()">Refresh Log</button></h3>
            <div id="debate-log-content" class="log-list">
                <p>Click "Refresh Log" to load debate events</p>
            </div>
        </div>
    `;
}

function renderExportPanel() {
    if (!currentDebate) return '';
    
    return `
        <div class="export-section">
            <h3>Export</h3>
            <button class="btn btn-secondary" onclick="DebateClient.exportDebateFlow()">Export JSON</button>
        </div>
    `;
}

function getPhasePrompt(phase) {
    const prompts = {
        opening: {
            title: 'Opening Statement',
            objective: 'Present your core case clearly and set framing.',
            bullets: [
                'State your thesis in 1-2 sentences.',
                'Give your 2-3 strongest supporting points.',
                'Define key terms or assumptions early.'
            ],
            placeholder: 'Opening: state your position, key reasons, and framing...'
        },
        rebuttal_1: {
            title: 'Rebuttal Round 1',
            objective: 'Directly answer the opponent’s strongest arguments.',
            bullets: [
                'Identify the top claim you are rebutting.',
                'Show why that claim is weak, incomplete, or unsupported.',
                'Reinforce your side with one stronger alternative.'
            ],
            placeholder: 'Rebuttal: address opponent points directly, then reinforce your case...'
        },
        rebuttal_2: {
            title: 'Rebuttal Round 2',
            objective: 'Close remaining gaps and pressure unresolved weaknesses.',
            bullets: [
                'Respond to unresolved counterarguments from round 1.',
                'Prioritize quality over quantity, focus on decisive points.',
                'Set up your closing narrative.'
            ],
            placeholder: 'Rebuttal 2: resolve remaining objections and strengthen your final line...'
        },
        cross_exam: {
            title: 'Cross Examination',
            objective: 'Challenge assumptions and force precise answers.',
            bullets: [
                'Ask or answer focused, testable points.',
                'Expose contradictions or missing evidence.',
                'Keep it concise and high signal.'
            ],
            placeholder: 'Cross-exam: ask/answer directly, test assumptions, expose weak links...'
        },
        closing: {
            title: 'Closing Statement',
            objective: 'Summarize why your side wins on the key decision criteria.',
            bullets: [
                'Recap the 2-3 decisive points only.',
                'Explain why opponent rebuttals failed on those points.',
                'End with a clear final judgment statement.'
            ],
            placeholder: 'Closing: summarize decisive points and final judgment...'
        }
    };

    return prompts[phase] || {
        title: 'Current Phase',
        objective: 'Follow the phase objective and keep your turn focused.',
        bullets: ['Be clear and concise.', 'Address the most relevant points.'],
        placeholder: 'Enter your argument...'
    };
}

function renderPhasePromptCard(phase) {
    const p = getPhasePrompt(phase);
    return `
        <div class="phase-prompt-card">
            <h4>${escapeHtml(p.title)}</h4>
            <p><strong>Objective:</strong> ${escapeHtml(p.objective)}</p>
            <ul>
                ${p.bullets.map(b => `<li>${escapeHtml(b)}</li>`).join('')}
            </ul>
        </div>
    `;
}

async function loadDebateLog() {
    if (!currentDebate) return;
    
    try {
        const logData = await getDebateLog(currentDebate.id);
        const logContainer = document.getElementById('debate-log-content');
        
        if (!logData.log || logData.log.length === 0) {
            logContainer.innerHTML = '<p>No events yet</p>';
            return;
        }
        
        logContainer.innerHTML = logData.log.map(entry => `
            <div class="log-item ${entry.event_type}">
                <div class="log-header">
                    <span class="log-timestamp">${formatDate(entry.timestamp)}</span>
                    <span class="log-event">${entry.event_type}</span>
                    <span class="log-actor">${escapeHtml(entry.actor || 'system')}</span>
                </div>
                <div class="log-data">
                    ${Object.entries(entry.data || {}).map(([k, v]) => 
                        `<span class="log-field">${k}: ${typeof v === 'object' ? JSON.stringify(v) : escapeHtml(String(v))}</span>`
                    ).join('')}
                </div>
            </div>
        `).join('');
        
    } catch (error) {
        showError(`Failed to load log: ${error.message}`);
    }
}

async function exportDebateFlow() {
    if (!currentDebate) {
        showError('No debate loaded');
        return;
    }
    
    try {
        await exportDebate(currentDebate.id);
        showSuccess('Debate exported!');
    } catch (error) {
        showError(`Export failed: ${error.message}`);
    }
}

function renderActionPanel() {
    if (!currentDebate) return '';
    
    const d = currentDebate;
    
    // Viewer hasn't joined — show join form for public, joinable debates
    if (!currentParticipant) {
        const canJoin = d.is_public && d.status !== 'complete' && d.status !== 'cancelled';
        if (!canJoin) {
            // Private debate or ended — show read-only notice
            const reason = !d.is_public ? 'This is a private debate. An invite token is required.' :
                'This debate has ended.';
            return `
                <div class="action-panel" style="text-align:center;">
                    <h3>🔒 ${d.is_public ? 'Debate Ended' : 'Private Debate'}</h3>
                    <p style="color:#6b7280;">${reason}</p>
                    <p style="font-size:0.875rem;color:#9ca3af;">You can view turns and scores above.</p>
                </div>
            `;
        }
        
        // Public, joinable — show join form
        const requiredPerSide = d.format_mode === '2v2' ? 2 : 1;
        const counts = {
            pro: (d.participants || []).filter(x => x.side === 'pro').length,
            con: (d.participants || []).filter(x => x.side === 'con').length,
            judge: (d.participants || []).filter(x => x.side === 'judge').length,
        };
        const canJoinPro = counts.pro < requiredPerSide;
        const canJoinCon = counts.con < requiredPerSide;
        const canJoinJudge = counts.judge < (d.judges_required || 1);
        const canJoinAny = canJoinPro || canJoinCon || canJoinJudge;
        
        if (!canJoinAny && d.status === 'pending') {
            return `
                <div class="action-panel" style="text-align:center;">
                    <h3>✅ Roster Full</h3>
                    <p style="color:#6b7280;">All slots filled (${counts.pro} PRO / ${counts.con} CON / ${counts.judge} JUDGE).</p>
                    <p style="font-size:0.875rem;color:#9ca3af;">Waiting for host to start the debate.</p>
                </div>
            `;
        }
        
        const slotOptions = [];
        if (canJoinPro) slotOptions.push(`<option value="pro">PRO (${counts.pro}/${requiredPerSide} filled)</option>`);
        if (canJoinCon) slotOptions.push(`<option value="con">CON (${counts.con}/${requiredPerSide} filled)</option>`);
        if (canJoinJudge) slotOptions.push(`<option value="judge">JUDGE (${counts.judge}/${d.judges_required || 1} filled)</option>`);
        
        return `
            <div class="action-panel" style="border: 2px solid #3b82f6; background: #eff6ff;">
                <h3 style="color: #1d4ed8;">👋 Join This Debate</h3>
                <p style="color:#374151;margin-bottom:1rem;">This is a public debate — no invite token needed.</p>
                <div class="form-group" style="margin-bottom:0.75rem;">
                    <label for="room-join-name" style="display:block;margin-bottom:0.25rem;font-weight:500;">Your Name</label>
                    <input type="text" id="room-join-name" placeholder="Enter your name" style="width:100%;padding:0.75rem;border:1px solid #93c5fd;border-radius:6px;font-size:1rem;">
                </div>
                <div class="form-group" style="margin-bottom:1rem;">
                    <label for="room-join-role" style="display:block;margin-bottom:0.25rem;font-weight:500;">Role</label>
                    <select id="room-join-role" style="width:100%;padding:0.75rem;border:1px solid #93c5fd;border-radius:6px;font-size:1rem;">
                        ${slotOptions.join('')}
                    </select>
                </div>
                <button class="btn btn-primary" onclick="DebateClient.joinFromRoom()" style="font-size:1.1rem;padding:0.75rem 2rem;">
                    🚀 Join Debate
                </button>
            </div>
        `;
    }
    
    const p = getCurrentDebateParticipant() || currentParticipant;
    const hostId = resolveHostId();
    
    // Host controls
    if (hostId && d.status === 'pending') {
        const requiredPerSide = d.format_mode === '2v2' ? 2 : 1;
        const requiredJudges = d.judges_required || 1;
        const counts = {
            pro: (d.participants || []).filter(x => x.side === 'pro').length,
            con: (d.participants || []).filter(x => x.side === 'con').length,
            judge: (d.participants || []).filter(x => x.side === 'judge').length,
        };
        const missing = [];
        if (counts.pro < requiredPerSide) missing.push(`PRO ${counts.pro}/${requiredPerSide}`);
        if (counts.con < requiredPerSide) missing.push(`CON ${counts.con}/${requiredPerSide}`);
        if (counts.judge < requiredJudges) missing.push(`JUDGE ${counts.judge}/${requiredJudges}`);
        const canStart = counts.pro >= requiredPerSide && counts.con >= requiredPerSide && counts.judge >= requiredJudges;
        return `
            <div class="action-panel">
                <h3>Host Controls</h3>
                <p class="text-muted">${canStart ? 'Ready to start.' : `Roster incomplete. Missing: ${missing.join(', ')}`}</p>
                <button class="btn btn-primary" onclick="startDebateFlow()" ${canStart ? '' : 'disabled'}>Start Debate</button>
                <button class="btn btn-secondary" onclick="showInviteTokens()">Manage Invite Tokens</button>
            </div>
        `;
    }
    
    // Host finalize control
    if (hostId && d.status === 'judging') {
        const judges = (d.participants || []).filter(x => x.side === 'judge');
        const expectedScores = ((d.participants || []).filter(x => x.side === 'pro' || x.side === 'con').length) * judges.length;
        const actualScores = Array.isArray(d.scores) ? d.scores.length : 0;
        const missing = Math.max(expectedScores - actualScores, 0);

        return `
            <div class="action-panel">
                <h3>Host Controls</h3>
                <p style="margin-bottom:0.75rem;color:${missing ? '#991b1b' : '#065f46'};">Scoring progress: ${actualScores}/${expectedScores}${missing ? ` (${missing} missing)` : ' (complete)'}</p>
                <div style="margin-bottom:1rem;">
                    ${renderJudgeScoringForm(true)}
                </div>
                <button class="btn btn-primary" onclick="finalizeDebateFlow()">Finalize Debate</button>
            </div>
        `;
    }
    
    // Debater turn submission
    if ((p.side === 'pro' || p.side === 'con') && 
        ['opening', 'rebuttal_1', 'rebuttal_2', 'cross_exam', 'closing'].includes(d.status)) {
        const phasePrompt = getPhasePrompt(d.status);
        const mode = d.content_mode || 'simple';
        const minChars = d.min_turn_chars || Math.max(80, Math.floor((d.max_turn_length || 1000) * (mode === 'rich' ? 0.6 : 0.2)));
        const qualityHint = mode === 'rich'
            ? 'Rich mode: use stronger evidence, deeper reasoning, and directly rebut specific opponent claims. Web search and high-thinking mode are recommended.'
            : 'Simple mode: keep it concise but structured. Still rebut at least one concrete opponent point.';
        return `
            <div class="action-panel">
                <h3>Your Turn</h3>
                ${renderPhasePromptCard(d.status)}
                <p style="margin:0.5rem 0;color:#374151;"><strong>Length guard:</strong> ${minChars}-${d.max_turn_length} characters (${escapeHtml(mode)} mode).</p>
                <p style="margin:0.25rem 0 0.75rem;color:#4b5563;">${escapeHtml(qualityHint)}</p>
                <textarea id="turn-content" placeholder="${escapeHtml(phasePrompt.placeholder)}" maxlength="${d.max_turn_length}"></textarea>
                <small id="char-count">0 / ${d.max_turn_length} characters</small>
                <button class="btn btn-primary" onclick="submitTurnFlow()">Submit Turn</button>
            </div>
        `;
    }
    
    // Judge scoring
    if (p.side === 'judge' && d.status === 'judging') {
        return `
            <div class="action-panel">
                <h3>Submit Scores</h3>
                ${renderJudgeScoringForm(false)}
            </div>
        `;
    }
    
    if (d.status === 'pending') {
        const requiredPerSide = d.format_mode === '2v2' ? 2 : 1;
        const requiredJudges = d.judges_required || 1;
        const counts = {
            pro: (d.participants || []).filter(x => x.side === 'pro').length,
            con: (d.participants || []).filter(x => x.side === 'con').length,
            judge: (d.participants || []).filter(x => x.side === 'judge').length,
        };
        const missing = [];
        if (counts.pro < requiredPerSide) missing.push(`PRO ${counts.pro}/${requiredPerSide}`);
        if (counts.con < requiredPerSide) missing.push(`CON ${counts.con}/${requiredPerSide}`);
        if (counts.judge < requiredJudges) missing.push(`JUDGE ${counts.judge}/${requiredJudges}`);
        const missingText = missing.length ? ` Missing: ${missing.join(', ')}.` : '';
        return `
            <div class="action-panel">
                <h3>Waiting to Start</h3>
                <p>Waiting for host to start debate.${missingText}</p>
            </div>
        `;
    }

    return '';
}

function renderJudgeScoringForm(hostMode = false) {
    if (!currentDebate) return '';
    
    const debaters = currentDebate.participants.filter(p => p.side === 'pro' || p.side === 'con');
    const judges = currentDebate.participants.filter(p => p.side === 'judge');
    const currentDebateParticipant = getCurrentDebateParticipant();
    const canScoreDirectly = currentDebateParticipant?.side === 'judge';

    const judgeSelector = (!canScoreDirectly && hostMode && judges.length > 0)
        ? `<div class="form-group" style="margin-bottom: 1rem;">
                <label for="score-judge-select">Score as Judge</label>
                <select id="score-judge-select" style="width:100%;padding:0.5rem;border:1px solid #d1d5db;border-radius:6px;">
                    ${judges.map(j => `<option value="${j.id}">${escapeHtml(j.name)} (${j.id.slice(0,8)})</option>`).join('')}
                </select>
           </div>`
        : '';

    if (debaters.length === 0) {
        return '<p>No PRO/CON participants found to score yet.</p>';
    }
    
    return `${judgeSelector}${debaters.map(d => `
        <div class="score-form" data-participant-id="${d.id}">
            <h4>${escapeHtml(d.name)} (${d.side.toUpperCase()} TEAM)</h4>
            <div class="score-inputs">
                <label>Argument Quality (0-10): <input type="number" class="score-arg" min="0" max="10" step="0.5" value="7"></label>
                <label>Evidence Quality (0-10): <input type="number" class="score-evi" min="0" max="10" step="0.5" value="7"></label>
                <label>Rebuttal Strength (0-10): <input type="number" class="score-reb" min="0" max="10" step="0.5" value="7"></label>
                <label>Clarity (0-10): <input type="number" class="score-cla" min="0" max="10" step="0.5" value="7"></label>
                <label>Compliance (0-10): <input type="number" class="score-com" min="0" max="10" step="0.5" value="7"></label>
            </div>
            <textarea class="score-rationale" placeholder="Rationale for scoring..."></textarea>
        </div>
    `).join('')}
        <button class="btn btn-primary" onclick="submitScoresFlow()">Submit All Scores</button>
    `;
}

// ============== Flow Functions ==============

async function startDebateFlow() {
    if (!currentDebate || !currentParticipant) {
        showError('No debate or participant loaded');
        return;
    }
    
    const hostId = resolveHostId();
    // Verify this participant is the host
    if (!hostId) {
        showError('Only the host can start the debate');
        return;
    }
    
    try {
        const result = await startDebate(currentDebate.id, hostId);
        showSuccess('Debate started!');
        currentDebate = result;
        renderDebateRoom();
    } catch (error) {
        showError(`Failed to start: ${error.message}`);
    }
}

async function finalizeDebateFlow() {
    if (!currentDebate || !currentParticipant) return;

    const hostId = resolveHostId();
    if (!hostId) {
        showError('Only the host can finalize this debate');
        return;
    }
    
    try {
        const result = await finalizeDebate(currentDebate.id, hostId);
        showSuccess(`Debate finalized! Winner: ${result.winner_side}`);
        loadDebateRoom(currentDebate.id);
        showResults(result);
    } catch (error) {
        showError(`Failed to finalize: ${error.message}`);
    }
}

async function submitTurnFlow() {
    if (!currentDebate || !currentParticipant) return;
    
    const content = document.getElementById('turn-content').value.trim();
    if (!content) {
        showError('Please enter content for your turn');
        return;
    }
    
    try {
        await submitTurn(currentDebate.id, currentParticipant.id, content);
        showSuccess('Turn submitted!');
        document.getElementById('turn-content').value = '';
        loadDebateRoom(currentDebate.id);
    } catch (error) {
        showError(`Failed to submit: ${error.message}`);
    }
}

async function submitScoresFlow() {
    if (!currentDebate || !currentParticipant) return;

    const debateParticipant = getCurrentDebateParticipant();
    let judgeId = debateParticipant?.side === 'judge' ? debateParticipant.id : null;

    if (!judgeId) {
        const hostId = resolveHostId();
        if (!hostId) {
            showError('Only a judge (or host selecting a judge) can submit scores');
            return;
        }
        const selectedJudge = document.getElementById('score-judge-select');
        judgeId = selectedJudge && selectedJudge.value ? selectedJudge.value : null;
        if (!judgeId) {
            showError('Select a judge identity before submitting scores');
            return;
        }
    }
    
    const scoreForms = document.querySelectorAll('.score-form');
    
    for (const form of scoreForms) {
        const participantId = form.dataset.participantId;
        const scoreData = {
            participant_id: participantId,
            argument_quality: parseFloat(form.querySelector('.score-arg').value),
            evidence_quality: parseFloat(form.querySelector('.score-evi').value),
            rebuttal_strength: parseFloat(form.querySelector('.score-reb').value),
            clarity: parseFloat(form.querySelector('.score-cla').value),
            compliance: parseFloat(form.querySelector('.score-com').value),
            rationale: form.querySelector('.score-rationale').value
        };
        
        try {
            await submitScore(currentDebate.id, judgeId, scoreData);
        } catch (error) {
            showError(`Failed to submit score for ${participantId}: ${error.message}`);
            return;
        }
    }
    
    showSuccess('All scores submitted!');
    loadDebateRoom(currentDebate.id);
}

function showResults(debate) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <h2>Debate Results</h2>
            <div class="winner-announcement">
                <h3>Winner: ${debate.winner_side ? debate.winner_side.toUpperCase() : 'TIE'}</h3>
                <p>Confidence: ${(debate.confidence_score * 100).toFixed(1)}%</p>
            </div>
            <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Close</button>
        </div>
    `;
    document.body.appendChild(modal);
}

// ============== Join Flow ==============

async function joinDebateFlow() {
    const token = document.getElementById('join-token').value.trim();
    const name = document.getElementById('join-name').value.trim();
    const mode = (document.getElementById('join-participant-type')?.value || 'auto').toLowerCase();
    
    if (!token || !name) {
        showError('Please enter both token and name');
        return;
    }
    
    // Show loading state on button
    const btn = document.querySelector('button[onclick="DebateClient.joinDebateFlow()"]') 
             || document.querySelector('#join-section button') 
             || document.createElement('button');
    const btnText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Joining…';

    try {
        let result;
        let joinedType = mode;

        if (mode === 'human' || mode === 'agent') {
            result = await joinDebate(token, name, mode);
        } else {
            // Auto mode: try human first, then agent for agent-scoped tokens.
            try {
                result = await joinDebate(token, name, 'human');
                joinedType = 'human';
            } catch (err) {
                const msg = String(err?.message || '').toLowerCase();
                if (msg.includes('participant type')) {
                    result = await joinDebate(token, name, 'agent');
                    joinedType = 'agent';
                } else {
                    throw err;
                }
            }
        }

        // Resolve joined participant details (including side) for turn submission UI.
        const joinedDebate = await getDebate(result.debate_id);
        const joinedParticipant = (joinedDebate.participants || []).find(p => p.id === result.participant_id);

        currentDebate = joinedDebate;
        currentParticipant = {
            id: result.participant_id,
            name: name,
            side: joinedParticipant?.side,
            participant_type: joinedParticipant?.participant_type,
        };

        // Allow host control recovery when host rejoins by same identity.
        if ((name || '').trim() === (joinedDebate.created_by || '').trim()) {
            storeHostIdentity(joinedDebate.id, joinedDebate.created_by);
        }

        if (mode === 'auto' && joinedType === 'agent') {
            showSuccess('Joined successfully (agent token type matched automatically).');
        } else {
            showSuccess('Joined successfully!');
        }
        loadDebateRoom(result.debate_id);
    } catch (error) {
        showError(`Failed to join: ${error.message}`);
    } finally {
        // Restore button
        btn.disabled = false;
        btn.textContent = btnText;
    }
}

async function joinFromRoom() {
    if (!currentDebate) {
        showError('No debate loaded');
        return;
    }
    
    const name = document.getElementById('room-join-name')?.value.trim();
    if (!name) {
        showError('Please enter your name');
        return;
    }
    
    const role = document.getElementById('room-join-role')?.value || 'auto';
    
    // Show loading
    const btn = document.querySelector('button[onclick="DebateClient.joinFromRoom()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Joining…'; }
    
    try {
        const response = await fetch(`${API_BASE}/api/agents/join`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                debate_id: currentDebate.id,
                agent_name: name,
                model: 'human-participant',
                preferred_role: role,
                mode: 'auto'
            })
        });
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        
        const result = await response.json();
        
        // Set current participant from join result
        currentParticipant = {
            id: result.participant_id,
            name: name,
            side: result.assigned_role
        };
        
        // Store for recovery
        sessionStorage.setItem('ad_token', result.token);
        sessionStorage.setItem('ad_debate_id', result.debate_id);
        sessionStorage.setItem('ad_participant_id', result.participant_id);
        
        // Store host identity if this user created the debate
        const debate = await getDebate(currentDebate.id);
        if ((name || '').trim() === (debate.created_by || '').trim()) {
            storeHostIdentity(debate.id, debate.created_by);
        }
        
        showSuccess(`Joined as ${result.assigned_role}!`);
        loadDebateRoom(currentDebate.id);
    } catch (error) {
        showError(`Join failed: ${error.message}`);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🚀 Join Debate'; }
    }
}

async function autoJoinFlow() {
    const name = document.getElementById('join-name').value.trim();
    if (!name) {
        showError('Please enter your name');
        return;
    }
    try {
        // Call the agent join API — auto-selects latest public debate
        const response = await fetch(`${API_BASE}/api/agents/join`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                agent_name: name,
                model: 'auto-join',
                preferred_role: 'auto',
                mode: 'auto'
            })
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        const result = await response.json();
        showSuccess(`Joined as ${result.assigned_role}!`);
        
        // Store the bearer token so the worker can use it
        sessionStorage.setItem('ad_token', result.token);
        sessionStorage.setItem('ad_debate_id', result.debate_id);
        sessionStorage.setItem('ad_participant_id', result.participant_id);
        
        loadDebateRoom(result.debate_id);
    } catch (error) {
        showError(`Quick join failed: ${error.message}. Make sure a public debate is open.`);
    }
}

// ============== Create Debate Flow ==============

function getContentModeDefaults(mode) {
    if (mode === 'rich') {
        return {
            minTurnRatio: 0.60,
            targetMinRatio: 0.75,
            targetIdealRatio: 0.92,
            webSearch: 'recommended',
            highThinking: 'recommended',
        };
    }
    return {
        minTurnRatio: 0.20,
        targetMinRatio: 0.45,
        targetIdealRatio: 0.75,
        webSearch: 'optional',
        highThinking: 'optional',
    };
}

function buildCreateGuidancePreviewData() {
    const maxLength = parseInt(document.getElementById('create-max-length')?.value || '1000') || 1000;
    const contentMode = (document.getElementById('create-content-mode')?.value || 'rich').trim();
    const turnTime = parseInt(document.getElementById('create-turn-time')?.value || '360') || 360;
    const judgeMultiplier = parseFloat(document.getElementById('create-judge-time-multiplier')?.value || '1.5') || 1.5;
    const cfg = getContentModeDefaults(contentMode);
    return {
        content_mode: contentMode,
        max_turn_length: maxLength,
        max_turn_time_seconds: turnTime,
        judge_time_multiplier: Number(judgeMultiplier.toFixed(2)),
        turn_quality_instructions: {
            length_targets: {
                enforced_min_chars: Math.max(80, Math.floor(maxLength * cfg.minTurnRatio)),
                target_min_chars: Math.floor(maxLength * cfg.targetMinRatio),
                target_ideal_chars: Math.floor(maxLength * cfg.targetIdealRatio),
                max_chars: maxLength,
            },
            research_and_reasoning: {
                web_search: cfg.webSearch,
                high_thinking: cfg.highThinking,
                notes: [
                    'Directly rebut at least one specific opponent claim.',
                    'Use concrete evidence when available and do not fabricate citations.',
                ],
            },
        },
    };
}

function updateCreateGuidancePreview() {
    const preview = document.getElementById('create-guidance-preview');
    if (!preview) return;
    preview.textContent = JSON.stringify(buildCreateGuidancePreviewData(), null, 2);
}

async function createDebateFlow() {
    const title = document.getElementById('create-title').value.trim();
    const proposition = document.getElementById('create-proposition').value.trim();
    const createdBy = document.getElementById('create-host').value.trim() || 'anonymous';
    const formatMode = (document.getElementById('create-format-mode')?.value || '1v1').trim();
    const turnTimeSeconds = parseInt(document.getElementById('create-turn-time')?.value || '360') || 360;
    const contentMode = (document.getElementById('create-content-mode')?.value || 'rich').trim();
    const judgeTimeMultiplier = parseFloat(document.getElementById('create-judge-time-multiplier')?.value || '1.5') || 1.5;
    const isPublic = document.getElementById('create-is-public')?.checked ?? false;
    const cfg = getContentModeDefaults(contentMode);
    
    if (!title || !proposition) {
        showError('Please enter both title and proposition');
        return;
    }
    
    // Show loading state on button
    const btn = document.querySelector('button[onclick="DebateClient.createDebateFlow()"]') 
             || document.querySelector('#create-section button') 
             || document.createElement('button');
    const btnText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Creating…';

    try {
        const debate = await createDebate({
            title,
            proposition,
            created_by: createdBy,
            max_turn_length: parseInt(document.getElementById('create-max-length').value) || 1000,
            max_turn_time_seconds: turnTimeSeconds,
            format_mode: formatMode,
            judges_required: 1,
            content_mode: contentMode,
            min_turn_ratio: cfg.minTurnRatio,
            judge_time_multiplier: judgeTimeMultiplier,
            is_public: isPublic
        });
        
        currentParticipant = { id: createdBy, name: createdBy };
        storeHostIdentity(debate.id, createdBy);
        showSuccess('Debate created!');
        
        // Create invite tokens for all sides (capture full token values on creation)
        const sideSlots = formatMode === '2v2' ? 2 : 1;
        const proToken = await createInviteToken(debate.id, { side: 'pro', max_uses: sideSlots }, createdBy);
        const conToken = await createInviteToken(debate.id, { side: 'con', max_uses: sideSlots }, createdBy);
        const judgeToken = await createInviteToken(debate.id, { side: 'judge', max_uses: 1 }, createdBy);
        
        loadDebateRoom(debate.id);
        showInviteTokens([proToken, conToken, judgeToken]);
        
    } catch (error) {
        showError(`Failed to create: ${error.message}`);
    } finally {
        // Restore button
        btn.disabled = false;
        btn.textContent = btnText;
    }
}

async function showInviteTokens(createdTokens = null) {
    if (!currentDebate && !createdTokens) return;
    
    try {
        const tokens = createdTokens || await listInviteTokens(currentDebate.id);
        
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.innerHTML = '<div class="modal-content" style="max-width: 700px;">' +
            '<h2>Invite Tokens</h2>' +
            '<p>Share these tokens with participants. Use the Copy button to share them.</p>' +
            '<div class="tokens-list">' +
            tokens.map(t => {
                const displayToken = t.token || t.token_preview || '(no token)';
                const safeToken = (displayToken || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
                const canCopy = !!t.token;
                return '<div class="token-item" style="flex-direction:column;align-items:flex-start;gap:6px;">' +
                    '<strong>' + t.side.toUpperCase() + '</strong>' +
                    '<code style="word-break:break-all;display:block;background:#e5e7eb;padding:6px 10px;border-radius:4px;font-family:monospace;font-size:0.85em;width:100%;max-width:100%;overflow-wrap:anywhere;">' + displayToken + '</code>' +
                    '<div style="display:flex;gap:8px;align-items:center;">' +
                    '<small>Uses: ' + (t.used_count || 0) + '/' + t.max_uses + '</small>' +
                    (canCopy ? '<button class="btn btn-secondary" onclick="DebateClient.copyToken(\'' + safeToken + '\')">Copy</button>' : '') +
                    '</div></div>';
            }).join('') +
            '</div>' +
            '<button class="btn btn-secondary" onclick="this.closest(\'.modal\').remove()">Close</button>' +
            '</div>';
        document.body.appendChild(modal);
        
    } catch (error) {
        showError(`Failed to load tokens: ${error.message}`);
    }
}

function copyToken(token) {
    if (!token) {
        showError('No token to copy');
        return;
    }
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(token)
            .then(() => showSuccess('Token copied'))
            .catch(() => showError('Copy failed, please copy manually'));
        return;
    }
    const ta = document.createElement('textarea');
    ta.value = token;
    document.body.appendChild(ta);
    ta.select();
    try {
        document.execCommand('copy');
        showSuccess('Token copied');
    } catch {
        showError('Copy failed, please copy manually');
    }
    document.body.removeChild(ta);
}

function showCreateSection() {
    const joinSection = document.getElementById('join-section');
    const debateRoom = document.getElementById('debate-room');
    if (debateRoom) debateRoom.style.display = 'none';
    if (joinSection) joinSection.style.display = 'block';
    const el = document.getElementById('create-title');
    if (el) el.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showJoinSection() {
    const joinSection = document.getElementById('join-section');
    const debateRoom = document.getElementById('debate-room');
    if (debateRoom) debateRoom.style.display = 'none';
    if (joinSection) joinSection.style.display = 'block';
    const el = document.getElementById('join-token');
    if (el) el.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function viewDebatesFlow() {
    try {
        const debates = await listDebates();
        const modalId = `debate-list-${Date.now()}`;
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.id = modalId;

        const rows = (debates || []).slice(0, 20).map(d => `
            <div class="debate-item" style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin:8px 0;padding:10px;border:1px solid #eee;border-radius:8px;">
                <div>
                    <strong>${escapeHtml(d.title)}</strong><br>
                    <small>Status: ${escapeHtml(d.status)} &bull; Participants: ${d.participant_count} &bull; Turns: ${d.turn_count}</small>
                </div>
                <button class="btn btn-secondary" data-debate-id="${escapeHtml(d.id)}" data-modal-id="${escapeHtml(modalId)}">Open</button>
            </div>
        `).join('');

        modal.innerHTML = `
            <div class="modal-content" style="max-width: 820px;" role="dialog" aria-modal="true" aria-labelledby="debate-list-title">
                <h2 id="debate-list-title">View Debates</h2>
                ${rows || '<p>No debates found yet.</p>'}
                <button class="btn btn-secondary modal-close-btn">Close</button>
            </div>
        `;
        document.body.appendChild(modal);

        // Keyboard: close on Escape
        modal.addEventListener('keydown', e => { if (e.key === 'Escape') modal.remove(); });
        modal.querySelector('.modal-close-btn').focus();

        // Event delegation for Open buttons — avoids inline onclick XSS
        modal.querySelectorAll('[data-debate-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                const debateId = btn.dataset.debateId;
                const mId = btn.dataset.modalId;
                const m = document.getElementById(mId);
                if (m) m.remove();
                loadDebateRoom(debateId);
            });
        });
    } catch (error) {
        showError(`Failed to load debates: ${error.message}`);
    }
}

function openDebateFromList(debateId, modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.remove();
    loadDebateRoom(debateId);
}

// Export for use in other scripts

// Load public debates onto the join page
async function loadPublicDebatesForJoin() {
    const container = document.getElementById('public-debates-list');
    if (!container) return;
    try {
        const debates = await listDebates(20);
        // Filter to public + active only (not complete or cancelled)
        const openDebates = debates.filter(d =>
            d.is_public && d.status !== 'complete' && d.status !== 'cancelled'
        );
        if (openDebates.length === 0) {
            container.innerHTML = '<p style="color: #9ca3af;">No open public debates right now.</p>';
            return;
        }
        container.innerHTML = openDebates.map(d => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:0.75rem;margin-bottom:0.5rem;background:white;border:1px solid #e5e7eb;border-radius:8px;">
                <div>
                    <strong>${escapeHtml(d.title)}</strong>
                    <span style="color:#6b7280;margin-left:0.75rem;font-size:0.875rem;">${d.participant_count} participants • ${d.turn_count} turns</span>
                    <br><span style="color:#9ca3af;font-size:0.8rem;">${escapeHtml(d.proposition.substring(0, 80))}${d.proposition.length > 80 ? '...' : ''}</span>
                </div>
                <div style="flex-shrink:0;">
                    <span style="padding:0.2rem 0.6rem;border-radius:9999px;font-size:0.75rem;font-weight:500;${getStatusBg(d.status)}">${d.status}</span>
                    <a href="/debate-room?id=${d.id}" class="btn btn-secondary" style="margin-left:0.5rem;text-decoration:none;">View</a>
                </div>
            </div>
        `).join('');
    } catch (error) {
        container.innerHTML = '<p style="color: #9ca3af;">Could not load debates.</p>';
    }
}

function getStatusBg(status) {
    const colors = {
        pending: 'background:#e0e7ff;color:#3730a3',
        opening: 'background:#dbeafe;color:#1e40af',
        rebuttal_1: 'background:#fef3c7;color:#92400e',
        rebuttal_2: 'background:#fef3c7;color:#92400e',
        closing: 'background:#fce7f3;color:#9d174d',
        judging: 'background:#ede9fe;color:#5b21b6',
        complete: 'background:#d1fae5;color:#065f46',
        cancelled: 'background:#fee2e2;color:#991b1b',
    };
    return colors[status] || 'background:#f3f4f6;color:#374151';
}

window.DebateClient = {
    createDebate,
    getDebate,
    listDebates,
    startDebate,
    finalizeDebate,
    joinDebate,
    submitTurn,
    listTurns,
    submitScore,
    listScores,
    createInviteToken,
    listInviteTokens,
    getResults,
    getDebateLog,
    exportDebate,
    loadDebateRoom,
    createDebateFlow,
    copyToken,
    showCreateSection,
    showJoinSection,
    viewDebatesFlow,
    openDebateFromList,
    joinDebateFlow,
    joinFromRoom,
    autoJoinFlow,
    startDebateFlow,
    finalizeDebateFlow,
    submitTurnFlow,
    submitScoresFlow,
    loadDebateLog,
    exportDebateFlow,
    updateCreateGuidancePreview,
    loadPublicDebatesForJoin,
};
