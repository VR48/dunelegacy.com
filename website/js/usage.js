'use strict';
let usageReport;
let usagePeriod = 'day';
const count = n => Number(n || 0).toLocaleString();
const pct = (n, d) => d ? (100 * n / d).toFixed(1) + '%' : '—';
const names = {browser:'Browser', native:'Desktop', unknown:'Unknown', campaign:'Campaign', single_custom:'Custom single-player', skirmish:'Skirmish', multiplayer:'Network multiplayer', vanilla:'Vanilla', dunecity:'Dune City', same_house:'AI sharing the human’s house', ally:'AI ally in another house', opponent:'AI opponent'};
const label = value => names[value] || value;
function element(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
}
function duration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const n = Math.round(seconds);
    return n >= 3600 ? Math.floor(n/3600)+'h '+Math.floor(n%3600/60)+'m' : n >= 60 ? Math.floor(n/60)+'m '+n%60+'s' : n+'s';
}
function table(target, headers, rows, caption) {
    const host = typeof target === 'string' ? document.getElementById(target) : target;
    host.replaceChildren();
    if (!rows.length) { host.append(element('p', 'No sessions recorded in this period.', 'usage-note')); return; }
    const wrap = element('div', undefined, 'usage-table-wrap');
    const t = element('table', undefined, 'usage-table');
    if (caption) t.setAttribute('aria-label', caption);
    const head = element('thead'); const tr = element('tr');
    headers.forEach(h => { const cell=element('th',h); cell.scope='col'; tr.append(cell); }); head.append(tr); t.append(head);
    const body=element('tbody');
    rows.forEach(row=> { const r=element('tr'); row.forEach((value,i)=> { const c=element(i===0?'th':'td',value); if(i===0)c.scope='row'; r.append(c); }); body.append(r); });
    t.append(body);wrap.append(t);host.append(wrap);
}
function comparison(target, rows, title, transform=label, sort='total') {
    const total = r => r.total === undefined ? r.browser+r.native+r.unknown : r.total;
    const sorted = [...rows].sort((a,b)=>(sort==='total'?total(b)-total(a):b[sort]-a[sort]) || a.label.localeCompare(b.label,undefined,{numeric:true}));
    table(target,[title,'Browser','Desktop','Unknown','Total'],sorted.map(r=>[transform(r.label),count(r.browser),count(r.native),count(r.unknown),count(total(r))]),title+' by platform');
    const headers=document.getElementById(target).querySelectorAll('thead th');
    ['browser','native','unknown','total'].forEach((key,i)=>{
        if(!headers[i+1])return;
        const text=headers[i+1].textContent;
        const button=element('button',text+(sort===key?' ↓':''));button.type='button';
        button.setAttribute('aria-label','Rank '+title.toLowerCase()+' by '+text.toLowerCase()+' sessions');
        button.addEventListener('click',()=>comparison(target,rows,title,transform,key));
        headers[i+1].replaceChildren(button);
        headers[i+1].setAttribute('aria-sort',sort===key?'descending':'none');
    });
}
function chart(target, rows, month) {
    const host=document.getElementById(target); host.replaceChildren();
    if(!rows.length) {host.append(element('p','No activity recorded.','usage-note'));return;}
    const bars=element('div',undefined,'usage-bars'); bars.setAttribute('aria-hidden','true');
    const max=Math.max(1,...rows.map(r=>r.total));
    rows.forEach(r=>{
        const column=element('div',undefined,'usage-column'); column.append(element('span',count(r.total),'count'));
        const track=element('div',undefined,'usage-bar-track'); const stack=element('div',undefined,'usage-bar-stack');
        stack.style.height=(r.total/max*100)+'%';
        ['browser','native','unknown'].forEach(rt=>{const segment=element('span',undefined,'bar-'+rt);segment.style.height=(r.total?r[rt]/r.total*100:0)+'%';stack.append(segment);});
        track.append(stack);column.append(track);
        const date=new Date(r.label+(month?'-01':'')+'T00:00:00Z');
        column.append(element('span',date.toLocaleDateString('en',{month:'short',...(month?{year:'2-digit'}:{day:'numeric'}),timeZone:'UTC'}),'usage-bar-label'));
        column.title=r.label+': '+count(r.total)+' sessions; browser '+count(r.browser)+', desktop '+count(r.native)+', unknown '+count(r.unknown);
        bars.append(column);
    }); host.append(bars);
    const details=element('details');details.append(element('summary','View exact '+(month?'monthly':'daily')+' counts'));
    const tab=element('div');comparisonNode(tab,rows,month?'Month (UTC)':'Day (UTC)');details.append(tab);host.append(details);
}
function comparisonNode(host,rows,title){table(host,[title,'Browser','Desktop','Unknown','Total'],rows.map(r=>[r.label,count(r.browser),count(r.native),count(r.unknown),count(r.total)]));}
function renderUsage() {
    if(!usageReport)return;
    const p=usageReport.periods[usagePeriod];
    const date=s=>new Date(s).toLocaleString('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'})+' UTC';
    document.getElementById('period-range').textContent=date(p.since)+' → '+date(usageReport.generated);
    document.querySelectorAll('[data-period]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.period===usagePeriod)));
    const runtimes=Object.fromEntries(p.runtime.map(r=>[r.label,r.total]));
    const cards=document.getElementById('usage-cards');cards.replaceChildren();
    const known=(runtimes.browser||0)+(runtimes.native||0);
    [['Recorded sessions',p.total,'All platforms'],['Browser',runtimes.browser,pct(runtimes.browser||0,known)+' of identified sessions'],['Desktop',runtimes.native,pct(runtimes.native||0,known)+' of identified sessions'],['Platform unknown',runtimes.unknown,'Excluded from browser/desktop shares']].forEach(([title,n,note])=>{
        const card=element('article',undefined,'usage-card');card.append(element('h3',title),element('strong',count(n)),element('p',note));cards.append(card);
    });
    chart('daily-chart',p.daily.slice(-30),false); chart('monthly-chart',p.monthly.slice(-12),true);
    document.getElementById('daily-note').textContent='Recorded sessions within this view, grouped by day'+(p.daily.length>30?' · latest 30 days shown.':'.');
    document.getElementById('monthly-note').textContent='Recorded sessions within this view, grouped by month'+(p.monthly.length>12?' · latest 12 months shown.':'.');
    comparison('modes-table',p.modes,'Mode');comparison('mods-table',p.mods,'Mod');
    const online=p.modes.find(r=>r.label==='multiplayer')?.total||0;
    const offline=p.modes.filter(r=>['campaign','single_custom','skirmish'].includes(r.label)).reduce((n,r)=>n+r.total,0);
    document.getElementById('online-summary').textContent=count(offline)+' single-player sessions · '+count(online)+' network multiplayer sessions.';
    document.getElementById('platform-note').textContent=usageReport.platform_since?'Browser/desktop identification available from '+date(usageReport.platform_since)+'. Older or unmarked clients remain unknown.':'';
    comparison('levels-table',p.levels,'Level',x=>'Level '+x);comparison('maps-table',p.maps.slice(0,15),'Map');
    comparison('opponents-table',p.bots.ai.filter(r=>r.relation==='opponent'),'Opponent');
    comparison('assist-table',p.bots.assistance.filter(r=>r.relation!=='opponent'),'Assistance');
    comparison('difficulty-table',p.bots.difficulty,'Difficulty',x=>x[0].toUpperCase()+x.slice(1));
    comparison('teammates-table',p.bots.ai.filter(r=>r.relation!=='opponent').map(r=>({...r,label:r.label+' · '+label(r.relation)})),'Teammate');
    table('outcomes-table',['Platform','Starts','Finished','Human win','Human loss','Early exit','No end report'],p.outcomes.map(r=>[label(r.runtime),count(r.sessions),count(r.finished),count(r.human_wins),count(r.human_losses),count(r.exited),count(r.missing_end)]));
    table('lengths-table',['Platform / mode','Starts','Measured','Average','Median','Longest','Longest result'],p.lengths.filter(r=>r.sessions).map(r=>[label(r.runtime)+' · '+r.label,count(r.sessions),count(r.measured),duration(r.average_seconds),duration(r.median_seconds),duration(r.longest_seconds),r.longest_outcome||'—']));
    table('quick-table',['Platform','Level-1 starts','Measured','Exits ≤60s','Share of starts','No end report'],p.lengths.filter(r=>r.label==='Campaign level 1').map(r=>[label(r.runtime),count(r.sessions),count(r.measured),count(r.quick_exits),pct(r.quick_exits,r.sessions),count(r.missing_end)]));
    document.getElementById('coverage-note').textContent=usageReport.tracking_since?'Tracking began '+date(usageReport.tracking_since)+'. These are games received by the metaserver, not a census of every game played. No player names or individual session records are published.':'';
    document.getElementById('usage-content').hidden=false;
}
async function refreshUsage() {
    const status=document.getElementById('usage-status');
    try {
        const response=await fetch('metaserver/usage.php',{cache:'no-store'});
        if(!response.ok)throw new Error('unavailable');
        const report=await response.json(); if(report.schema!==1||!report.periods)throw new Error('invalid');
        usageReport=report;renderUsage();
        const stale=Date.now()-new Date(report.generated).getTime()>15*60*1000;
        status.classList.toggle('stale',stale);
        status.textContent=(stale?'Update delayed · ':'Refreshes every five minutes · ')+'last updated '+new Date(report.generated).toLocaleTimeString();
    } catch(error) {
        status.classList.add('stale');status.textContent=usageReport?'Update delayed. Showing the last available statistics.':'Statistics are temporarily unavailable. Please try again shortly.';
    }
}
document.querySelectorAll('[data-period]').forEach(button=>button.addEventListener('click',()=>{usagePeriod=button.dataset.period;renderUsage();}));
refreshUsage();setInterval(refreshUsage,5*60*1000);
