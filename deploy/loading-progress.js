'use strict';

// Fetch streams decoded bytes, but Content-Length describes the Brotli bytes.
// Emscripten supplies the original size through this preload hook; returning
// undefined leaves its normal downloader, error handling and startup intact.
(function () {
    const script = document.currentScript;
    const engineURL = script && script.dataset.engineUrl;
    const engineHash = script && script.dataset.engineSha256;
    if (engineURL && engineHash && !Module.instantiateWasm && !Module.wasmBinary) {
        const originURL = Module.locateFile('dunecity.wasm', new URL('.', script.src).href);
        const verifiedBytes = async function (url, timeout) {
            const controller = new AbortController();
            const timer = timeout ? setTimeout(() => controller.abort(), timeout) : null;
            try {
                const response = await fetch(url, { credentials: 'omit', signal: controller.signal });
                if (!response.ok) throw new Error('Engine download returned HTTP ' + response.status);
                const bytes = await response.arrayBuffer();
                const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
                    .map(byte => byte.toString(16).padStart(2, '0')).join('');
                if (hash !== engineHash) throw new Error('Engine download checksum mismatch');
                return bytes;
            } finally {
                if (timer !== null) clearTimeout(timer);
            }
        };
        Module.instantiateWasm = function (imports, ready) {
            // GitHub already publishes this exact public file. Pin the revision,
            // verify its bytes, and keep the origin as an automatic fallback.
            verifiedBytes(engineURL, 8000)
                .catch(() => verifiedBytes(originURL, 0))
                .then(bytes => WebAssembly.instantiate(bytes, imports))
                .then(result => ready(result.instance, result.module))
                .catch(error => Module.onAbort('Could not prepare the game engine: ' + error.message));
            return {};
        };
    }
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
