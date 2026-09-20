import {createSignalingServer} from './server.js';
import {pathToFileURL} from 'node:url';

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

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const service = createService();
  service.httpServer.listen(8788, '127.0.0.1', () => console.log('Matchmaking listening on loopback:8788'));
  const stop = () => service.close().then(() => process.exit(0));
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
}
