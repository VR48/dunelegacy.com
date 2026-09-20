import {createService} from './service.mjs';
const service = createService();
service.httpServer.listen(8788, '127.0.0.1', () => console.log('Matchmaking listening on loopback:8788'));
const stop = () => service.close().then(() => process.exit(0));
process.once('SIGTERM', stop);
process.once('SIGINT', stop);
