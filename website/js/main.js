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
            target.textContent = stats[key] ? formatCount(stats[key].total) : 'Unavailable';
        }
        if (stats.day) {
            const seconds = (new Date(stats.generated) - new Date(stats.day.since)) / 1000;
            const hours = seconds / 3600;
            const title = document.getElementById('stats-day-title');
            if (title) title.textContent = Math.abs(hours - 24) <= 1.5 ? 'Last 24 hours'
                : hours >= 1 ? 'Last ' + Math.round(hours) + ' hours'
                : 'Last ' + Math.max(1, Math.round(seconds / 60)) + ' minutes';
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
        if (source) source.textContent = 'Recorded totals: SourceForge ' + formatCount(stats.total.sourceforge) + ' · GitHub ' + formatCount(stats.total.github) + '.';
        const table = document.getElementById('stats-years');
        if (table) {
            table.replaceChildren();
            for (const year of stats.years) {
                const row = document.createElement('tr');
                [year.year, formatCount(year.sourceforge), year.github === null ? 'Not tracked' : formatCount(year.github), year.total === null ? 'Incomplete' : formatCount(year.total)].forEach((value, index) => {
                    const cell = document.createElement(index === 0 ? 'th' : 'td');
                    if (index === 0) cell.scope = 'row';
                    cell.textContent = value;
                    row.append(cell);
                });
                table.append(row);
            }
        }
        renderMonthlyDownloads(stats.months);
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


function renderMonthlyDownloads(months) {
    const chart = document.getElementById('stats-monthly-chart');
    if (!chart) return;
    chart.replaceChildren();
    if (!Array.isArray(months) || months.length !== 12) {
        chart.textContent = 'Monthly download history is being collected.';
        return;
    }
    const maximum = Math.max(1, ...months.map(month => month.total));
    for (const month of months) {
        const date = new Date(month.month + '-01T00:00:00Z');
        const label = date.toLocaleDateString('en', {month: 'short', year: '2-digit', timeZone: 'UTC'});
        const column = document.createElement('div');
        column.className = 'monthly-column';
        const value = document.createElement('span');
        value.className = 'monthly-count';
        value.textContent = formatCount(month.total);
        const track = document.createElement('div');
        track.className = 'monthly-track';
        const bar = document.createElement('div');
        bar.className = 'monthly-bar' + (month.partial_month ? ' monthly-current' : '');
        bar.style.height = (month.total / maximum * 100) + '%';
        track.append(bar);
        const caption = document.createElement('span');
        caption.className = 'monthly-label';
        caption.textContent = label + (month.partial_month ? '*' : '');
        column.title = label + ': SourceForge ' + formatCount(month.sourceforge)
            + (month.github === null ? '; GitHub history unavailable' : '; GitHub ' + formatCount(month.github));
        column.append(value, track, caption);
        chart.append(column);
    }
}
