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
const SOURCEFORGE_STATS_API = 'https://sourceforge.net/projects/dunelegacy/files/stats/json?start_date=2009-01-01&end_date=2030-12-31';

// Download count display (progressive enhancement)
document.addEventListener('DOMContentLoaded', () => {
    const githubData = fetchLiveDownloadCounts()
        .catch(() => fetch('data/downloads.json')
            .then(r => r.ok ? r.json() : Promise.reject(r.status)))
        .catch(() => null);

    const sfData = fetchSourceForgeStats().catch(() => null);

    Promise.all([githubData, sfData])
        .then(([gh, sf]) => renderDownloadCounts(gh, sf));
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

function fetchSourceForgeStats() {
    return fetch(SOURCEFORGE_STATS_API)
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(data => {
            // SourceForge returns { oses: { "Windows": N, "Linux": N, "Mac": N, ... }, total: N }
            const oses = data.oses || {};
            const total = data.total || 0;

            // Calculate avg downloads/month from start_date to now
            const startDate = new Date('2009-01-01');
            const months = Math.max(1, (Date.now() - startDate.getTime()) / (1000 * 60 * 60 * 24 * 30.44));
            const avgPerMonth = Math.round(total / months);

            return {
                windows: oses['Windows'] || 0,
                macos: oses['Mac'] || 0,
                linux: (oses['Linux'] || 0) + (oses['BSD'] || 0),
                total,
                avgPerMonth
            };
        });
}

function renderDownloadCounts(ghData, sfData) {
    // GitHub per-platform counts (Dune City — all releases)
    const gh = { windows: 0, macos: 0, linux: 0, total: 0 };
    if (ghData && ghData.releases) {
        for (const release of ghData.releases) {
            for (const asset of release.assets) {
                const name = asset.name.toLowerCase();
                if (name.includes('windows')) gh.windows += asset.download_count;
                else if (name.includes('macos')) gh.macos += asset.download_count;
                else if (name.includes('linux')) gh.linux += asset.download_count;
            }
        }
        gh.total = ghData.all_releases_total || 0;
    }

    // SourceForge counts (Dune Legacy)
    const sf = sfData || { windows: 0, macos: 0, linux: 0, total: 0, avgPerMonth: 0 };

    // Per-card counts show Dune City (GitHub) downloads only
    document.querySelectorAll('.download-card[data-platform]').forEach(card => {
        const p = card.dataset.platform;
        let count = 0;
        if (p === 'windows') count = gh.windows;
        else if (p === 'macos') count = gh.macos;
        else if (p === 'linux') count = gh.linux;

        const el = card.querySelector('.download-count');
        if (el) {
            el.textContent = formatCount(count) + ' downloads';
        }
    });

    // Dune City total
    document.querySelectorAll('.download-dunecity-total').forEach(el => {
        el.textContent = formatCount(gh.total) + ' total Dune City downloads';
    });

    // Dune Legacy total (SourceForge) with avg/month
    if (sf.total > 0) {
        const avgText = sf.avgPerMonth > 0
            ? ' (' + formatCount(sf.avgPerMonth) + ' avg/month)'
            : '';
        document.querySelectorAll('.download-legacy-total').forEach(el => {
            el.textContent = formatCount(sf.total) + ' total Dune Legacy downloads' + avgText;
        });
    }

    // Latest release count badges
    if (ghData && ghData.latest_release) {
        document.querySelectorAll('.download-latest-count').forEach(el => {
            el.textContent = formatCount(ghData.latest_release.total_downloads) + ' downloads this release';
        });
    }
}

function formatCount(n) {
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return n.toLocaleString();
}
