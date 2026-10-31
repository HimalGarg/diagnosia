// Helpers shared by the tool pages.
// Text from patients and from the AI always goes in through textContent,
// never innerHTML, so it can't run as code on the page.

async function apiFetch(url, options) {
    const response = await fetch(url, options);
    if (response.ok) return response;
    const data = await response.json().catch(() => ({}));
    const message = typeof data.detail === 'string'
        ? data.detail
        : 'Something went wrong. Please try again.';
    const error = new Error(message);
    error.status = response.status;
    throw error;
}

async function postJson(url, body) {
    const response = await apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return response.json();
}

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
}

function showError(node, message) {
    node.textContent = message;
    node.hidden = false;
}

function listSection(title, items) {
    const section = element('div', 'tool-panel');
    section.appendChild(element('h4', '', title));
    const list = element('ul');
    items.forEach((item) => list.appendChild(element('li', '', item)));
    section.appendChild(list);
    return section;
}

// Returns the signed-in user, or shows a message in `gate` and returns null.
async function requireAccount(gate, role) {
    const response = await fetch('/api/me').catch(() => null);
    const user = response && response.ok ? await response.json() : null;
    gate.hidden = false;
    gate.textContent = '';
    if (!user) {
        const message = element('p', '', 'Please sign in to use this tool. ');
        const link = element('a', '', 'Sign in');
        link.href = 'login.html';
        message.appendChild(link);
        gate.appendChild(message);
        return null;
    }
    if (role && user.role !== role) {
        gate.appendChild(element('p', '', 'This tool is for doctors. Please sign in with a doctor account.'));
        return null;
    }
    gate.hidden = true;
    return user;
}
