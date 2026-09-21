'use strict';

// Fetch streams decoded bytes, but Content-Length describes the Brotli bytes.
// Emscripten supplies the original size through this preload hook; returning
// undefined leaves its normal downloader, error handling and startup intact.
(function () {
    const sizes = new Map();
    const preload = Module.getPreloadedPackage;
    const setStatus = Module.setStatus;
    Module.getPreloadedPackage = function (name, size) {
        if (Number.isFinite(size) && size > 0) sizes.set(name, size);
        return preload && preload.apply(this, arguments);
    };
    Module.setStatus = function (text) {
        const match = /^Downloading data.*\((\d+)\/\d+\)/.exec(text || '');
        const total = Array.from(sizes.values()).reduce((a, b) => a + b, 0);
        if (match && total) {
            const loaded = Number(match[1]);
            if (loaded >= total) {
                setStatus.call(this, 'Game data downloaded. Preparing the game engine...');
                document.getElementById('progress').removeAttribute('value');
                return;
            }
            text = `Downloading data... (${loaded}/${total})`;
        }
        return setStatus.call(this, text);
    };
}());
