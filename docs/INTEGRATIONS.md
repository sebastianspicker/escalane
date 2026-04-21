# Integration Notes

## Yealink trigger provisioning

Typical parameters:
- `alarm.number` (audible PBX fallback)
- `action_url.alarm.trigger` (broker webhook)

Example:

```ini
alarm.number = 8800
action_url.alarm.trigger = https://alarm.example.org/v1/yealink/alarm?token=YLK_T54W_3F9A
alarm.button.ring.enable = 0
```

Recommendations:
- generate long random `device_token` per phone,
- keep token mapping in `devices.device_token`.

## Zammad API template

Create ticket:

```bash
curl -sS -X POST "https://zammad.example.org/api/v1/tickets" \
  -H "Authorization: Bearer ${ZAMMAD_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "DURESS ALARM - Person X - Room 1.23",
    "group": "Emergency Desk",
    "priority_id": 3,
    "state_id": 1,
    "customer_id": "guess:alarm-system@example.org",
    "tags": ["duress", "silent"],
    "article": {
      "subject": "Alarm triggered (silent)",
      "body": "...",
      "type": "note",
      "internal": true
    }
  }'
```

Add internal note (ACK update):

```bash
curl -sS -X PUT "https://zammad.example.org/api/v1/tickets/${TICKET_ID}" \
  -H "Authorization: Bearer ${ZAMMAD_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "article": {
      "subject": "Alarm acknowledged",
      "body": "ACK details...",
      "type": "note",
      "internal": true
    }
  }'
```

## SMS and Signal connectors

- SMS connector is generic HTTP POST based (`SENDXMS_*` settings).
- Signal connector expects a signal-cli-rest-api compatible endpoint.

Both are optional and can remain disabled until explicitly configured.
