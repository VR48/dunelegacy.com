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

const DUNECITY_RELEASES_API = 'https://api.github.com/repos/svan058/dunecity/releases?per_page=100';

// Download count display (progressive enhancement)
document.addEventListener('DOMContentLoaded', () => {
    fetchLiveDownloadCounts()
        .then(renderDownloadCounts)
        .catch(() => fetch('data/downloads.json')
            .then(r => r.ok ? r.json() : Promise.reject(r.status))
            .then(renderDownloadCounts))
        .catch(() => {
            // Silent fail — counts are a nice-to-have
        });
});

function fetchLiveDownloadCounts() {
    return fetch(DUNECITY_RELEASES_API, {
        headers: {
            'Accept': 'application/vnd.github+json'
        }
    })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(releases => {
            if (!Array.isArray(releases) || releases.length === 0) {
                return Promise.reject('no releases');
            }

            const normalized = releases.map(release => {
                const assets = (release.assets || []).map(asset => ({
                    name: asset.name,
                    download_count: asset.download_count || 0,
                    size: asset.size || 0
                }));

                return {
                    tag: release.tag_name,
                    name: release.name,
                    published_at: release.published_at,
                    total_downloads: assets.reduce((sum, asset) => sum + asset.download_count, 0),
                    assets
                };
            });

            return {
                generated: new Date().toISOString(),
                repository: 'svan058/dunecity',
                latest_release: normalized[0],
                all_releases_total: normalized.reduce((sum, release) => sum + release.total_downloads, 0),
                releases: normalized
            };
        });
}

function renderDownloadCounts(data) {
    if (!data || !data.latest_release) return;

    const latest = data.latest_release;

    // Build a lookup: platform keyword -> count
    // Assets follow the pattern DuneCity-X.X.X-Platform-arch.ext
    const platformCounts = {};
    for (const asset of latest.assets) {
        const name = asset.name.toLowerCase();
        let platform = null;
        if (name.includes('windows')) platform = 'windows';
        else if (name.includes('macos')) platform = 'macos';
        else if (name.includes('linux') && name.endsWith('.deb')) platform = 'linux-deb';
        else if (name.includes('linux') && name.endsWith('.rpm')) platform = 'linux-rpm';
        else if (name.includes('linux') && name.endsWith('.tar.gz')) platform = 'linux-tar';
        if (platform) platformCounts[platform] = asset.download_count;
    }

    // Sum Linux variants
    const linuxTotal = (platformCounts['linux-deb'] || 0)
        + (platformCounts['linux-rpm'] || 0)
        + (platformCounts['linux-tar'] || 0);

    // Populate per-card counts via data-platform attributes
    document.querySelectorAll('.download-card[data-platform]').forEach(card => {
        const p = card.dataset.platform;
        let count = 0;
        if (p === 'windows') count = platformCounts['windows'] || 0;
        else if (p === 'macos') count = platformCounts['macos'] || 0;
        else if (p === 'linux') count = linuxTotal;

        const el = card.querySelector('.download-count');
        if (el) {
            el.textContent = formatCount(count) + ' downloads';
        }
    });

    // Populate total count badges
    document.querySelectorAll('.download-total-count').forEach(el => {
        el.textContent = formatCount(data.all_releases_total) + ' total downloads';
    });

    // Populate latest release count badges
    document.querySelectorAll('.download-latest-count').forEach(el => {
        el.textContent = formatCount(latest.total_downloads) + ' downloads this release';
    });
}

function formatCount(n) {
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return n.toLocaleString();
}
