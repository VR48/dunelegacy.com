import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import WebSocket from 'ws';
import {createService} from '../service.mjs';

async function setup(t) {
  const s=createService();
  s.httpServer.listen(0,'127.0.0.1'); await once(s.httpServer,'listening');
  t.after(()=>s.close());
  const address=`127.0.0.1:${s.httpServer.address().port}`;
  return {s,address};
}
async function connect(address) {
  const ws=new WebSocket(`ws://${address}`,{origin:`http://${address}`});
  await once(ws,'open');return ws;
}
function message(ws) {return once(ws,'message').then(([data])=>JSON.parse(data));}
test('health, FIFO pairing, signal forwarding and departure cleanup',async t=>{
  const {s,address}=await setup(t);
  assert.equal((await (await fetch(`http://${address}/health`)).json()).status,'ok');
  const a=await connect(address),b=await connect(address);
  let received=message(a);a.send('{"t":"find"}');assert.equal((await received).t,'waiting');
  const first=message(a),second=message(b);b.send('{"t":"find"}');
  assert.equal((await first).role,'host');assert.equal((await second).role,'joiner');
  assert.equal(s.stats().pairs,1);
  received=message(b);a.send('{"t":"sig","data":{"probe":"736"}}');
  assert.deepEqual(await received,{t:'sig',data:{probe:'736'}});
  received=message(b);a.close();assert.equal((await received).t,'peer_left');
  assert.equal(s.stats().pairs,0);b.close();
});
test('cancel removes a waiting player from the queue',async t=>{
  const {s,address}=await setup(t);const a=await connect(address);
  let received=message(a);a.send('{"t":"find"}');await received;
  a.send('{"t":"cancel"}');
  // A following find/response establishes processing of the cancel on this socket.
  received=message(a);a.send('{"t":"find"}');assert.equal((await received).t,'waiting');
  assert.equal(s.stats().waiting,1);a.close();
});
test('unrelated browser origins cannot join',async t=>{
  const {address}=await setup(t);
  const ws=new WebSocket(`ws://${address}`,{origin:'https://unrelated.invalid'});
  const [error]=await once(ws,'error');assert.match(error.message,/403/);
});
