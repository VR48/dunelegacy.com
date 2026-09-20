import {createSignalingServer} from './server.js';

export function createService(options = {}) {
  const service = createSignalingServer(options);
  service.httpServer.removeAllListeners('request');
  service.httpServer.on('request', (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200);
      res.end(JSON.stringify({status: 'ok', ...service.stats()}));
    } else {
      res.writeHead(404); res.end('{"error":"not found"}');
    }
  });
  return service;
}

