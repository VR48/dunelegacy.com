<?php
declare(strict_types=1);

/** Explicit public activity records, separate from anonymous lifecycle diagnostics. */
final class PublicActivity
{
    public function __construct(private readonly Store $store, private readonly bool $enabled) {}

    public function record(string $kind, array $fields): void
    {
        if (!$this->enabled || !function_exists('dunecityP2PRecordPublicActivity')) return;
        if (!in_array($kind, ['chat_message','public_game_created','public_game_joined','public_game_started'], true)) return;
        // Only explicit public display data crosses this hook. Never forward a room/session object.
        $event=[
            'event_id'=>Store::randomHex(16), 'kind'=>$kind,
            'occurred_at'=>(int)floor($this->store->now()/1000),
            'room_id'=>(string)($fields['room_id'] ?? ''),
            'player_name'=>(string)($fields['player_name'] ?? ''),
            'message'=>$kind==='chat_message' ? (string)($fields['message'] ?? '') : '',
            'channel_id'=>(string)($fields['channel_id'] ?? ''),
            'mod_name'=>(string)($fields['mod_name'] ?? ''),
            'mode'=>(string)($fields['mode'] ?? ''),
            'game_version'=>(string)($fields['game_version'] ?? ''),
            'participant_id'=>(int)($fields['participant_id'] ?? 0),
            'role'=>(string)($fields['role'] ?? ''),
            'start_id'=>(string)($fields['start_id'] ?? ''), 'players'=>[],
        ];
        if ($kind==='public_game_started') {
            foreach (array_slice($fields['players'] ?? [],0,Limits::MAX_PEERS_PER_ROOM) as $player)
                $event['players'][]=['id'=>(int)$player['id'], 'name'=>(string)$player['name'],
                    'role'=>(string)$player['role'], 'runtime'=>(string)$player['runtime']];
        }
        if ($kind!=='chat_message') {
            $identity=$kind==='public_game_started' ? $event['start_id'] : (string)$event['participant_id'];
            $event['event_id']=substr(hash('sha256',$kind.'|'.$event['room_id'].'|'.$identity),0,32);
        }
        try { dunecityP2PRecordPublicActivity($event); }
        catch (Throwable) { error_log('Public activity storage unavailable'); }
    }
}
