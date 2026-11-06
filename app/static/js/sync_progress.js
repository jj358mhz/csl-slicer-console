// Sync progress, shared by every page with a Sync button. A form whose
// data-sync-accounts lists [{label, url}] syncs those accounts one at a time and
// shows how far along it is. One account's sync is a single Uplynk call that
// reports no progress of its own, so only the overall bar has a real percentage;
// an account's bar just shows that it's running, then how it ended.
// Without JavaScript the forms still post normally.
window.initSyncProgress = function ({reloadUrl} = {}) {
    const modal = document.getElementById('sync-progress');
    if (!modal) return;

    const title = document.getElementById('sync-progress-title');
    const summary = document.getElementById('sync-progress-summary');
    const overall = document.getElementById('sync-progress-overall');
    const overallFill = overall.querySelector('.sync-progress__fill');
    const rows = document.getElementById('sync-progress-rows');
    const done = document.getElementById('sync-progress-done');

    function setOverall(finished, total) {
        overall.setAttribute('aria-valuenow', finished);
        overall.setAttribute('aria-valuemax', total);
        overallFill.style.width = `${total ? (finished / total) * 100 : 0}%`;
        summary.textContent = `${finished} of ${total} account${total === 1 ? '' : 's'} finished`;
    }

    function addRow(account) {
        const row = document.createElement('li');
        row.className = 'sync-progress__row';
        const label = document.createElement('div');
        label.className = 'sync-progress__label';
        label.textContent = account.label;
        const track = document.createElement('div');
        track.className = 'sync-progress__track';
        const fill = document.createElement('div');
        fill.className = 'sync-progress__fill';
        track.appendChild(fill);
        const status = document.createElement('div');
        status.className = 'sync-progress__status';
        status.textContent = 'Waiting';
        row.append(label, track, status);
        rows.appendChild(row);
        return {row, status};
    }

    async function syncOne(account, csrfToken) {
        const response = await fetch(account.url, {
            method: 'POST',
            headers: {Accept: 'application/json', 'X-CSRFToken': csrfToken},
        });
        // An expired session redirects to the login page, which isn't JSON.
        const type = response.headers.get('Content-Type') || '';
        if (!response.ok || !type.includes('application/json')) {
            throw new Error('Unexpected response. Reload the page and sign in if asked.');
        }
        return response.json();
    }

    async function run(accounts, csrfToken) {
        rows.textContent = '';
        title.textContent = accounts.length === 1 ? `Syncing ${accounts[0].label}` : 'Syncing accounts';
        done.disabled = true;
        modal.hidden = false;
        setOverall(0, accounts.length);
        const entries = accounts.map(account => ({account, ...addRow(account)}));

        let failed = 0;
        let finished = 0;
        for (const {account, row, status} of entries) {
            row.classList.add('sync-progress__row--running');
            status.textContent = 'Syncing…';
            let error = null;
            try {
                const result = await syncOne(account, csrfToken);
                error = result.error;
                if (!error) {
                    status.textContent = `${result.created} new, ${result.updated} updated, ${result.deactivated} deactivated`;
                }
            } catch (e) {
                error = e.message;
            }
            row.classList.remove('sync-progress__row--running');
            row.classList.add(error ? 'sync-progress__row--failed' : 'sync-progress__row--ok');
            if (error) {
                status.textContent = error;
                failed++;
            }
            setOverall(++finished, accounts.length);
        }

        title.textContent = failed ? 'Sync finished with problems' : 'Sync finished';
        if (failed) summary.textContent += `, ${failed} failed`;
        done.disabled = false;
        done.focus();
    }

    document.querySelectorAll('form[data-sync-accounts]').forEach(form => {
        form.addEventListener('submit', function (event) {
            // A declined confirmation has already stopped this submit.
            if (event.defaultPrevented) return;
            event.preventDefault();
            run(JSON.parse(form.dataset.syncAccounts), form.elements.csrf_token.value);
        });
    });

    done.addEventListener('click', function () {
        if (reloadUrl) location.assign(reloadUrl());
        else location.reload();
    });
};
