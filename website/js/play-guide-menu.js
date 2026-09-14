// Native details supplies click/touch/keyboard opening even without JavaScript.
// Add conventional dismissal without turning navigation links into an app menu.
document.querySelectorAll('.play-guide-menu').forEach((menu) => {
    const trigger = menu.querySelector('summary');
    menu.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && menu.open) {
            menu.open = false;
            trigger.focus();
            event.preventDefault();
            event.stopPropagation();
        }
    });
    menu.addEventListener('click', (event) => {
        if (event.target.closest('a')) menu.open = false;
    });
    menu.addEventListener('focusout', (event) => {
        if (!menu.contains(event.relatedTarget)) menu.open = false;
    });
    document.addEventListener('click', (event) => {
        if (!menu.contains(event.target)) menu.open = false;
    });
});
