// Zabbix · Tipo de medio "Ops Central" (Webhook)
// Parámetros del tipo de medio:
//   url        https://ops-central.empresa.local/api/zabbix/webhook
//   secreto    {$OPSCENTRAL_SECRETO}   (macro secreta global; el mismo valor que zabbix/secreto_webhook)
//   eventid    {EVENT.ID}                 (en la recuperación sigue siendo el id del problema)
//   event_value {EVENT.VALUE}            (1 problema, 0 recuperación)
//   host       {HOST.HOST}
//   name       {EVENT.NAME}
//   severity   {EVENT.NSEVERITY}
//   clock      {EVENT.TIMESTAMP}
//   tags       {EVENT.TAGSJSON}
//   maintenance {EVENT.SUPPRESSED}
// Zabbix sigue siendo quien notifica a las personas; este medio solo informa a Ops Central.

var p = JSON.parse(value);
var cuerpo = JSON.stringify({
  eventid: p.eventid,
  event_value: p.event_value,
  host: p.host,
  name: p.name,
  severity: parseInt(p.severity, 10),
  clock: parseInt(p.clock, 10),
  tags: (function () { try { return JSON.parse(p.tags); } catch (e) { return []; } })(),
  maintenance: p.maintenance
});
var tiempo = String(Math.floor(Date.now() / 1000));
var firma = 'sha256=' + hmac('sha256', p.secreto, tiempo + '.' + cuerpo);

var req = new HttpRequest();
req.addHeader('Content-Type: application/json');
req.addHeader('X-OpsCentral-Tiempo: ' + tiempo);
req.addHeader('X-OpsCentral-Firma: ' + firma);
var resp = req.post(p.url, cuerpo);
if (req.getStatus() !== 202) {
  throw 'Ops Central respondió ' + req.getStatus() + ': ' + resp;
}
return 'OK';
