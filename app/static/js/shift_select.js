// Shift-click a slicer checkbox to give every box between it and the last one
// clicked the same state. Shared by the Dashboard and Slicer Updates.
// A range stays inside one account, and skips boxes that are hidden or disabled,
// so it can't reach slicers you can't see or another account by accident.
window.initShiftSelect = function () {
    const BOX = '.slicer-checkbox';
    let last = null;

    const usable = box => !box.disabled && box.getClientRects().length > 0;

    // The browser would otherwise select the text between the two clicks.
    document.addEventListener('mousedown', function (event) {
        if (event.shiftKey && event.target.closest(`${BOX}, .slicer-card__select`)) {
            event.preventDefault();
        }
    });

    // The clicked box's own change event fires after this, and updates the batch bar.
    document.addEventListener('click', function (event) {
        if (event.target.closest('#batch-clear')) {
            last = null;
            return;
        }
        const box = event.target;
        if (!box.matches(BOX)) return;

        // Stopping the mousedown above also stopped focus moving here.
        if (event.shiftKey) box.focus();

        const section = box.closest('.account-section');
        if (event.shiftKey && last && last !== box && usable(last) && last.closest('.account-section') === section) {
            const boxes = Array.from(section.querySelectorAll(BOX)).filter(usable);
            const [from, to] = [boxes.indexOf(last), boxes.indexOf(box)].sort((a, b) => a - b);
            boxes.slice(from, to + 1).forEach(other => {
                other.checked = box.checked;
            });
        }
        last = box;
    });
};
