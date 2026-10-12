import asyncio,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock,patch
from market import MarketEngine
import vmi_ib as v
XML='<Report><CoID Type="Ticker">META</CoID><Ratios><Ratio FieldName="TTMROEPCT">25</Ratio><Ratio FieldName="TTMROIPCT">99</Ratio></Ratios></Report>'
class IBScannerTests(unittest.IsolatedAsyncioTestCase):
 def fixture(self):
  e=MarketEngine();e._ib=NS();e.info.update(connected=True,generation=3)
  c=NS(symbol='META',secType='STK',currency='USD',conId=123);e._stock=AsyncMock(return_value=c)
  e._snapshot_quote=AsyncMock(return_value=dict(brokerConid=123,last=100,status='LIVE',at=int(time.time()*1000)))
  return e,c
 async def test_owned_snapshot_and_report(self):
  e,c=self.fixture()
  with patch.object(v,'report',new=AsyncMock(return_value=XML)):
   r=await e._vmi_observation('META');self.assertEqual(r['fields']['ROE'],'25.0');self.assertNotIn('ROIC',r['fields']);self.assertTrue(v.valid_quote(r['quote'],123))
  e._snapshot_quote.assert_awaited_once_with(c)
 async def test_existing_quote_preserves_stream(self):
  e,c=self.fixture();ticker=object();e._active={123:dict(contract=c,ticker=ticker)};e._quote=Mock(return_value=e._snapshot_quote.return_value)
  with patch.object(v,'report',new=AsyncMock(return_value=XML)):await e._vmi_observation('META')
  e._snapshot_quote.assert_not_awaited();self.assertIs(e._active[123]['ticker'],ticker)
 async def test_no_entitlement_retains_quote_and_cools_down(self):
  e,c=self.fixture()
  with patch.object(v,'report',new=AsyncMock(side_effect=ValueError('No entitlement'))) as report:
   first=await e._vmi_observation('META');second=await e._vmi_observation('META')
   self.assertTrue(v.valid_quote(first['quote'],123));self.assertEqual(report.await_count,1);self.assertIn('cooldown',second['attempts'][-1]['error'])
 async def test_generation_change_rejected(self):
  e,c=self.fixture()
  async def change(*args):e.info['generation']+=1;return c
  e._stock=change
  with self.assertRaises(ConnectionError):await e._vmi_observation('META')
  e._snapshot_quote.assert_not_awaited()
 async def test_timeout_only_cancels_owned_report_id(self):
  future=asyncio.get_running_loop().create_future();client=NS(getReqId=lambda:77,reqFundamentalData=Mock(),cancelFundamentalData=Mock());wrapper=NS(startReq=Mock(return_value=future),_endReq=Mock());e=NS(_ib=NS(client=client,wrapper=wrapper))
  with self.assertRaises(asyncio.TimeoutError):await v.report(e,object(),timeout=.001)
  client.cancelFundamentalData.assert_called_once_with(77);wrapper._endReq.assert_called_once_with(77)
 def test_competing_session_does_not_submit_scanner_work(self):
  e,c=self.fixture();e.info['problem']='10197: No market data during competing live session';e.call=Mock()
  self.assertIn('10197',v.available(e,'META')['reason']);e.call.assert_not_called()
 def test_outer_timeout_circuit_and_generation_reset(self):
  e,c=self.fixture();e.call=Mock(side_effect=TimeoutError('fixture'))
  self.assertFalse(v.available(e,'META')['available']);self.assertFalse(v.available(e,'AAPL')['available'])
  self.assertEqual(e.call.call_count,1);self.assertFalse(e._vmi_scan_http_busy)
  e.info['generation']+=1;v.available(e,'AAPL');self.assertEqual(e.call.call_count,2)
 def test_busy_http_slot_does_not_queue(self):
  e,c=self.fixture();e._vmi_scan_http_busy=True;e.call=Mock()
  self.assertFalse(v.available(e,'META')['available']);e.call.assert_not_called()
 async def test_deadline_cancels_work_and_unlocks(self):
  e,c=self.fixture();cancelled=asyncio.Event()
  async def slow(*args):
   try: await asyncio.sleep(10)
   finally: cancelled.set()
  e._stock=slow
  with patch.object(v,'OBSERVATION_BUDGET',.01):
   r=await v.observe(e,'META')
  self.assertFalse(r['available']);self.assertTrue(cancelled.is_set());self.assertFalse(e._vmi_scanner_lock.locked())
  e._snapshot_quote.assert_not_awaited()
 async def test_busy_owner_slot_and_10197_never_acquire(self):
  e,c=self.fixture();e._vmi_scanner_lock=asyncio.Lock()
  async with e._vmi_scanner_lock: self.assertFalse((await v.observe(e,'META'))['available'])
  e.info['problem']='10197';self.assertFalse((await v.observe(e,'META'))['available']);e._stock.assert_not_awaited()
 def test_offline_does_not_connect(self):
  e=MarketEngine();e.call=Mock();self.assertFalse(v.available(e,'META')['available']);e.call.assert_not_called()
 def test_unsafe_xml_and_wrong_identity(self):
  for xml in [XML.replace('META','AAPL'),'<!DOCTYPE x>'+XML]:
   with self.assertRaises(ValueError):v.parse_report(xml,'META')
 def test_delayed_stale_wrong_contract_prices_not_promoted(self):
  q=dict(brokerConid=123,last=100,status='DELAYED',at=int(time.time()*1000));self.assertFalse(v.valid_quote(q,123));q['status']='LIVE';q['at']=0;self.assertFalse(v.valid_quote(q,123));q['at']=int(time.time()*1000);self.assertFalse(v.valid_quote(q,456))

if __name__=='__main__':unittest.main()
