<?php
/** Trusted local hook only: no new HTTP ingestion endpoint. */
require_once __DIR__ . '/analytics.php';
const PUBLIC_ACTIVITY_FIELDS=['event_id','kind','occurred_at','room_id','player_name','message',
    'channel_id','mod_name','mode','game_version','participant_id','role','start_id','players'];

function publicActivityText($value, $max, $empty=false) {
    return is_string($value) && strlen($value)<=$max && ($empty || trim($value)!=='')
        && preg_match('//u',$value) && !preg_match('/\p{Cc}|\p{Cf}|\p{Zl}|\p{Zp}/u',$value);
}
function publicActivityNormalize($event) {
    if (!is_array($event) || array_diff(array_keys($event),PUBLIC_ACTIVITY_FIELDS)
        || array_diff(PUBLIC_ACTIVITY_FIELDS,array_keys($event))) return null;
    if (!is_string($event['event_id']) || !preg_match('/^[a-f0-9]{32}$/D',$event['event_id'])
        || !in_array($event['kind'],['chat_message','public_game_created','public_game_joined','public_game_started'],true)
        || !is_int($event['occurred_at']) || $event['occurred_at']<0 || $event['occurred_at']>4102444800
        || !publicActivityText($event['player_name'],64)
        || !publicActivityText($event['message'],120,true) || !publicActivityText($event['mod_name'],64,true)
        || !is_string($event['game_version']) || !preg_match('/^[A-Za-z0-9._-]{0,64}$/D',$event['game_version'])
        || !is_int($event['participant_id']) || $event['participant_id']<0 || $event['participant_id']>65535
        || !is_array($event['players']) || !array_is_list($event['players']) || count($event['players'])>12) return null;
    $chat=$event['kind']==='chat_message';
    $start=$event['kind']==='public_game_started';
    if ($chat) {
        if ($event['room_id']!=='' || !is_string($event['channel_id']) || !preg_match('/^[a-f0-9]{64}$/D',$event['channel_id'])
            || !publicActivityText($event['message'],120) || $event['participant_id']!==0 || $event['role']!==''
            || $event['mode']!=='' || $event['mod_name']!=='' || $event['game_version']!=='' || $event['start_id']!=='' || $event['players']!==[]) return null;
    } else {
        if (!is_string($event['room_id']) || !preg_match('/^[A-Za-z0-9_-]{22,64}$/D',$event['room_id'])
            || $event['channel_id']!=='' || $event['message']!=='' || !in_array($event['mode'],['custom','coop'],true)) return null;
        if ($start) {
            if (!is_string($event['start_id']) || !preg_match('/^[a-f0-9]{32}$/D',$event['start_id'])
                || !$event['players'] || $event['participant_id']!==0 || $event['role']!=='') return null;
        } elseif ($event['participant_id']<1 || $event['players']!==[]
            || $event['role']!==($event['kind']==='public_game_created' ? 'host' : 'client')
            || $event['start_id']!=='') return null;
    }
    $ids=[]; $hosts=0;
    foreach ($event['players'] as $p) {
        if (!is_array($p) || count($p)!==4 || !isset($p['id'],$p['name'],$p['role'],$p['runtime'])
            || !is_int($p['id']) || $p['id']<1 || $p['id']>65535 || isset($ids[$p['id']])
            || !publicActivityText($p['name'],64) || !in_array($p['role'],['host','client'],true)
            || !in_array($p['runtime'],['browser','native'],true)) return null;
        $ids[$p['id']]=true; if ($p['role']==='host') { ++$hosts; if ($p['name']!==$event['player_name']) return null; }
    }
    if ($start && $hosts!==1) return null;
    $event['players']=array_map(static fn($p)=>['id'=>$p['id'],'name'=>$p['name'],
        'role'=>$p['role'],'runtime'=>$p['runtime']],$event['players']);
    $out=[]; foreach (PUBLIC_ACTIVITY_FIELDS as $key) $out[$key]=$event[$key];
    return $out;
}
function publicActivityRecord($event) {
    $event=publicActivityNormalize($event);
    if ($event===null) return false;
    $db=analyticsDatabase();
    if ($db===null) return analyticsPythonRequest('public_activity_record',['event'=>$event])!==null;
    try {
        $db->exec(file_get_contents(__DIR__.'/public_activity.sql'));
        $json=json_encode($event,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
        $stmt=$db->prepare('INSERT INTO analytics_public_activity
            (event_id,kind,occurred_at,received_at,room_id,player_name,message,details_json)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING');
        $stmt->execute([$event['event_id'],$event['kind'],$event['occurred_at'],time(),$event['room_id'],
            $event['player_name'],$event['message'],$json]);
        if (!$stmt->rowCount()) {
            $read=$db->prepare('SELECT details_json FROM analytics_public_activity WHERE event_id=?');
            $read->execute([$event['event_id']]);
            return json_decode($read->fetchColumn(),true)===$event;
        }
        return true;
    } catch (Throwable) { error_log('Public activity storage unavailable'); return false; }
}
