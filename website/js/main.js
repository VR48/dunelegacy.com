// Dune Legacy Website JavaScript

// Smooth scrolling for anchor links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// Serve a verified server-collected snapshot: no visitor API limits or CORS dependency.
async function refreshDownloadStatistics() {
    const status = document.getElementById('stats-updated');
    try {
        const response = await fetch('data/download-stats.json', {cache: 'no-store'});
        if (!response.ok) throw new Error('Statistics unavailable');
        const stats = await response.json();
        for (const key of ['total', 'year', 'month', 'day']) {
            const target = document.querySelector(`[data-stat="${key}"]`);
            if (!target) continue;
            target.textContent = stats[key] ? 'At least ' + formatCount(stats[key].total) : 'Collecting';
        }
        if (stats.day) {
            document.getElementById('stats-day-note')?.replaceChildren(document.createTextNode(
                'Since ' + new Date(stats.day.since).toLocaleString()));
        }
        const updated = new Date(stats.generated);
        const stale = Date.now() - updated.getTime() > 3 * 60 * 60 * 1000;
        if (status) {
            status.textContent = (stale ? 'Update delayed · Last successful update: ' : 'Updated hourly · Last updated: ') + updated.toLocaleString();
            status.classList.toggle('stats-stale', stale);
        }
        const source = document.getElementById('stats-sources');
        if (source) source.textContent = 'Recorded totals: SourceForge ' + formatCount(stats.total.sourceforge) + ' · GitHub at least ' + formatCount(stats.total.github) + '.';
        const table = document.getElementById('stats-years');
        if (table) {
            table.replaceChildren();
            for (const year of stats.years) {
                const row = document.createElement('tr');
                [year.year, formatCount(year.sourceforge), year.github === null ? 'Not tracked' : 'At least ' + formatCount(year.github), year.total === null ? 'Incomplete' : 'At least ' + formatCount(year.total)].forEach((value, index) => {
                    const cell = document.createElement(index === 0 ? 'th' : 'td');
                    if (index === 0) cell.scope = 'row';
                    cell.textContent = value;
                    row.append(cell);
                });
                table.append(row);
            }
        }
        document.querySelectorAll('.download-card[data-platform]').forEach(card => {
            const target = card.querySelector('.download-count');
            const count = stats.platforms[card.dataset.platform];
            if (target && Number.isFinite(count)) target.textContent = formatCount(count) + ' recorded GitHub downloads';
        });
    } catch (error) {
        if (status) status.textContent = 'Download statistics are temporarily unavailable. Please try again later.';
    }
}
document.addEventListener('DOMContentLoaded', refreshDownloadStatistics);
setInterval(refreshDownloadStatistics, 15 * 60 * 1000);

function formatCount(value) {
    return Number(value).toLocaleString();
}
