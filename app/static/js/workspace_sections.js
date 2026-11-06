// Workspace sections shared by the Dashboard and Slicer Updates: collapsible
// sections, Collapse all / Expand all, and the sticky chip nav with scrollspy.
// With persist: false every section starts open and nothing is remembered, for
// pages where a saved collapse could hide something the user must see.
window.initWorkspaceSections = function ({persist = true} = {}) {
    // ---- Collapsible sections ----
    const STORAGE_KEY = 'csl-console:collapsed-sections';
    const sections = document.querySelectorAll('.account-section');

    function loadCollapsed() {
        if (!persist) return new Set();
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return new Set(raw ? JSON.parse(raw) : []);
        } catch (_) {
            return new Set();
        }
    }

    function saveCollapsed(set) {
        if (!persist) return;
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set)));
        } catch (_) { /* quota / private mode — ignore */
        }
    }

    function applyCollapsed() {
        const collapsed = loadCollapsed();
        sections.forEach(sec => {
            const name = sec.dataset.section;
            if (collapsed.has(name)) {
                sec.classList.add('account-section--collapsed');
            } else {
                sec.classList.remove('account-section--collapsed');
            }
        });
        // A link to one slicer (#slicer-<id>, e.g. from the player) opens
        // its section for this visit without changing the saved state.
        const target = location.hash.startsWith('#slicer-')
            ? document.getElementById(location.hash.slice(1))
            : null;
        if (target) {
            target.closest('.account-section')?.classList.remove('account-section--collapsed');
            target.scrollIntoView({block: 'center'});
        }
    }

    function toggleSection(sec) {
        const name = sec.dataset.section;
        const collapsed = loadCollapsed();
        if (sec.classList.toggle('account-section--collapsed')) {
            collapsed.add(name);
        } else {
            collapsed.delete(name);
        }
        saveCollapsed(collapsed);
    }

    // Header click toggles, but clicks on the Select-all label don't.
    sections.forEach(sec => {
        const header = sec.querySelector('.account-section__header');
        if (!header) return;
        header.addEventListener('click', function (e) {
            if (e.target.closest('.section-selectall')) return;
            toggleSection(sec);
        });
    });

    // Collapse all / Expand all
    const collapseAllBtn = document.getElementById('collapse-all-btn');
    const expandAllBtn = document.getElementById('expand-all-btn');

    if (collapseAllBtn) {
        collapseAllBtn.addEventListener('click', function () {
            const collapsed = new Set();
            sections.forEach(sec => {
                sec.classList.add('account-section--collapsed');
                collapsed.add(sec.dataset.section);
            });
            saveCollapsed(collapsed);
        });
    }

    if (expandAllBtn) {
        expandAllBtn.addEventListener('click', function () {
            sections.forEach(sec => sec.classList.remove('account-section--collapsed'));
            saveCollapsed(new Set());
        });
    }

    // Initial paint
    applyCollapsed();

    // ---- Workspace chip nav (smooth-scroll + scrollspy) ----
    const chips = document.querySelectorAll('.workspace-nav__chip');

    if (chips.length && sections.length) {
        // Smooth-scroll on click
        chips.forEach(chip => {
            chip.addEventListener('click', function (e) {
                const targetId = chip.dataset.target;
                const target = document.getElementById(targetId);
                if (!target) return;
                e.preventDefault();
                // Set active immediately — observer will confirm after scroll settles
                const sectionName = target.dataset.section;
                setActiveChip(sectionName);
                target.scrollIntoView({behavior: 'smooth', block: 'start'});
            });
        });

        // Highlight the chip for the section currently in view
        function setActiveChip(sectionName) {
            chips.forEach(chip => {
                const isActive = chip.dataset.target === `section-${sectionName}`;
                chip.classList.toggle('workspace-nav__chip--active', isActive);
            });
        }

        // Track which sections are intersecting; pick the topmost one
        const visible = new Map();
        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    visible.set(entry.target.dataset.section, entry.boundingClientRect.top);
                } else {
                    visible.delete(entry.target.dataset.section);
                }
            });
            if (visible.size === 0) return;
            // Pick the section closest to the top of the viewport
            let topmost = null;
            let topmostY = Infinity;
            visible.forEach((y, name) => {
                if (y < topmostY) {
                    topmostY = y;
                    topmost = name;
                }
            });
            if (topmost) setActiveChip(topmost);
        }, {
            // Trigger when a section's top crosses ~20% down from the viewport top.
            // Bottom margin -60% means we don't count sections still near the bottom.
            rootMargin: '-20% 0px -60% 0px',
            threshold: 0,
        });

        sections.forEach(sec => observer.observe(sec));

        // Set an initial active chip based on which section is nearest the top
        const firstSection = sections[0];
        if (firstSection) setActiveChip(firstSection.dataset.section);
    }
};
